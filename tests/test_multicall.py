"""Multicall3 batching and block pinning on the real Web3EvmClient, driven by a stub ``w3``
that decodes aggregate3 and routes each sub-call to a FakeEvmClient."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
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


class RangeLimitedEth(StubEth):
    """Node that rejects eth_getLogs spans above `max_span` and rate-limits every 3rd call."""

    def __init__(self, fake: FakeEvmClient, max_span: int, rate_limit_every: int = 0):
        super().__init__(fake)
        self.max_span = max_span
        self.rate_limit_every = rate_limit_every
        self.log_calls: list[tuple[int, int]] = []

    def get_logs(self, params):
        self.log_calls.append((params["fromBlock"], params["toBlock"]))
        if self.rate_limit_every and len(self.log_calls) % self.rate_limit_every == 0:
            raise RuntimeError("429 Client Error: Too Many Requests")
        if params["toBlock"] - params["fromBlock"] + 1 > self.max_span:
            raise ValueError({"code": -32005, "message": "limit exceeded"})
        return [
            {"address": TOKEN, "topics": [b"\x00" * 32], "data": b"", "blockNumber": b, "transactionHash": b"\x01" * 32}
            for b in range(params["fromBlock"], params["toBlock"] + 1)
            if b % 1000 == 0
        ]


def test_get_logs_adapts_to_node_range_limit_and_rate_limits():
    eth = RangeLimitedEth(FakeEvmClient(), max_span=5_000, rate_limit_every=4)
    client = Web3EvmClient(StubW3(eth), chain_id=1)
    client.backoff_s = 0.0
    entries = client.get_logs(TOKEN, [None], 0, 19_999)
    assert [e.block_number for e in entries] == list(range(0, 20_000, 1000))  # nothing lost across splits/retries
    assert client.log_span_hint is not None and client.log_span_hint <= 5_000
    assert all(hi - lo + 1 <= 5_000 for lo, hi in eth.log_calls if hi - lo + 1 <= client.log_span_hint)
    before = len(eth.log_calls)
    client.get_logs(TOKEN, [None], 20_000, 29_999)  # the learned span is used proactively – no new rejections
    new_calls = eth.log_calls[before:]
    assert all(hi - lo + 1 <= client.log_span_hint for lo, hi in new_calls)


def test_depth_limited_node_is_learned_and_window_skips_refused_history():
    """publicnode-style: any query reaching deeper than `horizon` blocks is refused even at tiny spans."""
    from src.ingestion.evm.client import LogRangeUnavailable

    class DepthLimitedEth(StubEth):
        horizon = 10_000

        def get_logs(self, params):
            if params["fromBlock"] < self.block_number - self.horizon:
                raise RuntimeError("403 Client Error: Forbidden for url: https://node/")
            return [
                {
                    "address": TOKEN,
                    "topics": [b"\x00" * 32],
                    "data": b"",
                    "blockNumber": b,
                    "transactionHash": b"\x01" * 32,
                }
                for b in range(params["fromBlock"], params["toBlock"] + 1)
                if b % 500 == 0
            ]

    eth = DepthLimitedEth(FakeEvmClient())
    eth.block_number = 100_000
    client = Web3EvmClient(StubW3(eth), chain_id=1)
    client.log_span_hint = client.confirmed_span = 5_000  # a span the node has accepted before
    client.backoff_s = 0.0

    with pytest.raises(LogRangeUnavailable):
        client.get_logs(TOKEN, [None], 70_000, 74_999)
    assert client.min_served_block is not None and client.min_served_block <= 90_001

    entries, served_from = client.get_logs_window(TOKEN, [None], 60_000, 100_000, chunk=5_000)
    assert served_from is not None and served_from >= 90_000
    assert (
        entries and min(e.block_number for e in entries) >= 90_000 and max(e.block_number for e in entries) == 100_000
    )
    # the learned boundary short-circuits later deep queries without touching the node
    calls_before = len(eth.calls)
    with pytest.raises(LogRangeUnavailable):
        client.get_logs(TOKEN, [None], 10_000, 10_100)
    assert len(eth.calls) == calls_before


def test_window_recovers_the_served_tail_of_a_straddling_chunk():
    """A 50k chunk that starts before the node's horizon must yield its served tail, not be dropped whole."""

    class DepthLimitedEth(StubEth):
        horizon = 10_000

        def get_logs(self, params):
            if params["fromBlock"] < self.block_number - self.horizon:
                raise RuntimeError("403 Client Error: Forbidden for url: https://node/")
            return [
                {
                    "address": TOKEN,
                    "topics": [b"\x00" * 32],
                    "data": b"",
                    "blockNumber": b,
                    "transactionHash": b"\x01" * 32,
                }
                for b in range(params["fromBlock"], params["toBlock"] + 1)
                if b % 1_000 == 0
            ]

    eth = DepthLimitedEth(FakeEvmClient())
    eth.block_number = 200_000
    client = Web3EvmClient(StubW3(eth), chain_id=1)
    client.backoff_s = 0.0
    # one 50k chunk from 150_000 to 200_000 straddles the horizon at 190_000, with no prior knowledge
    entries, served_from = client.get_logs_window(TOKEN, [None], 150_000, 200_000, chunk=50_001)
    assert served_from is not None and 190_000 <= served_from <= 191_000
    blocks = sorted(e.block_number for e in entries)
    assert blocks and blocks[0] >= 190_000 and blocks[-1] == 200_000 and len(blocks) >= 9


