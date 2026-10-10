from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from src.models.risk_report import Chain, SellSimulation, Severity, TokenSnapshot
from src.storage import SnapshotCache
from src.watch import RecordingNotifier, TokenWatcher, WatchEvent, WebhookNotifier


class SequenceProvider:
    """Returns the given snapshots in order, then keeps returning the last one."""

    name = "sequence"

    def __init__(self, *snapshots: TokenSnapshot | Exception):
        self.items = list(snapshots)
        self.calls = 0

    async def supports(self, chain):
        return True

    async def fetch_snapshot(self, chain, address):
        item = self.items[min(self.calls, len(self.items) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item.model_copy(update={"observed_at": item.observed_at + timedelta(minutes=self.calls)})


def drained(benign: TokenSnapshot) -> TokenSnapshot:
    """The benign token after a liquidity-drain + honeypot switch."""
    s = benign.model_copy(deep=True)
    s.pools[0].liquidity_usd = 60_000.0  # from $850k → -93%
    s.pools[0].lp_holdings[0].locker = None  # LP left the UNCX locker
    s.pools[0].lp_holdings[0].is_deployer_or_owner = True
    s.sell_simulation = SellSimulation(attempted=5, succeeded=0, revert_reason="TRANSFER_FAILED")
    return s


def run(coro):
    return asyncio.run(coro)


def test_initial_then_escalation_events(engine, benign):
    provider = SequenceProvider(benign, drained(benign))
    rec = RecordingNotifier()
    watcher = TokenWatcher(provider, engine, [rec])  # type: ignore[arg-type]

    first = run(watcher.check(Chain.ETHEREUM, benign.address))
    assert [e.kind for e in first] == ["initial"] and first[0].severity is Severity.INFO

    second = run(watcher.check(Chain.ETHEREUM, benign.address))
    kinds = {e.kind for e in second}
    assert {"tier_escalation", "new_critical_flag", "liquidity_drop", "lp_unlocked"} <= kinds
    esc = next(e for e in second if e.kind == "tier_escalation")
    assert esc.severity is Severity.CRITICAL and "LOW → CRITICAL" in esc.message
    assert "HONEYPOT_SELL_BLOCKED" in esc.diff.added_flags
    liq = next(e for e in second if e.kind == "liquidity_drop")
    assert "93%" in liq.message and liq.severity is Severity.CRITICAL
    assert len(rec.events) == len(first) + len(second)


def test_no_events_when_nothing_changes(engine, benign):
    watcher = TokenWatcher(SequenceProvider(benign), engine, [])  # type: ignore[arg-type]
    run(watcher.check(Chain.ETHEREUM, benign.address))
    assert run(watcher.check(Chain.ETHEREUM, benign.address)) == []


def test_error_event_and_recovery(engine, benign):
    provider = SequenceProvider(RuntimeError("rpc down"), benign)
    rec = RecordingNotifier()
    watcher = TokenWatcher(provider, engine, [rec])  # type: ignore[arg-type]
    first = run(watcher.check(Chain.ETHEREUM, benign.address))
    assert first[0].kind == "error" and "rpc down" in first[0].message
    assert run(watcher.check(Chain.ETHEREUM, benign.address))[0].kind == "initial"


def test_previous_state_comes_from_cache(engine, benign):
    with SnapshotCache(":memory:") as cache:
        baseline = engine.score(benign, provider="t")
        cache.put_snapshot(benign, "t")
        cache.put_report(baseline)
        watcher = TokenWatcher(SequenceProvider(drained(benign)), engine, [], cache=cache)  # type: ignore[arg-type]
        events = run(watcher.check(Chain.ETHEREUM, benign.address))
        assert "tier_escalation" in {e.kind for e in events}  # no "initial": cache supplied the baseline
        assert len(cache.report_history("ethereum", benign.address)) == 2


def test_run_respects_max_iterations(engine, benign):
    provider = SequenceProvider(benign)
    watcher = TokenWatcher(provider, engine, [])  # type: ignore[arg-type]
    assert run(watcher.run(Chain.ETHEREUM, benign.address, interval_s=0, max_iterations=3)) == 3
    assert provider.calls == 3


def test_run_stops_on_event(engine, benign):
    async def scenario():
        stop = asyncio.Event()
        watcher = TokenWatcher(SequenceProvider(benign), engine, [])  # type: ignore[arg-type]
        task = asyncio.create_task(watcher.run(Chain.ETHEREUM, benign.address, interval_s=30, stop=stop))
        await asyncio.sleep(0.05)
        stop.set()
        return await asyncio.wait_for(task, timeout=2)

    assert run(scenario()) == 1


def test_webhook_notifier_posts_discord_payload():
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json, timeout=timeout)

    event = WatchEvent("liquidity_drop", Chain.BASE, "0x" + "ab" * 20, "liquidity fell 90%", Severity.CRITICAL)
    WebhookNotifier("https://hooks.example/abc", post=fake_post).notify(event)
    assert sent["url"] == "https://hooks.example/abc"
    assert "liquidity_drop" in sent["body"]["content"] and sent["body"]["embeds"][0]["title"] == "liquidity_drop"
    assert event.to_payload()["chain"] == "base"


def test_webhook_failure_is_swallowed(caplog):
    def boom(*a, **k):
        raise ConnectionError("nope")

    with caplog.at_level("WARNING", logger="rugscan.watch"):
        WebhookNotifier("https://x", post=boom).notify(
            WatchEvent("initial", Chain.BSC, "0x" + "ab" * 20, "m", Severity.INFO)
        )
    assert "webhook delivery failed" in caplog.text


@pytest.mark.parametrize("drop,expected", [(10.0, True), (95.0, False)])
def test_liquidity_threshold_is_configurable(engine, benign, drop, expected):
    s2 = benign.model_copy(deep=True)
    s2.pools[0].liquidity_usd = benign.pools[0].liquidity_usd * 0.5  # -50 %
    watcher = TokenWatcher(SequenceProvider(benign, s2), engine, [], liquidity_drop_pct=drop)  # type: ignore[arg-type]
    run(watcher.check(Chain.ETHEREUM, benign.address))
    kinds = {e.kind for e in run(watcher.check(Chain.ETHEREUM, benign.address))}
    assert ("liquidity_drop" in kinds) is expected
