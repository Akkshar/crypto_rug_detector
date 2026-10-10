"""Multicall3 batching and block pinning on the real Web3EvmClient, driven by a stub ``w3``
that decodes aggregate3 and routes each sub-call to a FakeEvmClient."""

from __future__ import annotations

from datetime import datetime, timezone

from eth_abi import decode, encode
from eth_utils import to_checksum_address

from src.ingestion.evm import abi
from src.ingestion.evm.abi import MULTICALL3, decode_aggregate3, encode_aggregate3
from src.ingestion.evm.client import CallReverted, FakeEvmClient, Web3EvmClient

TOKEN = to_checksum_address("0x" + "aa" * 20)
OTHER = to_checksum_address("0x" + "bb" * 20)


class StubEth:
    """Minimal ``w3.eth`` that answers eth_call by delegating to a FakeEvmClient."""

    def __init__(self, fake: FakeEvmClient, *, multicall_deployed: bool = True):
        self.fake = fake
        self.multicall_deployed = multicall_deployed
        self.block_number = 123
        self.calls: list[tuple[dict, object]] = []

    def get_code(self, address, block_id="latest"):
        if address == MULTICALL3:
            return b"\x60\x80" if self.multicall_deployed else b""
        return self.fake.get_code(address)

    def call(self, tx, block_id="latest", state_override=None):
        self.calls.append((tx, block_id))
        if tx["to"] == MULTICALL3:
            (batch,) = decode(["(address,bool,bytes)[]"], tx["data"][4:])
            results = []
            for to, _allow_fail, data in batch:
                try:
                    results.append((True, self.fake.call(_as_call(to, data))))
                except CallReverted:
                    results.append((False, b""))
            return encode(["(bool,bytes)[]"], [results])
        return self.fake.call(_as_call(tx["to"], tx["data"]))

    def get_block(self, block_id):
        return {"timestamp": 1_700_000_000 + (block_id if isinstance(block_id, int) else 0)}


def _as_call(to: str, data: bytes) -> abi.Call:
    """Wrap raw calldata so the fake's exact-match table can look it up."""

    class RawCall(abi.Call):
        @property
        def data(self) -> bytes:  # type: ignore[override]
            return data

    return RawCall(to, "raw()", ())


class StubW3:
    def __init__(self, eth):
        self.eth = eth


def make(fake: FakeEvmClient, **kw) -> tuple[Web3EvmClient, StubEth]:
    eth = StubEth(fake, **kw)
    return Web3EvmClient(StubW3(eth), chain_id=1), eth


def test_encode_decode_roundtrip():
    calls = [abi.erc20_decimals(TOKEN), abi.erc20_symbol(OTHER)]
    data = encode_aggregate3(calls)
    assert data[:4] == abi.selector(abi.AGGREGATE3_SIG)
    (batch,) = decode(["(address,bool,bytes)[]"], data[4:])
    assert [to_checksum_address(b[0]) for b in batch] == [TOKEN, OTHER] and all(b[1] for b in batch)
    assert decode_aggregate3(encode(["(bool,bytes)[]"], [[(True, b"\x01"), (False, b""), (True, b"")]])) == [
        b"\x01",
        None,
        None,
    ]


def test_call_many_uses_one_multicall_round_trip():
    fake = FakeEvmClient().returns(abi.erc20_decimals(TOKEN), 9).returns(abi.erc20_total_supply(TOKEN), 5)
    client, eth = make(fake)
    calls = [abi.erc20_decimals(TOKEN), abi.erc20_total_supply(TOKEN), abi.erc20_symbol(TOKEN)]  # third reverts
    out = client.call_many(calls)
    assert abi.erc20_decimals(TOKEN).one(out[0]) == 9 and abi.erc20_total_supply(TOKEN).one(out[1]) == 5
    assert out[2] is None
    multicalls = [c for c in eth.calls if c[0]["to"] == MULTICALL3]
    assert len(multicalls) == 1 and len(eth.calls) == 1


def test_call_many_falls_back_when_multicall_missing():
    fake = FakeEvmClient().returns(abi.erc20_decimals(TOKEN), 9)
    client, eth = make(fake, multicall_deployed=False)
    out = client.call_many([abi.erc20_decimals(TOKEN), abi.erc20_symbol(TOKEN)])
    assert abi.erc20_decimals(TOKEN).one(out[0]) == 9 and out[1] is None
    assert all(c[0]["to"] != MULTICALL3 for c in eth.calls) and len(eth.calls) == 2


def test_call_many_chunks_large_batches():
    fake = FakeEvmClient()
    calls = [abi.erc20_balance_of(TOKEN, to_checksum_address(f"0x{i:040x}")) for i in range(1, 451)]
    for c in calls:
        fake.returns(c, 1)
    client, eth = make(fake)
    assert len(client.call_many(calls)) == 450
    assert len(eth.calls) == 3  # 200 + 200 + 50


def test_block_pinning_is_passed_to_every_read():
    fake = FakeEvmClient().returns(abi.erc20_decimals(TOKEN), 18)
    client, eth = make(fake)
    pinned = client.at(1_000)
    assert pinned.block == 1_000 and client.block is None
    pinned.call(abi.erc20_decimals(TOKEN))
    pinned.call_many([abi.erc20_decimals(TOKEN)])
    assert {blk for _, blk in eth.calls} == {1_000}
    assert pinned.block_number() == 1_000 and client.block_number() == 123
    assert pinned.block_timestamp() == datetime.fromtimestamp(1_700_001_000, tz=timezone.utc)
    assert client.block_timestamp(5) == datetime.fromtimestamp(1_700_000_005, tz=timezone.utc)


def test_fake_client_block_pinning_clamps_logs():
    from src.ingestion.evm.client import LogEntry

    fake = FakeEvmClient(latest_block=100)
    fake.logs = [LogEntry(TOKEN, (b"\x00" * 32,), b"", block_number=b, tx_hash="0x" + "00" * 32) for b in (10, 50, 90)]
    assert len(fake.get_logs(TOKEN, [None], 0, "latest")) == 3
    pinned = fake.at(50)
    assert [e.block_number for e in pinned.get_logs(TOKEN, [None], 0, "latest")] == [10, 50]
    assert pinned.block_number() == 50 and fake.block_number() == 100
    assert pinned.responses is fake.responses  # programmed state is shared, only the pin differs
