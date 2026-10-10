"""Continuous monitoring: re-score a token on an interval and alert on change.

Point-in-time scans miss the moment a rug *happens*. The watcher compares each
new snapshot/report against the previous one and emits events for:

* tier escalation or a score jump
* liquidity dropping by more than ``liquidity_drop_pct``
* LP tokens leaving lockers / burn (locked share falling)
* new CRITICAL flags (e.g. sells start reverting)
* errors fetching the token (RPC down, token self-destructed…)

Notifiers are pluggable; the webhook one speaks the Discord/Slack
``{"content": ...}`` dialect so no SDK is needed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from rich.console import Console

from src.ingestion.base import BaseDataProvider
from src.models.risk_report import Chain, RiskReport, RiskTier, Severity, TokenSnapshot
from src.models.scoring_engine import ScoringEngine
from src.storage.snapshot_cache import ReportDiff, SnapshotCache

log = logging.getLogger("rugscan.watch")

TIER_ORDER = [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL]


@dataclass(frozen=True)
class WatchEvent:
    kind: str  # initial | tier_escalation | score_jump | liquidity_drop | lp_unlocked | new_critical_flag | error
    chain: Chain
    address: str
    message: str
    severity: Severity
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    report: RiskReport | None = None
    diff: ReportDiff | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "chain": self.chain.value,
            "address": self.address,
            "severity": self.severity.value,
            "at": self.at.isoformat(),
            "message": self.message,
            "risk_score": self.report.risk_score if self.report else None,
            "risk_tier": self.report.risk_tier.value if self.report else None,
        }


class Notifier(Protocol):
    def notify(self, event: WatchEvent) -> None: ...


class ConsoleNotifier:
    STYLE = {
        Severity.INFO: "dim",
        Severity.LOW: "cyan",
        Severity.MEDIUM: "yellow",
        Severity.HIGH: "dark_orange",
        Severity.CRITICAL: "bold red",
    }

    def __init__(self, console: Console | None = None):
        self.console = console or Console(stderr=True)

    def notify(self, event: WatchEvent) -> None:
        style = self.STYLE[event.severity]
        self.console.print(f"[{style}]\\[{event.kind}][/{style}] {event.at:%H:%M:%S} {event.address} – {event.message}")


class WebhookNotifier:
    def __init__(self, url: str, *, timeout: float = 10.0, post: Callable[..., Any] | None = None):
        self.url = url
        self.timeout = timeout
        self._post = post

    def notify(self, event: WatchEvent) -> None:
        body: dict[str, Any] = {
            "content": f"**rugscan {event.kind}** `{event.address}` ({event.chain.value}): {event.message}",
            "username": "rugscan",
            "embeds": [
                {
                    "title": event.kind,
                    "description": event.message,
                    "fields": [
                        {"name": k, "value": str(v), "inline": True}
                        for k, v in event.to_payload().items()
                        if k not in {"message", "kind"}
                    ],
                }
            ],
        }
        try:
            if self._post is not None:
                self._post(self.url, json=body, timeout=self.timeout)
            else:
                import requests

                requests.post(self.url, json=body, timeout=self.timeout).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - alerting must never kill the watcher
            log.warning("webhook delivery failed: %s", exc)


class RecordingNotifier:
    """Test helper: keeps every event."""

    def __init__(self) -> None:
        self.events: list[WatchEvent] = []

    def notify(self, event: WatchEvent) -> None:
        self.events.append(event)


# --------------------------------------------------------------------------- #


class TokenWatcher:
    def __init__(
        self,
        provider: BaseDataProvider,
        engine: ScoringEngine,
        notifiers: list[Notifier],
        *,
        cache: SnapshotCache | None = None,
        liquidity_drop_pct: float = 30.0,
        score_jump: float = 10.0,
        lp_unlock_drop: float = 0.10,
    ):
        self.provider = provider
        self.engine = engine
        self.notifiers = notifiers
        self.cache = cache
        self.liquidity_drop_pct = liquidity_drop_pct
        self.score_jump = score_jump
        self.lp_unlock_drop = lp_unlock_drop
        self._last_snapshot: dict[tuple[Chain, str], TokenSnapshot] = {}
        self._last_report: dict[tuple[Chain, str], RiskReport] = {}

    # ------------------------------------------------------------------ #
    def _previous(self, chain: Chain, address: str) -> tuple[TokenSnapshot | None, RiskReport | None]:
        key = (chain, address)
        snap, rep = self._last_snapshot.get(key), self._last_report.get(key)
        if (snap is None or rep is None) and self.cache is not None:
            snap = snap or self.cache.get_latest(chain, address)
            rep = rep or self.cache.latest_report(chain, address)
        return snap, rep

    def _emit(self, event: WatchEvent) -> WatchEvent:
        for notifier in self.notifiers:
            try:
                notifier.notify(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("notifier %s failed: %s", type(notifier).__name__, exc)
        return event

    async def check(self, chain: Chain, address: str) -> list[WatchEvent]:
        """Fetch, score, compare with the previous observation and emit events."""
        chain = Chain(chain)
        prev_snap, prev_report = self._previous(chain, address)
        try:
            snapshot = await self.provider.fetch_snapshot(chain, address)
        except Exception as exc:  # noqa: BLE001
            return [self._emit(WatchEvent("error", chain, address, f"fetch failed: {exc}", Severity.MEDIUM))]

        report = self.engine.score(snapshot, provider=self.provider.name)
        address = report.address
        if self.cache is not None:
            self.cache.put_snapshot(snapshot, self.provider.name)
            self.cache.put_report(report)
        self._last_snapshot[(chain, address)] = snapshot
        self._last_report[(chain, address)] = report

        events: list[WatchEvent] = []
        if prev_report is None:
            msg = f"baseline: {report.risk_tier.value} {report.risk_score:.1f}/100, {len(report.flags)} flag(s)"
            events.append(WatchEvent("initial", chain, address, msg, _tier_severity(report.risk_tier), report=report))
        else:
            diff = SnapshotCache.diff(prev_report, report)
            if TIER_ORDER.index(report.risk_tier) > TIER_ORDER.index(prev_report.risk_tier):
                events.append(
                    WatchEvent(
                        "tier_escalation",
                        chain,
                        address,
                        f"{prev_report.risk_tier.value} → {report.risk_tier.value}: {diff.summary()}",
                        _tier_severity(report.risk_tier),
                        report=report,
                        diff=diff,
                    )
                )
            elif diff.score_delta >= self.score_jump:
                events.append(
                    WatchEvent(
                        "score_jump",
                        chain,
                        address,
                        diff.summary(),
                        Severity.MEDIUM,
                        report=report,
                        diff=diff,
                    )
                )
            new_critical = sorted(
                {f.code for f in report.flags if f.severity is Severity.CRITICAL}
                - {f.code for f in prev_report.flags if f.severity is Severity.CRITICAL}
            )
            if new_critical:
                events.append(
                    WatchEvent(
                        "new_critical_flag",
                        chain,
                        address,
                        "new critical flag(s): " + ", ".join(new_critical),
                        Severity.CRITICAL,
                        report=report,
                        diff=diff,
                    )
                )

        if prev_snap is not None:
            events.extend(self._snapshot_events(chain, address, prev_snap, snapshot, report))

        return [self._emit(e) for e in events]

    def _snapshot_events(
        self, chain: Chain, address: str, prev: TokenSnapshot, cur: TokenSnapshot, report: RiskReport
    ) -> list[WatchEvent]:
        events: list[WatchEvent] = []
        before, after = prev.total_liquidity_usd, cur.total_liquidity_usd
        if before > 0:
            drop_pct = (before - after) / before * 100
            if drop_pct >= self.liquidity_drop_pct:
                events.append(
                    WatchEvent(
                        "liquidity_drop",
                        chain,
                        address,
                        f"liquidity fell {drop_pct:.0f}% (${before:,.0f} → ${after:,.0f})",
                        Severity.CRITICAL if drop_pct >= 80 else Severity.HIGH,
                        report=report,
                    )
                )
        pp, cp = prev.primary_pool, cur.primary_pool
        if pp is None or cp is None or not pp.lp_holdings or not cp.lp_holdings:
            return events  # lock status unknown on one side – nothing to compare
        if pp.locked_share - cp.locked_share >= self.lp_unlock_drop:
            events.append(
                WatchEvent(
                    "lp_unlocked",
                    chain,
                    address,
                    f"locked LP share fell {pp.locked_share:.0%} → {cp.locked_share:.0%} – LP tokens left the locker",
                    Severity.CRITICAL,
                    report=report,
                )
            )
        return events

    async def run(
        self,
        chain: Chain,
        address: str,
        *,
        interval_s: float = 60.0,
        max_iterations: int | None = None,
        stop: asyncio.Event | None = None,
    ) -> int:
        """Poll until ``max_iterations`` checks have run or ``stop`` is set. Returns checks performed."""
        n = 0
        while max_iterations is None or n < max_iterations:
            await self.check(chain, address)
            n += 1
            if max_iterations is not None and n >= max_iterations:
                break
            if stop is not None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval_s)
                    break
                except TimeoutError:
                    continue
            await asyncio.sleep(interval_s)
        return n


def _tier_severity(tier: RiskTier) -> Severity:
    return {
        RiskTier.LOW: Severity.INFO,
        RiskTier.MEDIUM: Severity.MEDIUM,
        RiskTier.HIGH: Severity.HIGH,
        RiskTier.CRITICAL: Severity.CRITICAL,
    }[tier]
