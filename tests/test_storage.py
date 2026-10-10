from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models.risk_report import Chain, RiskReport, TokenSnapshot
from src.storage import CachingProvider, SnapshotCache


@pytest.fixture
def cache() -> SnapshotCache:
    with SnapshotCache(":memory:") as c:
        yield c


def test_put_and_get_latest(cache, benign):
    cache.put_snapshot(benign, "mock")
    got = cache.get_latest(Chain.ETHEREUM, benign.address)
    assert got == benign
    assert cache.get_latest("bsc", benign.address) is None
    assert cache.stats() == {"snapshots": 1, "tokens": 1, "labelled": 0, "reports": 0}


def test_latest_prefers_newest_observation(cache, benign):
    older = benign.model_copy(update={"observed_at": benign.observed_at - timedelta(days=1)})
    cache.put_snapshot(older, "mock")
    cache.put_snapshot(benign, "mock")
    assert cache.get_latest("ethereum", benign.address).observed_at == benign.observed_at
    assert len(cache.snapshot_history("ethereum", benign.address)) == 2


def test_max_age_expires(cache, benign):
    cache.put_snapshot(benign, "mock")  # observed 2026-10-10, i.e. the past
    assert cache.get_latest("ethereum", benign.address, max_age=timedelta(days=36500)) is not None
    assert cache.get_latest("ethereum", benign.address, max_age=timedelta(seconds=1)) is None


def test_labels_and_iteration(cache, benign, rug):
    cache.put_snapshot(benign, "mock")
    cache.put_snapshot(rug, "mock", label="rug")
    assert cache.set_label("ethereum", benign.address, "benign") == 1
    labelled = list(cache.iter_snapshots(labelled_only=True))
    assert {lbl for _, _, lbl in labelled} == {"benign", "rug"}
    assert [s.chain for s, _, _ in cache.iter_snapshots(chain="base")] == [Chain.BASE]


def test_reports_history_and_diff(cache, engine, benign):
    first = engine.score(benign, provider="mock")
    cache.put_report(first)
    benign.contract.sell_tax_pct = 30.0
    later = engine.score(benign, provider="mock").model_copy(
        update={"generated_at": first.generated_at + timedelta(hours=1)}
    )
    cache.put_report(later)

    history = cache.report_history("ethereum", benign.address)
    assert [r.generated_at for r in history] == [later.generated_at, first.generated_at]
    diff = SnapshotCache.diff(history[1], history[0])
    assert diff.changed and diff.score_delta > 0
    assert "SELL_TAX_HIGH" in diff.added_flags and diff.removed_flags == []
    assert "new: SELL_TAX_HIGH" in diff.summary()
    assert cache.latest_report("ethereum", benign.address).risk_score == later.risk_score


def test_jsonl_roundtrip(cache, tmp_path: Path, benign, honeypot):
    cache.put_snapshot(benign, "mock", label="benign")
    cache.put_snapshot(honeypot, "mock")
    out = tmp_path / "snaps.jsonl"
    assert cache.export_jsonl(out) == 2
    assert cache.export_jsonl(tmp_path / "labelled.jsonl", labelled_only=True) == 1

    with SnapshotCache(":memory:") as other:
        assert other.import_jsonl(out) == 2
        assert other.get_latest("bsc", honeypot.address) == honeypot
        assert other.stats()["labelled"] == 1


def test_persistent_file(tmp_path: Path, benign):
    db = tmp_path / "nested" / "rugscan.db"
    with SnapshotCache(db) as c:
        c.put_snapshot(benign, "mock")
    with SnapshotCache(db) as c:
        assert c.get_latest("ethereum", benign.address) == benign


class _CountingProvider:
    name = "counting"

    def __init__(self, snapshot: TokenSnapshot):
        self.snapshot = snapshot
        self.calls = 0

    async def supports(self, chain):
        return True

    async def fetch_snapshot(self, chain, address):
        self.calls += 1
        return self.snapshot.model_copy(update={"observed_at": datetime.now(timezone.utc)})


def test_caching_provider_short_circuits(cache, benign):
    inner = _CountingProvider(benign)
    cp = CachingProvider(inner, cache, max_age=timedelta(hours=1))  # type: ignore[arg-type]

    first = asyncio.run(cp.fetch_snapshot(Chain.ETHEREUM, benign.address))
    assert inner.calls == 1 and not cp.last_hit
    second = asyncio.run(cp.fetch_snapshot(Chain.ETHEREUM, benign.address))
    assert inner.calls == 1 and cp.last_hit and second == first

    uncached = CachingProvider(inner, cache, max_age=None)  # type: ignore[arg-type]
    asyncio.run(uncached.fetch_snapshot(Chain.ETHEREUM, benign.address))
    assert inner.calls == 2 and cp.name == "counting"


def test_report_json_compact_is_valid(engine, rug):
    report = engine.score(rug, provider="mock")
    assert RiskReport.model_validate_json(report.to_json(indent=0)) == RiskReport.model_validate_json(report.to_json())
