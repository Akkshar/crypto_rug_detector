"""The narrow EVM surface the live provider needs, behind an interface that can
be faked in tests.

``Web3EvmClient`` is the real thing (web3.py, blocking, called via
``asyncio.to_thread``). ``FakeEvmClient`` replays programmed responses keyed by
``(to, calldata)`` and records every call so tests can assert on exactly what
was asked of the chain.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from eth_utils import to_checksum_address

from src.ingestion.evm.abi import Call

log = logging.getLogger("rugscan.evm")


class CallReverted(RuntimeError):
    def __init__(self, call: Call, reason: str | None = None):
        self.call = call
        self.reason = reason
        super().__init__(f"{call.signature} on {call.to} reverted: {reason or 'no reason'}")


@dataclass(frozen=True)
class LogEntry:
    address: str
    topics: tuple[bytes, ...]
    data: bytes
    block_number: int
    tx_hash: str
    log_index: int = 0


@dataclass(frozen=True)
class TxInfo:
    hash: str
    sender: str
    to: str | None
    value: int
    block_number: int
    input: bytes = b""


StateOverride = Mapping[str, Mapping[str, Any]]


class EvmClient(Protocol):
    chain_id: int

    def call(self, call: Call, *, state_override: StateOverride | None = None, sender: str | None = None) -> bytes: ...

    def try_call(self, call: Call) -> bytes | None: ...

    def get_code(self, address: str) -> bytes: ...

    def block_number(self) -> int: ...

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]: ...

    def get_transaction(self, tx_hash: str) -> TxInfo | None: ...

    def estimate_gas(
        self, to: str, data: bytes, *, sender: str, value: int = 0, state_override: StateOverride | None = None
    ) -> int: ...


# --------------------------------------------------------------------------- #


class Web3EvmClient:
    def __init__(self, w3: Any, chain_id: int | None = None):
        self.w3 = w3
        self.chain_id = chain_id if chain_id is not None else int(w3.eth.chain_id)

    def call(self, call: Call, *, state_override: StateOverride | None = None, sender: str | None = None) -> bytes:
        tx: dict[str, Any] = {"to": to_checksum_address(call.to), "data": call.data}
        if sender:
            tx["from"] = to_checksum_address(sender)
        if call.value:
            tx["value"] = call.value
        try:
            if state_override:
                return bytes(self.w3.eth.call(tx, "latest", state_override))
            return bytes(self.w3.eth.call(tx))
        except Exception as exc:
            raise CallReverted(call, str(exc)) from exc

    def try_call(self, call: Call) -> bytes | None:
        try:
            raw = self.call(call)
        except CallReverted:
            return None
        return raw or None

    def get_code(self, address: str) -> bytes:
        return bytes(self.w3.eth.get_code(to_checksum_address(address)))

    def block_number(self) -> int:
        return int(self.w3.eth.block_number)

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]:
        raw = self.w3.eth.get_logs(
            {
                "address": to_checksum_address(address),
                "topics": [("0x" + t.hex()) if isinstance(t, bytes) else t for t in topics],
                "fromBlock": from_block,
                "toBlock": to_block,
            }
        )
        return [
            LogEntry(
                address=to_checksum_address(entry["address"]),
                topics=tuple(bytes(t) for t in entry["topics"]),
                data=bytes(entry["data"]),
                block_number=int(entry["blockNumber"]),
                tx_hash=entry["transactionHash"].hex()
                if hasattr(entry["transactionHash"], "hex")
                else str(entry["transactionHash"]),
                log_index=int(entry.get("logIndex", 0)),
            )
            for entry in raw
        ]

    def get_transaction(self, tx_hash: str) -> TxInfo | None:
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
        except Exception:  # noqa: BLE001
            return None
        return TxInfo(
            hash=tx_hash,
            sender=to_checksum_address(tx["from"]),
            to=to_checksum_address(tx["to"]) if tx.get("to") else None,
            value=int(tx["value"]),
            block_number=int(tx["blockNumber"]),
            input=bytes(tx.get("input", b"")),
        )

    def estimate_gas(
        self, to: str, data: bytes, *, sender: str, value: int = 0, state_override: StateOverride | None = None
    ) -> int:
        tx = {"to": to_checksum_address(to), "data": data, "from": to_checksum_address(sender), "value": value}
        if state_override:
            return int(self.w3.eth.estimate_gas(tx, "latest", state_override))
        return int(self.w3.eth.estimate_gas(tx))


# --------------------------------------------------------------------------- #


@dataclass
class FakeEvmClient:
    """Programmable stand-in. Responses are keyed on ``(to, calldata_hex)``.

    ``responses[(to, data)]`` may be ``bytes`` (returned), an ``Exception``
    (raised as CallReverted) or a callable ``(state_override, sender) -> bytes``
    so tests can model override-dependent behaviour (sell simulation).

    When no exact response matches, ``handlers`` registered with
    ``on_selector`` are tried in order: ``fn(call, state_override, sender)``
    returns bytes or raises ``CallReverted``. That is how a fake router can
    answer swaps whose calldata varies with ``amountOutMin``.
    """

    chain_id: int = 1
    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    handlers: list[tuple[str | None, bytes, Any]] = field(default_factory=list)
    code: dict[str, bytes] = field(default_factory=dict)
    logs: list[LogEntry] = field(default_factory=list)
    transactions: dict[str, TxInfo] = field(default_factory=dict)
    gas: dict[tuple[str, str], int] = field(default_factory=dict)
    latest_block: int = 20_000_000
    calls: list[Call] = field(default_factory=list)

    # programming helpers ---------------------------------------------------
    def on(self, call: Call, response: Any) -> FakeEvmClient:
        self.responses[(to_checksum_address(call.to), call.data.hex())] = response
        return self

    def on_selector(self, signature: str, handler: Any, *, to: str | None = None) -> FakeEvmClient:
        from src.ingestion.evm.abi import selector

        self.handlers.append((to_checksum_address(to) if to else None, selector(signature), handler))
        return self

    def returns(self, call: Call, *values: Any) -> FakeEvmClient:
        from eth_abi import encode

        return self.on(call, encode(list(call.returns), list(values)))

    def reverts(self, call: Call, reason: str = "execution reverted") -> FakeEvmClient:
        return self.on(call, CallReverted(call, reason))

    def with_code(self, address: str, code: bytes = b"\x60\x80") -> FakeEvmClient:
        self.code[to_checksum_address(address)] = code
        return self

    # EvmClient protocol ----------------------------------------------------
    def call(self, call: Call, *, state_override: StateOverride | None = None, sender: str | None = None) -> bytes:
        self.calls.append(call)
        key = (to_checksum_address(call.to), call.data.hex())
        if key not in self.responses:
            data = call.data
            for to, sel, handler in self.handlers:
                if data[:4] == sel and (to is None or to == key[0]):
                    return bytes(handler(call, state_override, sender))
            raise CallReverted(call, "no fake response programmed")
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return bytes(response(state_override, sender))
        return bytes(response)

    def try_call(self, call: Call) -> bytes | None:
        try:
            raw = self.call(call)
        except CallReverted:
            return None
        return raw or None

    def get_code(self, address: str) -> bytes:
        return self.code.get(to_checksum_address(address), b"")

    def block_number(self) -> int:
        return self.latest_block

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]:
        hi = self.latest_block if to_block == "latest" else int(to_block)
        addr = to_checksum_address(address)
        out = []
        for entry in self.logs:
            if entry.address != addr or not (from_block <= entry.block_number <= hi):
                continue
            if any(t is not None and (i >= len(entry.topics) or entry.topics[i] != t) for i, t in enumerate(topics)):
                continue
            out.append(entry)
        return out

    def get_transaction(self, tx_hash: str) -> TxInfo | None:
        return self.transactions.get(tx_hash)

    def estimate_gas(
        self, to: str, data: bytes, *, sender: str, value: int = 0, state_override: StateOverride | None = None
    ) -> int:
        return self.gas.get((to_checksum_address(to), data.hex()), 150_000)


def topic_address(address: str) -> bytes:
    """Left-pad an address to a 32-byte log topic."""
    return bytes.fromhex(to_checksum_address(address)[2:]).rjust(32, b"\x00")


def address_from_topic(topic: bytes) -> str:
    return to_checksum_address("0x" + topic[-20:].hex())


def chunked_ranges(start: int, end: int, size: int) -> Iterable[tuple[int, int]]:
    lo = start
    while lo <= end:
        hi = min(lo + size - 1, end)
        yield lo, hi
        lo = hi + 1
