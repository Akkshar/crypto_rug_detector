"""Tests for config loading, the ML layer, score aggregation and the final report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from config.settings import RiskConfig, load_risk_config
from src.models.ml_model import FEATURE_NAMES, FeatureVector, RugRiskModel, extract_features, generate_synthetic_dataset
from src.models.risk_report import MLAssessment, RiskCategory, RiskReport, RiskTier
from src.models.scoring_engine import ScoringEngine
from tests.conftest import FIXTURE_NAMES

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_config_loads_with_all_categories_capped(config):
    assert set(config.category_caps) == set(RiskCategory)
    assert config.scoring.rule_weight + config.scoring.ml_weight == pytest.approx(1.0)
    assert config.points("HONEYPOT_SELL_BLOCKED") == 50


def test_config_unknown_flag_raises(config):
    with pytest.raises(KeyError):
        config.points("NOT_A_FLAG")


def test_config_rejects_bad_weights(tmp_path: Path):
    raw = yaml.safe_load(Path("config/risk_weights.yaml").read_text(encoding="utf-8"))
    raw["scoring"]["ml_weight"] = 0.5  # rule 0.75 + ml 0.5 != 1
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_risk_config(bad)


def test_config_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"tiers": {"medium": 25, "high": 50, "critical": 75, "bogus": 1}})


# --------------------------------------------------------------------------- #
# Feature extraction & ML
# --------------------------------------------------------------------------- #


def test_feature_extraction_benign(config, benign):
    fv = extract_features(benign, config)
    assert fv.imputed == ()
    assert fv.values["is_lp_locked"] == 1.0
    assert fv.values["has_mint_function"] == 0.0
    assert fv.values["max_tax_rate"] == pytest.approx(1.2)
    assert fv.values["tvl_usd"] == 850_000
    assert fv.values["top10_concentration"] == pytest.approx(0.19 / 0.56, abs=1e-3)
    assert list(fv.values) == list(FEATURE_NAMES)


def test_feature_extraction_honeypot_blocked_sell_is_100pct_tax(config, honeypot):
    assert extract_features(honeypot, config).values["max_tax_rate"] == 100.0


def test_feature_extraction_imputes_missing(config, benign):
    benign.deployer = None
    benign.holders.top_holders = []
    benign.pools[0].lp_holdings = []
    fv = extract_features(benign, config)
    assert set(fv.imputed) == {"deployer_age_days", "top10_concentration", "is_lp_locked"}
    assert fv.values["deployer_age_days"] == 90.0


def test_synthetic_dataset_is_deterministic_and_labelled():
    X1, y1 = generate_synthetic_dataset(500, seed=3)
    X2, y2 = generate_synthetic_dataset(500, seed=3)
    assert np.array_equal(X1, X2) and np.array_equal(y1, y2)
    assert X1.shape == (500, len(FEATURE_NAMES))
    assert 0.3 < y1.mean() < 0.5


def test_model_learns_but_is_not_trivially_separable(engine):
    model: RugRiskModel = engine.model
    assert 0.80 < model.train_accuracy < 0.995
    importances = model.feature_importances()
    assert sum(importances.values()) == pytest.approx(1.0)
    assert max(importances.values()) < 0.7, "one feature should not dominate the synthetic model"


def test_model_predictions_directional(engine):
    clean = FeatureVector(
        values={
            "top10_concentration": 0.2,
            "is_lp_locked": 1,
            "max_tax_rate": 1,
            "has_mint_function": 0,
            "deployer_age_days": 800,
            "tvl_usd": 900_000,
        },
        imputed=(),
    )
    dirty = FeatureVector(
        values={
            "top10_concentration": 0.9,
            "is_lp_locked": 0,
            "max_tax_rate": 60,
            "has_mint_function": 1,
            "deployer_age_days": 1,
            "tvl_usd": 3_000,
        },
        imputed=(),
    )
    p_clean, p_dirty = engine.model.predict(clean), engine.model.predict(dirty)
    assert p_clean.rug_probability < 0.2 < 0.8 < p_dirty.rug_probability
    assert engine.model.predict(clean).rug_probability == p_clean.rug_probability  # deterministic


# --------------------------------------------------------------------------- #
# Aggregation & blending
# --------------------------------------------------------------------------- #


def test_tier_boundaries(engine):
    assert engine.tier_for(0) is RiskTier.LOW
    assert engine.tier_for(24.9) is RiskTier.LOW
    assert engine.tier_for(25) is RiskTier.MEDIUM
    assert engine.tier_for(50) is RiskTier.HIGH
    assert engine.tier_for(75) is RiskTier.CRITICAL
    assert engine.tier_for(100) is RiskTier.CRITICAL


def _ml(prob: float, reliable: bool = True) -> MLAssessment:
    return MLAssessment(model="t", rug_probability=prob, anomaly_score=0.0, reliable=reliable)


def test_combine_blends_rule_and_ml(engine):
    score, tier = engine.combine(50.0, _ml(1.0), hard_fail=False)
    assert score == pytest.approx(0.75 * 50 + 0.25 * 100) and tier is RiskTier.HIGH
    score, _ = engine.combine(50.0, _ml(1.0, reliable=False), hard_fail=False)
    assert score == pytest.approx(0.875 * 50 + 0.125 * 100, abs=0.05)  # engine rounds to 1 dp
    score, _ = engine.combine(50.0, None, hard_fail=False)
    assert score == 50.0


def test_combine_hard_fail_floor(engine):
    score, tier = engine.combine(10.0, _ml(0.0), hard_fail=True)
    assert score == 95.0 and tier is RiskTier.CRITICAL
    score, tier = engine.combine(100.0, _ml(1.0), hard_fail=True)
    assert score == 100.0 and tier is RiskTier.CRITICAL


def test_category_caps_enforced(engine, honeypot):
    report = engine.score(honeypot, provider="mock")
    for row in report.breakdown:
        assert row.capped_points <= row.cap
        assert row.capped_points <= row.raw_points
    contract = next(r for r in report.breakdown if r.category is RiskCategory.CONTRACT_PRIVILEGE)
    assert contract.raw_points > contract.cap == contract.capped_points
    assert report.rule_score <= 100


# --------------------------------------------------------------------------- #
# End-to-end verdicts
# --------------------------------------------------------------------------- #


def test_benign_token_is_low(engine, benign):
    report = engine.score(benign, provider="mock")
    assert report.risk_tier is RiskTier.LOW
    assert report.risk_score < 25
    assert not report.hard_fail and report.hard_fail_reasons == []
    assert report.ml is not None and report.ml.rug_probability < 0.2 and report.ml.reliable
    assert report.missing_data == []
    assert [f.code for f in report.flags] == ["TOP10_CONCENTRATION_ELEVATED"]


def test_honeypot_is_critical_via_prefilter(engine, honeypot):
    report = engine.score(honeypot, provider="mock")
    assert report.risk_tier is RiskTier.CRITICAL
    assert report.hard_fail and len(report.hard_fail_reasons) == 2
    assert report.risk_score >= engine.config.scoring.hard_fail_score_floor
    assert {"HONEYPOT_SELL_BLOCKED", "SELL_TAX_EXTREME", "WHITELIST_TRANSFER_GATE"} <= set(report.critical_flags) | {
        f.code for f in report.flags
    }
    assert report.warnings[0].startswith("PRE-FILTER HARD FAIL")
    assert report.ml.is_out_of_distribution and not report.ml.reliable


def test_unlocked_rug_is_critical_by_score_alone(engine, rug):
    report = engine.score(rug, provider="mock")
    assert report.risk_tier is RiskTier.CRITICAL
    assert not report.hard_fail, "the rug must reach CRITICAL through weighted scoring, not the pre-filter"
    assert report.risk_score >= 75
    assert {"LP_MOSTLY_UNLOCKED", "HIDDEN_MINT_FUNCTION", "DEPLOYER_MIXER_FUNDED"} <= set(report.critical_flags)
    assert report.ml.rug_probability > 0.8


def test_verdict_ordering(engine, provider):
    scores = {name: engine.score(provider.resolve(name), provider="mock").risk_score for name in FIXTURE_NAMES}
    assert scores["sample_benign_token"] < scores["sample_unlocked_rug"] <= scores["sample_honeypot"]


def test_prefilter_alone_forces_critical(engine, benign):
    benign.contract.sell_tax_pct = 100.0
    report = engine.score(benign, provider="mock")
    assert report.hard_fail and report.risk_tier is RiskTier.CRITICAL
    assert report.risk_score >= 95


def test_ml_disabled_scores_rules_only(config, benign):
    cfg = config.model_copy(update={"ml": config.ml.model_copy(update={"enabled": False})})
    report = ScoringEngine(cfg).score(benign, provider="mock")
    assert report.ml is None and report.risk_score == report.rule_score


def test_missing_data_is_reported_not_penalised(engine, benign):
    benign.deployer = None
    benign.holders.top_holders = []
    report = engine.score(benign, provider="mock")
    assert set(report.missing_data) == {"holder_metrics", "deployer_tracer"}
    assert report.risk_tier is RiskTier.LOW
    assert set(report.ml.imputed_features) == {"deployer_age_days", "top10_concentration"}


def test_report_json_roundtrip(engine, rug):
    report = engine.score(rug, provider="mock")
    payload = json.loads(report.to_json())
    assert payload["risk_tier"] == "CRITICAL" and payload["schema_version"] == 1
    assert RiskReport.model_validate_json(report.to_json()).risk_score == report.risk_score