def test_non_range_errors_propagate():
    class Broken(StubEth):
        def get_logs(self, params):
            raise RuntimeError("invalid argument 0: json: cannot unmarshal")

    client = Web3EvmClient(StubW3(Broken(FakeEvmClient())), chain_id=1)
    with pytest.raises(RuntimeError, match="unmarshal"):
        client.get_logs(TOKEN, [None], 0, 10)


def test_fake_client_block_pinning_clamps_logs():
    from src.ingestion.evm.client import LogEntry

    fake = FakeEvmClient(latest_block=100)
    fake.logs = [LogEntry(TOKEN, (b"\x00" * 32,), b"", block_number=b, tx_hash="0x" + "00" * 32) for b in (10, 50, 90)]
    assert len(fake.get_logs(TOKEN, [None], 0, "latest")) == 3
    pinned = fake.at(50)
    assert [e.block_number for e in pinned.get_logs(TOKEN, [None], 0, "latest")] == [10, 50]
    assert pinned.block_number() == 50 and fake.block_number() == 100
    assert pinned.responses is fake.responses  # programmed state is shared, only the pin differs


# --------------------------------------------------------------------------- #
# JSON-RPC batched eth_getTransactionByHash
# --------------------------------------------------------------------------- #


class BatchingEth(StubEth):
    """``w3.eth`` whose ``get_transaction`` is recorded, plus a ``batch_requests`` on the parent w3."""

    def __init__(self, fake: FakeEvmClient, *, fail_batches: int = 0, missing: set[str] | None = None):
        super().__init__(fake)
        self.single_calls: list[str] = []
        self.batch_calls: list[list[str]] = []
        self.fail_batches = fail_batches
        self.missing = missing or set()
        self._batching = False

    def _raw(self, tx_hash: str) -> dict:
        tx = self.fake.transactions[tx_hash]
        return {"from": tx.sender, "to": tx.to, "value": tx.value, "blockNumber": tx.block_number, "input": b""}

    def get_transaction(self, tx_hash: str):
        if self._batching:
            return ("eth_getTransactionByHash", tx_hash)  # web3 returns request info while batching
        self.single_calls.append(tx_hash)
        if tx_hash in self.missing:
            raise RuntimeError("transaction not found")
        return self._raw(tx_hash)


class Batch:
    def __init__(self, eth: BatchingEth):
        self.eth = eth
        self.items: list[str] = []

    def __enter__(self):
        self.eth._batching = True
        return self

    def __exit__(self, *exc):
        self.eth._batching = False

    def add(self, payload):
        self.items.append(payload[1])

    def execute(self):
        self.eth.batch_calls.append(list(self.items))
        if self.eth.fail_batches > 0:
            self.eth.fail_batches -= 1
            raise RuntimeError("batch request not supported")
        if any(h in self.eth.missing for h in self.items):
            raise RuntimeError("transaction not found")  # web3 raises for the whole batch
        return [self.eth._raw(h) for h in self.items]


class BatchingW3(StubW3):
    def batch_requests(self):
        return Batch(self.eth)


def _txs(fake: FakeEvmClient, n: int) -> list[str]:
    from src.ingestion.evm.client import TxInfo

    hashes = []
    for i in range(1, n + 1):
        h = "0x" + f"{i:064x}"
        fake.transactions[h] = TxInfo(h, to_checksum_address(f"0x{i:040x}"), OTHER, 0, 100 + i)
        hashes.append(h)
    return hashes


def test_get_transactions_uses_one_batch_per_chunk():
    fake = FakeEvmClient()
    eth = BatchingEth(fake)
    client = Web3EvmClient(BatchingW3(eth), chain_id=1)
    client.TX_BATCH = 4
    hashes = _txs(fake, 10)

    out = client.get_transactions(hashes + hashes[:2])  # duplicates collapse
    assert [tx.sender for tx in out.values() if tx] == [fake.transactions[h].sender for h in hashes]
    assert [len(b) for b in eth.batch_calls] == [4, 4, 2]
    assert eth.single_calls == []


def test_get_transactions_falls_back_per_hash_when_a_batch_fails():
    fake = FakeEvmClient()
    hashes = _txs(fake, 3)
    eth = BatchingEth(fake, missing={hashes[1]})
    client = Web3EvmClient(BatchingW3(eth), chain_id=1)

    out = client.get_transactions(hashes)
    assert out[hashes[0]] is not None and out[hashes[1]] is None and out[hashes[2]] is not None
    assert len(eth.batch_calls) == 1 and eth.single_calls == hashes
    assert client._batch_ok, "one pruned transaction must not disable batching"


def test_get_transactions_disables_batching_after_repeated_failures():
    fake = FakeEvmClient()
    hashes = _txs(fake, 6)
    eth = BatchingEth(fake, fail_batches=5)
    client = Web3EvmClient(BatchingW3(eth), chain_id=1)
    client.TX_BATCH = 2

    out = client.get_transactions(hashes)
    assert all(out[h] is not None for h in hashes)
    assert len(eth.batch_calls) == client.TX_BATCH_FAILURES  # stopped trying after the second failure
    assert eth.single_calls == hashes and not client._batch_ok
