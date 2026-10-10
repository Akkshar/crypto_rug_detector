from __future__ import annotations

from pathlib import Path

from src.models.ml_model import FEATURE_NAMES, RugRiskModel, extract_features, get_model, model_fingerprint


def test_explain_returns_every_feature_and_points_the_right_way(engine, config, benign, rug):
    rug_contrib = engine.model.explain(extract_features(rug, config))
    assert set(rug_contrib) == set(FEATURE_NAMES)
    top, delta = max(rug_contrib.items(), key=lambda kv: kv[1])
    assert delta > 0 and top in {"max_tax_rate", "top10_concentration", "deployer_age_days", "is_lp_locked"}

    benign_contrib = engine.model.explain(extract_features(benign, config))
    assert max(benign_contrib.values()) <= 0.05  # nothing about the benign token pushes it toward "rug"


def test_report_carries_contributions(engine, rug):
    report = engine.score(rug, provider="t")
    assert report.ml is not None and set(report.ml.contributions) == set(FEATURE_NAMES)
    drivers = report.ml.top_drivers(2)
    assert len(drivers) == 2 and abs(drivers[0][1]) >= abs(drivers[1][1])


def test_persistence_roundtrip(tmp_path: Path, config, benign):
    get_model.cache_clear()
    first = get_model(config.ml, tmp_path)
    path = tmp_path / f"{model_fingerprint(config.ml)}.joblib"
    assert path.exists() and first.trained_on == "synthetic"

    get_model.cache_clear()
    loaded = get_model(config.ml, tmp_path)
    assert isinstance(loaded, RugRiskModel) and loaded is not first
    fv = extract_features(benign, config)
    assert loaded.predict(fv) == first.predict(fv)
    assert loaded.explain(fv) == first.explain(fv)
    get_model.cache_clear()


def test_corrupt_cache_falls_back_to_training(tmp_path: Path, config, caplog):
    get_model.cache_clear()
    (tmp_path / f"{model_fingerprint(config.ml)}.joblib").write_bytes(b"not a pickle")
    with caplog.at_level("WARNING", logger="rugscan.ml"):
        model = get_model(config.ml, tmp_path)
    assert model.train_accuracy > 0.8 and "unreadable model cache" in caplog.text
    get_model.cache_clear()


def test_fingerprint_changes_with_config(config):
    other = config.ml.model_copy(update={"seed": config.ml.seed + 1})
    assert model_fingerprint(config.ml) != model_fingerprint(other)
    assert model_fingerprint(config.ml) != model_fingerprint(config.ml, trained_on="labelled")
