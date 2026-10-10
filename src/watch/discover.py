"""New-pair firehose: find tokens as they list and scan them immediately.

Every v2 factory emits ``PairCreated(token0, token1, pair, n)`` and every v3
factory ``PoolCreated(token0, token1, fee, tickSpacing, pool)``. Scanning
those logs over a block range yields every token that just got a pool against
a known quote asset. The ``Firehose`` scores each one and alerts on anything
at or above the configured tier, writing everything to the cache so the
corpus grows by itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from eth_utils import keccak, to_checksum_address

from config.chains import ChainConfig, DexKind
from src.ingestion.base import BaseDataProvider
from src.ingestion.evm.client import EvmClient, address_from_topic, chunked_ranges
from src.models.risk_report import Chain, RiskReport, RiskTier, Severity
from src.models.scoring_engine import ScoringEngine
from src.storage.snapshot_cache import SnapshotCache
from src.watch.monitor import TIER_ORDER, Notifier, WatchEvent, _tier_severity

log = logging.getLogger("rugscan.discover")

PAIR_CREATED_TOPIC = keccak(text="PairCreated(address,address,address,uint256)")
POOL_CREATED_TOPIC = keccak(text="PoolCreated(address,address,uint24,int24,address)")


@dataclass(frozen=True)
class NewPair:
    chain: Chain
    dex: str
    pair: str
    token: str
    quote_symbol: str
    block: int
    tx_hash: str


class NewPairScanner:
    def __init__(self, client: EvmClient, chain: Chain, cfg: ChainConfig, *, log_chunk: int = 5_000):
        self.client = client
        self.chain = Chain(chain)
        self.cfg = cfg
        self.log_chunk = log_chunk
        self._quote_by_addr = {addr: sym for sym, addr in cfg.quote_tokens.items()}

    def scan(self, from_block: int, to_block: int) -> list[NewPair]:
        found: list[NewPair] = []
        for dex_name, dex in self.cfg.dexes.items():
            topic = PAIR_CREATED_TOPIC if dex.kind is DexKind.V2 else POOL_CREATED_TOPIC
            for lo, hi in chunked_ranges(from_block, to_block, self.log_chunk):
                try:
                    entries = self.client.get_logs(dex.factory, [topic], lo, hi)
                except Exception as exc:  # noqa: BLE001 - one factory's RPC hiccup must not stop the scan
                    log.warning("%s PairCreated scan %d-%d failed: %s", dex_name, lo, hi, exc)
                    continue
                for entry in entries:
                    if len(entry.topics) < 3:
                        continue
                    token0, token1 = address_from_topic(entry.topics[1]), address_from_topic(entry.topics[2])
                    pair = to_checksum_address("0x" + entry.data[12:32].hex()) if len(entry.data) >= 32 else None
                    if dex.kind is DexKind.V3 and len(entry.data) >= 64:
                        pair = to_checksum_address("0x" + entry.data[44:64].hex())  # (tickSpacing, pool)
                    if pair is None:
                        continue
                    q0, q1 = self._quote_by_addr.get(token0), self._quote_by_addr.get(token1)
                    if (q0 is None) == (q1 is None):
                        continue  # both or neither side is a quote asset → not a new-token listing we price
                    token, quote = (token1, q0) if q0 else (token0, q1)
                    found.append(
                        NewPair(self.chain, dex_name, pair, token, quote or "", entry.block_number, entry.tx_hash)
                    )
        found.sort(key=lambda p: (p.block, p.tx_hash))
        return found


class Firehose:
    def __init__(
        self,
        provider: BaseDataProvider,
        engine: ScoringEngine,
        scanner: NewPairScanner,
        notifiers: list[Notifier],
        *,
        cache: SnapshotCache | None = None,
        min_tier: RiskTier = RiskTier.HIGH,
        max_tokens_per_round: int = 50,
    ):
        self.provider = provider
        self.engine = engine
        self.scanner = scanner
        self.notifiers = notifiers
        self.cache = cache
        self.min_tier = min_tier
        self.max_tokens_per_round = max_tokens_per_round
        self.seen: set[str] = set()
        self.reports: list[RiskReport] = []

    def _emit(self, event: WatchEvent) -> None:
        for n in self.notifiers:
            try:
                n.notify(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("notifier %s failed: %s", type(n).__name__, exc)

    async def scan_round(self, from_block: int, to_block: int) -> list[RiskReport]:
        pairs = [p for p in self.scanner.scan(from_block, to_block) if p.token not in self.seen]
        if len(pairs) > self.max_tokens_per_round:
            log.warning(
                "%d new pairs in %d-%d; scanning the first %d",
                len(pairs),
                from_block,
                to_block,
                self.max_tokens_per_round,
            )
            pairs = pairs[: self.max_tokens_per_round]
        out: list[RiskReport] = []
        for pair in pairs:
            self.seen.add(pair.token)
            try:
                snapshot = await self.provider.fetch_snapshot(pair.chain, pair.token)
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    WatchEvent(
                        "error",
                        pair.chain,
                        pair.token,
                        f"{pair.dex} listing at block {pair.block}: fetch failed: {exc}",
                        Severity.LOW,
                    )
                )
                continue
            report = self.engine.score(snapshot, provider=self.provider.name)
            if self.cache is not None:
                self.cache.put_snapshot(snapshot, self.provider.name)
                self.cache.put_report(report)
            out.append(report)
            if TIER_ORDER.index(report.risk_tier) >= TIER_ORDER.index(self.min_tier):
                top = ", ".join(f.code for f in sorted(report.flags, key=lambda f: -f.points)[:4])
                self._emit(
                    WatchEvent(
                        "new_token",
                        pair.chain,
                        report.address,
                        f"{report.token_symbol or '?'} listed on {pair.dex}/{pair.quote_symbol} at block {pair.block}: "
                        f"{report.risk_tier.value} {report.risk_score:.1f}/100 – {top or 'no flags'}",
                        _tier_severity(report.risk_tier),
                        report=report,
                    )
                )
        self.reports.extend(out)
        return out

    async def run(
        self,
        client: EvmClient,
        *,
        from_block: int | None = None,
        lookback: int = 2_000,
        follow: bool = False,
        poll_s: float = 15.0,
        max_rounds: int | None = None,
    ) -> int:
        """Scan from ``from_block`` (or ``head - lookback``) to the head; with ``follow`` keep polling."""
        head = client.block_number()
        cursor = from_block if from_block is not None else max(0, head - lookback)
        rounds = 0
        while True:
            head = client.block_number()
            if cursor <= head:
                log.info("scanning blocks %d-%d", cursor, head)
                await self.scan_round(cursor, head)
                cursor = head + 1
            rounds += 1
            if not follow or (max_rounds is not None and rounds >= max_rounds):
                return rounds
            await asyncio.sleep(poll_s)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
