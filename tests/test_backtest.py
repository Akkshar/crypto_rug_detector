from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_utils import to_checksum_address
from rich.console import Console

from src.backtest import LabelledSnapshot, load_labelled, render_backtest, run_backtest
from src.backtest.harness import parse_label, roc_auc, threshold_stats
from src.models.risk_report import RiskTier
from src.storage import SnapshotCache


def variant(snapshot, i: int, **contract_updates):
    s = snapshot.model_copy(deep=True)
    s.address = to_checksum_address(f"0x{i:040x}")
    for k, v in contract_updates.items():
        setattr(s.contract, k, v)
    return s


@pytest.fixture
def corpus(benign, honeypot, rug) -> list[LabelledSnapshot]:
    return [
        LabelledSnapshot(benign, False),
        LabelledSnapshot(variant(benign, 11, sell_tax_pct=3.0), False),
        LabelledSnapshot(variant(benign, 12, source_verified=False), False),
        LabelledSnapshot(honeypot, True),
        LabelledSnapshot(rug, True),
        LabelledSnapshot(variant(rug, 13, sell_tax_pct=12.0), True),
    ]


def test_metrics_on_separable_corpus(engine, corpus):
    result = run_backtest(engine, corpus)
    assert (result.n, result.n_rug) == (6, 3) and result.base_rate == 0.5
    assert result.auc_score == 1.0 and result.auc_rule == 1.0 and result.auc_ml == 1.0
    assert result.at_tier.threshold == engine.config.tiers.high
    assert (result.at_tier.precision, result.at_tier.recall, result.at_tier.f1) == (1.0, 1.0, 1.0)
    assert result.best_f1.f1 == 1.0
    assert result.tier_counts["LOW"]["benign"] == 3 and result.tier_counts["CRITICAL"]["rug"] == 3
    assert result.worst_misses == []
    assert result.hard_fail is not None and result.hard_fail.precision == 1.0 and result.hard_fail.tp == 1

    flags = {f.code: f for f in result.flags}
    assert flags["HONEYPOT_SELL_BLOCKED"].precision == 1.0 and flags["HONEYPOT_SELL_BLOCKED"].lift == 2.0
    assert flags["SOURCE_UNVERIFIED"].precision == 0.0 and flags["SOURCE_UNVERIFIED"].fires == 1
    assert flags["LP_MOSTLY_UNLOCKED"].recall == pytest.approx(2 / 3, abs=1e-4)  # stats are rounded to 4 dp
    assert result.flags[0].lift >= result.flags[-1].lift


def test_misses_are_reported(engine, corpus):
    mislabelled = [*corpus, LabelledSnapshot(variant(corpus[0].snapshot, 99), True)]  # a "rug" that looks benign
    result = run_backtest(engine, mislabelled)
    assert result.at_tier.fn == 1 and len(result.worst_misses) == 1
    assert result.worst_misses[0]["label"] == "rug" and result.worst_misses[0]["tier"] == "LOW"
    assert result.auc_score < 1.0


def test_positive_tier_and_json(engine, corpus):
    result = run_backtest(engine, corpus, positive_tier=RiskTier.MEDIUM)
    assert result.at_tier.threshold == engine.config.tiers.medium
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["auc"]["score"] == 1.0 and payload["positive_tier"] == "MEDIUM"
    assert {f["code"] for f in payload["flags"]} >= {"HONEYPOT_SELL_BLOCKED", "DEPLOYER_MIXER_FUNDED"}


def test_render_does_not_crash(engine, corpus):
    console = Console(record=True, width=120)
    render_backtest(run_backtest(engine, corpus), console=console)
    text = console.export_text()
    assert "AUC – final score" in text and "Flag diagnostics" in text


def test_empty_corpus_raises(engine):
    with pytest.raises(ValueError):
        run_backtest(engine, [])


def test_helpers():
    assert roc_auc([0.9, 0.1, 0.5], [True, False, False]) == 1.0
    assert roc_auc([0.5, 0.5], [True, False]) == 0.5
    assert roc_auc([1.0], [True]) is None
    assert threshold_stats([80, 20], [True, False], 50).f1 == 1.0
    assert parse_label("RUG") is True and parse_label("benign") is False and parse_label("maybe") is None


def test_load_labelled_from_cache_and_jsonl(tmp_path: Path, benign, honeypot, rug):
    with SnapshotCache(":memory:") as cache:
        cache.put_snapshot(benign, "mock", label="benign")
        cache.put_snapshot(honeypot, "mock", label="honeypot")
        cache.put_snapshot(rug, "mock")  # unlabelled → excluded
        cache.set_label("base", rug.address, "weird")  # unparseable → excluded
        items = load_labelled(cache=cache)
        assert {(i.snapshot.address, i.is_rug) for i in items} == {(benign.address, False), (honeypot.address, True)}

        out = tmp_path / "c.jsonl"
        cache.export_jsonl(out, labelled_only=True)
    from_file = load_labelled(jsonl=out)
    assert len(from_file) == 2 and all(i.source == "jsonl" for i in from_file)
