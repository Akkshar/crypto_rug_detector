from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

import pytest
from eth_utils import to_checksum_address

from config.settings import RiskConfig
from src.backtest import LabelledSnapshot, train_model, tune_weights, write_proposal
from src.backtest.train import build_dataset, cross_validate
from src.backtest.tune import MemoisedEngine
from src.models.ml_model import RugRiskModel, get_model, model_fingerprint
from src.models.risk_report import RiskTier, TokenSnapshot
from src.models.scoring_engine import ScoringEngine


def perturb(snapshot: TokenSnapshot, i: int, rng: random.Random) -> TokenSnapshot:
    s = snapshot.model_copy(deep=True)
    s.address = to_checksum_address(f"0x{i:040x}")
    s.price_usd = (s.price_usd or 1.0) * rng.uniform(0.5, 2.0)
    s.pools[0].liquidity_usd *= rng.uniform(0.6, 1.6)
    if s.contract.sell_tax_pct is not None:
        s.contract.sell_tax_pct = min(100.0, max(0.0, s.contract.sell_tax_pct + rng.uniform(-1, 1)))
    if s.deployer and s.deployer.first_seen:
        s.deployer.first_seen -= timedelta(days=rng.uniform(0, 30))
    return s


@pytest.fixture
def corpus(benign, honeypot, rug) -> list[LabelledSnapshot]:
    rng = random.Random(1)
    items = []
    for i in range(1, 21):
        items.append(LabelledSnapshot(perturb(benign, i, rng), False))
    for i in range(21, 31):
        items.append(LabelledSnapshot(perturb(honeypot, i, rng), True))
    for i in range(31, 41):
        items.append(LabelledSnapshot(perturb(rug, i, rng), True))
    return items


def test_build_dataset_shapes(config, corpus):
    X, y, imputed = build_dataset(corpus, config)
    assert X.shape == (40, 6) and y.sum() == 20 and imputed == 0


def test_cross_validate_and_train(tmp_path: Path, config: RiskConfig, corpus):
    X, y, _ = build_dataset(corpus, config)
    metrics = cross_validate(X, y, config, folds=4)
    assert metrics.folds == 4 and metrics.auc_mean is not None and metrics.auc_mean > 0.9
    assert 0.0 <= metrics.brier < 0.15 and metrics.accuracy > 0.9

    model, metrics = train_model(corpus, config, calibrate="sigmoid", folds=3, persist_dir=tmp_path)
    assert model.trained_on == "labelled" and model.calibration == "sigmoid"
    assert (tmp_path / f"{model_fingerprint(config.ml, 'labelled')}.joblib").exists()
    assert sum(model.feature_importances().values()) == pytest.approx(1.0)
    probs = model.predict_proba_matrix(X)
    assert probs.min() >= 0.0 and probs.max() <= 1.0 and probs[y == 1].mean() > probs[y == 0].mean()

    # get_model now prefers the labelled model for this configuration
    get_model.cache_clear()
    loaded = get_model(config.ml, tmp_path)
    assert loaded.trained_on == "labelled"
    get_model.cache_clear()


def test_train_without_calibration_and_too_few_samples(config, corpus):
    model, _ = train_model(corpus, config, calibrate=None, folds=2)
    assert model.calibration is None and hasattr(model.classifier, "feature_importances_")
    with pytest.raises(ValueError, match="at least 30"):
        train_model(corpus[:10], config)
    with pytest.raises(ValueError, match="per class"):
        train_model(corpus[:20] + corpus[20:25], config)  # 20 benign / 5 rug


def test_calibrated_model_scores_through_engine(config, corpus, benign):
    model = RugRiskModel(config.ml, dataset=build_dataset(corpus, config)[:2], calibrate="isotonic")
    report = ScoringEngine(config, model=model).score(benign, provider="t")
    assert report.ml is not None and report.ml.rug_probability < 0.5 and report.ml.contributions


