"""The narrow EVM surface the live provider needs, behind an interface that can
be faked in tests.

``Web3EvmClient`` is the real thing (web3.py, blocking, called via
``asyncio.to_thread``). ``FakeEvmClient`` replays programmed responses keyed by
``(to, calldata)`` and records every call so tests can assert on exactly what
was asked of the chain.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from eth_utils import to_checksum_address

from src.ingestion.evm.abi import AGGREGATE3_SIG, MULTICALL3, Call, decode_aggregate3, encode_aggregate3

log = logging.getLogger("rugscan.evm")


class CallReverted(RuntimeError):
    def __init__(self, call: Call, reason: str | None = None):
        self.call = call
        self.reason = reason
        super().__init__(f"{call.signature} on {call.to} reverted: {reason or 'no reason'}")


class LogRangeUnavailable(RuntimeError):
    """The node refuses to serve logs for this block range even at the minimum span – typically because
    public endpoints only keep the last N blocks of log history (publicnode BSC: ~10k blocks)."""

    def __init__(self, lo: int, hi: int, reason: str):
        self.lo, self.hi, self.reason = lo, hi, reason
        super().__init__(f"logs for blocks {lo}-{hi} not served: {reason}")


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
    #: When set, every read is pinned to this block (time-travel snapshots).
    block: int | None

    def call(self, call: Call, *, state_override: StateOverride | None = None, sender: str | None = None) -> bytes: ...

    def try_call(self, call: Call) -> bytes | None: ...

    def call_many(self, calls: list[Call]) -> list[bytes | None]: ...

    def get_code(self, address: str) -> bytes: ...

    def block_number(self) -> int: ...

    def block_timestamp(self, block: int | None = None) -> datetime | None: ...

    def at(self, block: int | None) -> EvmClient: ...

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]: ...

    def get_logs_window(
        self,
        address: str,
        topics: list[bytes | None],
        from_block: int,
        to_block: int,
        *,
        chunk: int,
        max_chunks: int | None = None,
    ) -> tuple[list[LogEntry], int | None]:
        """Chunked fetch that skips ranges the node refuses; returns (entries, first block actually served)."""
        ...

    def get_transaction(self, tx_hash: str) -> TxInfo | None: ...

    def estimate_gas(
        self, to: str, data: bytes, *, sender: str, value: int = 0, state_override: StateOverride | None = None
    ) -> int: ...


# --------------------------------------------------------------------------- #


class Web3EvmClient:
    MULTICALL_CHUNK = 200

    def __init__(
        self,
        w3: Any,
        chain_id: int | None = None,
        *,
        block: int | None = None,
        multicall: str | None = MULTICALL3,
    ):
        self.w3 = w3
        self.chain_id = chain_id if chain_id is not None else int(w3.eth.chain_id)
        self.block = block
        self.multicall = multicall
        self._multicall_ok: bool | None = None
        #: Largest block span the node has accepted for eth_getLogs so far (learned on the first rejection).
        self.log_span_hint: int | None = None
        #: Largest span that has actually succeeded – the proof that a refusal is about depth, not size.
        self.confirmed_span: int | None = None
        #: Oldest block the node is known to serve logs for (learned when a confirmed span is still refused).
        self.min_served_block: int | None = None
        self.retries = 3
        self.backoff_s = 0.5

    @property
    def _block_id(self) -> int | str:
        return self.block if self.block is not None else "latest"

    def at(self, block: int | None) -> Web3EvmClient:
        clone = Web3EvmClient(self.w3, self.chain_id, block=block, multicall=self.multicall)
        clone._multicall_ok = self._multicall_ok
        clone.log_span_hint = self.log_span_hint
        clone.confirmed_span = self.confirmed_span
        clone.min_served_block = self.min_served_block
        return clone

    # -- transport resilience ------------------------------------------- #
    def _with_retry(self, fn: Any, what: str) -> Any:
        """Retry transient transport failures (rate limits, timeouts, 5xx) with exponential backoff."""
        delay = self.backoff_s
        for attempt in range(self.retries + 1):
            try:
                return fn()
            except Exception as exc:
                if attempt >= self.retries or not is_transient(exc):
                    raise
                log.info(
                    "%s: transient RPC error (%s); retry %d/%d in %.1fs",
                    what,
                    _short(exc),
                    attempt + 1,
                    self.retries,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def call(self, call: Call, *, state_override: StateOverride | None = None, sender: str | None = None) -> bytes:
        tx: dict[str, Any] = {"to": to_checksum_address(call.to), "data": call.data}
        if sender:
            tx["from"] = to_checksum_address(sender)
        if call.value:
            tx["value"] = call.value
        try:
            if state_override:
                return bytes(
                    self._with_retry(lambda: self.w3.eth.call(tx, self._block_id, state_override), call.signature)
                )
            return bytes(self._with_retry(lambda: self.w3.eth.call(tx, self._block_id), call.signature))
        except Exception as exc:
            raise CallReverted(call, str(exc)) from exc

    def try_call(self, call: Call) -> bytes | None:
        try:
            raw = self.call(call)
        except CallReverted:
            return None
        return raw or None

    def call_many(self, calls: list[Call]) -> list[bytes | None]:
        """One Multicall3 round-trip per 200 calls; falls back to sequential calls when unavailable."""
        if not calls:
            return []
        if self._multicall_ok is None:
            self._multicall_ok = bool(self.multicall) and bool(self.get_code(self.multicall or ""))
            if not self._multicall_ok:
                log.info("Multicall3 not available on chain %s – using sequential eth_call", self.chain_id)
        if not self._multicall_ok:
            return [self.try_call(c) for c in calls]
        out: list[bytes | None] = []
        for start in range(0, len(calls), self.MULTICALL_CHUNK):
            chunk = calls[start : start + self.MULTICALL_CHUNK]
            batch = Call(self.multicall or MULTICALL3, AGGREGATE3_SIG)
            try:
                data = encode_aggregate3(chunk)
                raw = bytes(
                    self._with_retry(
                        lambda to=batch.to, d=data: self.w3.eth.call({"to": to, "data": d}, self._block_id), "multicall"
                    )
                )
                out.extend(decode_aggregate3(raw))
            except Exception as exc:  # noqa: BLE001 - degrade to sequential rather than fail the scan
                log.warning("multicall batch failed (%s); retrying sequentially", exc)
                out.extend(self.try_call(c) for c in chunk)
        return out

    def get_code(self, address: str) -> bytes:
        return bytes(
            self._with_retry(lambda: self.w3.eth.get_code(to_checksum_address(address), self._block_id), "getCode")
        )

    def block_number(self) -> int:
        return (
            self.block
            if self.block is not None
            else int(self._with_retry(lambda: self.w3.eth.block_number, "blockNumber"))
        )

    def block_timestamp(self, block: int | None = None) -> datetime | None:
        target = block if block is not None else self._block_id
        try:
            ts = int(self.w3.eth.get_block(target)["timestamp"])
        except Exception as exc:  # noqa: BLE001
            log.debug("block timestamp lookup failed: %s", exc)
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    LOG_MIN_SPAN = 200

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]:
        """eth_getLogs that adapts to the node's block-range limit.

        Public endpoints cap the span (BSC: "limit exceeded", others: "block range too large"). On such an
        error the span is halved recursively down to LOG_MIN_SPAN and the largest accepted span is remembered,
        so later calls split proactively instead of failing first.
        """
        hi = self.block_number() if to_block == "latest" else int(to_block)
        if self.block is not None and hi > self.block:
            hi = self.block
        if from_block > hi:
            return []
        return self._logs_adaptive(to_checksum_address(address), topics, from_block, hi)

    def _logs_adaptive(self, address: str, topics: list[bytes | None], lo: int, hi: int) -> list[LogEntry]:
        span = hi - lo + 1
        if self.min_served_block is not None and lo < self.min_served_block:
            raise LogRangeUnavailable(lo, hi, f"node serves logs from block {self.min_served_block} only")
        if self.log_span_hint is not None and span > self.log_span_hint:
            out: list[LogEntry] = []
            for a, b in chunked_ranges(lo, hi, self.log_span_hint):
                out.extend(self._logs_adaptive(address, topics, a, b))
            return out
        try:
            got = self._fetch_logs(address, topics, lo, hi)
        except Exception as exc:
            if not is_range_limit(exc):
                raise
            # Only a span the node has *actually accepted* before proves that splitting cannot help; the
            # tentative hint set on rejection is not proof. Then the refusal is about depth – the node does
            # not keep history this old – and we remember the boundary.
            if span <= self.LOG_MIN_SPAN or (self.confirmed_span is not None and span <= self.confirmed_span):
                self.min_served_block = max(self.min_served_block or 0, hi + 1)
                log.info("eth_getLogs refused for blocks %d-%d (%s) – node history starts later", lo, hi, _short(exc))
                raise LogRangeUnavailable(lo, hi, _short(exc)) from exc
            half = max(self.LOG_MIN_SPAN, span // 2)
            self.log_span_hint = half if self.log_span_hint is None else min(self.log_span_hint, half)
            log.info("eth_getLogs span %d rejected by node (%s); splitting to %d", span, _short(exc), half)
            mid = lo + half - 1
            return self._logs_adaptive(address, topics, lo, mid) + self._logs_adaptive(address, topics, mid + 1, hi)
        self.confirmed_span = max(self.confirmed_span or 0, span)
        return got

    def get_logs_window(
        self,
        address: str,
        topics: list[bytes | None],
        from_block: int,
        to_block: int,
        *,
        chunk: int,
        max_chunks: int | None = None,
    ) -> tuple[list[LogEntry], int | None]:
        return _window(self, address, topics, from_block, to_block, chunk=chunk, max_chunks=max_chunks)

    def _fetch_logs(self, address: str, topics: list[bytes | None], lo: int, hi: int) -> list[LogEntry]:
        params = {
            "address": address,
            "topics": [("0x" + t.hex()) if isinstance(t, bytes) else t for t in topics],
            "fromBlock": lo,
            "toBlock": hi,
        }
        raw = self._with_retry(lambda: self.w3.eth.get_logs(params), f"getLogs[{lo}-{hi}]")
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
    block: int | None = None
    min_served_block: int | None = None  # emulate a public node that only keeps recent log history
    timestamps: dict[int, datetime] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    batches: list[int] = field(default_factory=list)  # sizes of each call_many batch, for assertions

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

    def call_many(self, calls: list[Call]) -> list[bytes | None]:
        self.batches.append(len(calls))
        return [self.try_call(c) for c in calls]

    def get_code(self, address: str) -> bytes:
        return self.code.get(to_checksum_address(address), b"")

    def block_number(self) -> int:
        return self.block if self.block is not None else self.latest_block

    def block_timestamp(self, block: int | None = None) -> datetime | None:
        target = block if block is not None else self.block_number()
        return self.timestamps.get(target)

    def at(self, block: int | None) -> FakeEvmClient:
        return replace(self, block=block)  # shares the programmed responses/logs, pins reads

    def get_logs(
        self, address: str, topics: list[bytes | None], from_block: int, to_block: int | str
    ) -> list[LogEntry]:
        hi = min(self.block_number(), self.latest_block if to_block == "latest" else int(to_block))
        if self.min_served_block is not None and from_block < self.min_served_block:
            raise LogRangeUnavailable(from_block, hi, f"fake node serves logs from block {self.min_served_block} only")
        addr = to_checksum_address(address)
        out = []
        for entry in self.logs:
            if entry.address != addr or not (from_block <= entry.block_number <= hi):
                continue
            if any(t is not None and (i >= len(entry.topics) or entry.topics[i] != t) for i, t in enumerate(topics)):
                continue
            out.append(entry)
        return out

    def get_logs_window(
        self,
        address: str,
        topics: list[bytes | None],
        from_block: int,
        to_block: int,
        *,
        chunk: int,
        max_chunks: int | None = None,
    ) -> tuple[list[LogEntry], int | None]:
        return _window(self, address, topics, from_block, to_block, chunk=chunk, max_chunks=max_chunks)

    def get_transaction(self, tx_hash: str) -> TxInfo | None:
        return self.transactions.get(tx_hash)

    def estimate_gas(
        self, to: str, data: bytes, *, sender: str, value: int = 0, state_override: StateOverride | None = None
    ) -> int:
        return self.gas.get((to_checksum_address(to), data.hex()), 150_000)


# Node messages for "your eth_getLogs span/result is too big" (BSC: -32005 "limit exceeded"; geth/erigon:
# "query returned more than N results"; Alchemy/Infura: "block range" / "response size exceeded").
_RANGE_LIMIT_MARKERS = (
    "limit exceeded",
    "block range",
    "range too large",
    "too many results",
    "query returned more than",
    "-32005",
    "response size",
    "max results",
    "exceeds the limit",
    "400 client error",  # some gateways (drpc) answer an oversized getLogs with a bare HTTP 400
    "403 client error",  # publicnode answers one with 403 – only ever consulted on the getLogs path
)
# Transport-level hiccups worth a retry. Checked *first*: "429 Too Many Requests" must never be mistaken
# for a range limit (it would be split forever instead of retried).
_TRANSIENT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "timed out",
    "timeout",
    "502",
    "503",
    "504",
    "connection",
)


def _short(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:120]


def is_transient(exc: BaseException) -> bool:
    msg = _short(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def is_range_limit(exc: BaseException) -> bool:
    msg = _short(exc).lower()
    return not is_transient(exc) and any(m in msg for m in _RANGE_LIMIT_MARKERS)


def _window(
    client: Any,
    address: str,
    topics: list[bytes | None],
    from_block: int,
    to_block: int,
    *,
    chunk: int,
    max_chunks: int | None,
) -> tuple[list[LogEntry], int | None]:
    """Shared chunked-window loop: skip ranges the node refuses, report where served history starts."""
    entries: list[LogEntry] = []
    first_served: int | None = None
    skipped = 0
    for i, (lo, hi) in enumerate(chunked_ranges(from_block, to_block, chunk)):
        if max_chunks is not None and i >= max_chunks:
            log.warning("log window for %s truncated after %d chunks", address, max_chunks)
            break
        try:
            got = client.get_logs(address, topics, lo, hi)
        except LogRangeUnavailable:
            skipped += 1
            continue
        if first_served is None:
            first_served = lo
        entries.extend(got)
    if skipped:
        log.info(
            "%s: %d chunk(s) of log history not served by the node; window starts at %s", address, skipped, first_served
        )
    return entries, first_served


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