def test_tune_improves_or_keeps_objective(tmp_path: Path, config: RiskConfig, corpus, engine):
    # Start from the worst possible weights: every flag at zero, so every token scores LOW (F1 = 0),
    # and the pre-filter disabled so the weights alone decide the verdict.
    bad = config.model_copy(
        update={
            "flags": {k: 0.0 for k in config.flags},
            "prefilter": config.prefilter.model_copy(update={"hard_fail_tax_pct": 100.0}),
        }
    )
    result = tune_weights(bad, corpus, positive_tier=RiskTier.HIGH, passes=1, model=engine.model)
    # Honeypots still hard-fail via the sell simulation (weights don't govern that), so the baseline
    # catches them but misses every unlocked-LP rug; tuning must recover those.
    assert result.baseline.fn > 0 and result.best.fn == 0 and result.best.f1 > 0.9
    assert result.best_objective > result.baseline_objective
    assert result.changed and all(after > before for before, after in result.changed.values())
    assert all(result.weights[c] == 0.0 or c in result.changed for c in result.weights)  # only fired flags move

    proposal = write_proposal(result, Path("config/risk_weights.yaml"), tmp_path / "tuned.yaml")
    from config.settings import load_risk_config

    tuned = load_risk_config(proposal)
    assert tuned.flags == {k: float(v) for k, v in result.weights.items()}
    assert "Proposed by `rugscan tune`" in proposal.read_text(encoding="utf-8")


def test_tune_respects_fp_ceiling(config, corpus, engine):
    result = tune_weights(config, corpus, max_fp_rate=0.0, passes=1, model=engine.model)
    fp_rate = result.best.fp / (result.best.fp + result.best.tn)
    assert fp_rate == 0.0 and result.to_dict()["max_fp_rate"] == 0.0


def test_memoised_engine_caches_ml(config, benign, engine):
    MemoisedEngine._ml_cache.clear()
    e = MemoisedEngine(config, model=engine.model)
    first = e.refine(benign)
    assert e.refine(benign) is first and len(MemoisedEngine._ml_cache) == 1


def test_compare_models_and_algorithms(config, corpus):
    from src.backtest import ALGORITHMS, compare_models

    results = compare_models(corpus, config, folds=3)
    assert [m.algorithm for m in results] == list(ALGORITHMS)
    for m in results:
        assert m.auc_mean is not None and m.ap_mean is not None and 0.0 <= m.ap_mean <= 1.0
        assert m.precision > 0 and m.recall > 0 and m.false_negatives + round(m.recall * m.n_rug) == m.n_rug
    assert all(m.ap_mean > 0.8 for m in results)  # the corpus is separable; even the linear baseline should get it

    rf, _ = train_model(corpus, config, calibrate=None, folds=2, algorithm="random_forest")
    assert rf.algorithm == "random_forest" and sum(rf.feature_importances().values()) == pytest.approx(1.0)
    lr, _ = train_model(corpus, config, calibrate="sigmoid", folds=2, algorithm="logistic")
    assert lr.algorithm == "logistic" and sum(lr.feature_importances().values()) == pytest.approx(1.0)
    assert lr.predict_proba_matrix(build_dataset(corpus, config)[0]).shape == (40,)
    assert model_fingerprint(config.ml) != model_fingerprint(config.ml.model_copy(update={"algorithm": "logistic"}))


def test_pr_auc_definition():
    from src.backtest.harness import pr_auc

    assert pr_auc([0.9, 0.8, 0.1], [True, True, False]) == 1.0
    assert pr_auc([0.9, 0.1, 0.8], [True, False, True]) == pytest.approx(1.0)  # ranking is what matters
    assert pr_auc([0.1, 0.9], [True, False]) == pytest.approx(0.5)  # perfect inversion → AP = 0.5 for 1 of 2
    assert pr_auc([0.5, 0.5], [True, False]) == pytest.approx(0.5)  # tie handled as one block, not split favourably
    assert pr_auc([0.3, 0.2], [True, True]) is None and pr_auc([0.3], [False]) is None


def test_tune_rejects_empty(config):
    with pytest.raises(ValueError):
        tune_weights(config, [])
