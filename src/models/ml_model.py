"""Lightweight ML refinement layer.

Two scikit-learn models trained on a *synthetic* population (no real labels
exist offline yet):

* ``GradientBoostingClassifier`` → ``rug_probability`` in [0, 1]
* ``IsolationForest`` → novelty score used to detect inputs that look nothing
  like the training distribution, so the engine can down-weight the ML term.

Feature extraction lives here too so the training generator and the live
scorer can never drift apart. Missing inputs are imputed with a neutral prior
and reported, never silently zero-filled.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest

from config.settings import MLConfig, RiskConfig
from src.models.risk_report import TokenSnapshot

log = logging.getLogger("rugscan.ml")

FEATURE_NAMES: tuple[str, ...] = (
    "top10_concentration",
    "is_lp_locked",
    "max_tax_rate",
    "has_mint_function",
    "deployer_age_days",
    "tvl_usd",
)

# Neutral priors used when a feature can't be observed.
IMPUTE_DEFAULTS: dict[str, float] = {
    "top10_concentration": 0.35,
    "is_lp_locked": 0.5,
    "max_tax_rate": 5.0,
    "deployer_age_days": 90.0,
    "tvl_usd": 25_000.0,
}


@dataclass(frozen=True)
class FeatureVector:
    values: dict[str, float]
    imputed: tuple[str, ...]

    def as_array(self) -> np.ndarray:
        return np.array([[self.values[name] for name in FEATURE_NAMES]], dtype=float)


def extract_features(snapshot: TokenSnapshot, config: RiskConfig) -> FeatureVector:
    values: dict[str, float] = {}
    imputed: list[str] = []

    def put(name: str, value: float | None) -> None:
        if value is None:
            values[name] = IMPUTE_DEFAULTS[name]
            imputed.append(name)
        else:
            values[name] = float(value)

    holders = snapshot.holders
    circ = holders.circulating_share() if holders.top_holders else 0.0
    if holders.top_holders and circ > 0:
        put("top10_concentration", min(1.0, sum(h.share for h in holders.circulating_top_n(10)) / circ))
    else:
        put("top10_concentration", None)

    pool = snapshot.primary_pool
    if pool is None:
        values["is_lp_locked"] = 0.0  # no pool at all is a real observation, not missing data
    elif pool.lp_holdings:
        values["is_lp_locked"] = float(pool.locked_share >= config.thresholds.lp_locked_ok_ratio)
    else:
        put("is_lp_locked", None)

    c = snapshot.contract
    taxes = [t for t in (c.buy_tax_pct, c.sell_tax_pct) if t is not None]
    if snapshot.sell_simulation and snapshot.sell_simulation.effective_sell_tax_pct is not None:
        taxes.append(snapshot.sell_simulation.effective_sell_tax_pct)
    if snapshot.sell_simulation and snapshot.sell_simulation.attempted and snapshot.sell_simulation.succeeded == 0:
        taxes.append(100.0)  # a blocked sell is a 100% effective tax
    put("max_tax_rate", max(taxes) if taxes else None)

    values["has_mint_function"] = float(
        (bool(c.mint_functions) and not c.ownership_renounced) or any(f.hidden for f in c.mint_functions)
    )

    put("deployer_age_days", snapshot.deployer.age_days(snapshot.observed_at) if snapshot.deployer else None)
    values["tvl_usd"] = snapshot.total_liquidity_usd if snapshot.pools else 0.0

    return FeatureVector(values=values, imputed=tuple(imputed))


# --------------------------------------------------------------------------- #
# Synthetic training population
# --------------------------------------------------------------------------- #


def _transform(x: np.ndarray) -> np.ndarray:
    """Compress heavy-tailed columns so IsolationForest isn't dominated by TVL."""
    out = x.copy()
    out[:, FEATURE_NAMES.index("deployer_age_days")] = np.log1p(out[:, FEATURE_NAMES.index("deployer_age_days")])
    out[:, FEATURE_NAMES.index("tvl_usd")] = np.log1p(out[:, FEATURE_NAMES.index("tvl_usd")])
    return out


def generate_synthetic_dataset(n_samples: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(X, y)`` with roughly 60/40 benign/rug split.

    Distributions are deliberately overlapping: plenty of benign tokens have a
    few bad traits and some rugs look clean on one axis, so the classifier has
    to combine evidence rather than key off a single feature.
    """
    rng = np.random.default_rng(seed)
    n_rug = int(n_samples * 0.4)
    n_ok = n_samples - n_rug

    benign = np.column_stack(
        [
            rng.beta(2.0, 4.0, n_ok),  # top10 ~0.33
            (rng.random(n_ok) < 0.75).astype(float),  # lp locked
            np.clip(rng.gamma(1.5, 3.0, n_ok), 0, 100),  # taxes ~4.5%
            (rng.random(n_ok) < 0.20).astype(float),  # mint
            rng.lognormal(4.3, 1.6, n_ok),  # age ~75d, wide
            rng.lognormal(11.0, 1.6, n_ok),  # tvl ~$60k
        ]
    )
    rug = np.column_stack(
        [
            rng.beta(4.0, 2.5, n_rug),  # top10 ~0.62
            (rng.random(n_rug) < 0.35).astype(float),
            np.clip(rng.gamma(1.8, 10.0, n_rug), 0, 100),  # taxes ~18%, long tail
            (rng.random(n_rug) < 0.50).astype(float),
            rng.lognormal(2.6, 1.6, n_rug),  # age ~13d, wide
            rng.lognormal(9.6, 1.6, n_rug),  # tvl ~$15k
        ]
    )
    X = np.vstack([benign, rug])
    y = np.concatenate([np.zeros(n_ok), np.ones(n_rug)])
    order = rng.permutation(len(y))
    return X[order], y[order]


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MLPrediction:
    rug_probability: float
    anomaly_score: float
    is_out_of_distribution: bool


class RugRiskModel:
    name = "gbm+isoforest/synthetic-v1"

    def __init__(
        self,
        ml_config: MLConfig,
        dataset: tuple[np.ndarray, np.ndarray] | None = None,
        *,
        calibrate: str | None = None,
    ):
        self.cfg = ml_config
        X, y = dataset if dataset is not None else generate_synthetic_dataset(ml_config.n_samples, ml_config.seed)
        self.trained_on = "synthetic" if dataset is None else "labelled"
        self.calibration = calibrate
        Xt = _transform(X)
        gbm = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.08, random_state=ml_config.seed)
        if calibrate:
            from sklearn.calibration import CalibratedClassifierCV

            # Calibrated probabilities mean "0.8" ≈ 80 % of such tokens rugged; needs a few of each class per fold.
            folds = max(2, min(5, int(min(y.sum(), (1 - y).sum()))))
            self.classifier: Any = CalibratedClassifierCV(gbm, method=calibrate, cv=folds).fit(Xt, y)
        else:
            self.classifier = gbm.fit(Xt, y)
        self.detector = IsolationForest(n_estimators=100, contamination="auto", random_state=ml_config.seed).fit(Xt)
        scores = self.detector.decision_function(Xt)
        # Anything more anomalous than (1 - ood_percentile) of the training set is "novel".
        self.ood_threshold = float(np.quantile(scores, 1.0 - ml_config.ood_percentile))
        self.train_accuracy = float((self.classifier.predict(Xt) == y).mean())
        # Per-feature reference points for the what-if explanations.
        self.medians = {name: float(np.median(X[:, i])) for i, name in enumerate(FEATURE_NAMES)}
        self.n_train = len(y)

    def predict(self, features: FeatureVector) -> MLPrediction:
        x = _transform(features.as_array())
        prob = float(self.classifier.predict_proba(x)[0, 1])
        score = float(self.detector.decision_function(x)[0])
        return MLPrediction(
            rug_probability=min(1.0, max(0.0, prob)),
            anomaly_score=score,
            is_out_of_distribution=score < self.ood_threshold,
        )

    def explain(self, features: FeatureVector) -> dict[str, float]:
        """Marginal contribution of each feature: P(rug | x) − P(rug | x with feature_i at its training median).

        Positive values push the verdict toward "rug". Dependency-free alternative to SHAP that is exact for
        the question an analyst actually asks ("what if this one thing were normal?").
        """
        base = float(self.classifier.predict_proba(_transform(features.as_array()))[0, 1])
        out: dict[str, float] = {}
        for name in FEATURE_NAMES:
            counterfactual = dict(features.values)
            counterfactual[name] = self.medians[name]
            x = _transform(np.array([[counterfactual[n] for n in FEATURE_NAMES]], dtype=float))
            out[name] = round(base - float(self.classifier.predict_proba(x)[0, 1]), 4)
        return out

    def predict_proba_matrix(self, X: np.ndarray) -> np.ndarray:
        """P(rug) for a raw (untransformed) feature matrix – used by cross-validation."""
        return self.classifier.predict_proba(_transform(np.asarray(X, dtype=float)))[:, 1]

    def feature_importances(self) -> dict[str, float]:
        clf = self.classifier
        if hasattr(clf, "feature_importances_"):
            importances = clf.feature_importances_
        else:  # CalibratedClassifierCV – average the underlying boosters
            importances = np.mean([c.estimator.feature_importances_ for c in clf.calibrated_classifiers_], axis=0)
        return dict(zip(FEATURE_NAMES, map(float, importances), strict=True))

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def fingerprint(self) -> str:
        return model_fingerprint(self.cfg, self.trained_on)

    def save(self, directory: Path) -> Path:
        import joblib

        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.fingerprint()}.joblib"
        joblib.dump(self, path)
        return path

    @classmethod
    def load(cls, path: Path) -> RugRiskModel:
        import joblib

        model = joblib.load(path)
        if not isinstance(model, cls):
            raise TypeError(f"{path} does not contain a {cls.__name__}")
        return model


def model_fingerprint(ml_config: MLConfig, trained_on: str = "synthetic") -> str:
    import sklearn

    raw = f"{RugRiskModel.name}|{trained_on}|{sklearn.__version__}|{ml_config.model_dump_json()}|{FEATURE_NAMES}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@lru_cache(maxsize=4)
def get_model(ml_config: MLConfig, cache_dir: Path | None = None) -> RugRiskModel:
    """Load a persisted model for this configuration, or train once and persist it.

    A model trained on labelled data (``rugscan train``) takes precedence over the
    synthetic one when both exist for the same configuration.
    """
    if cache_dir is not None:
        for trained_on in ("labelled", "synthetic"):
            path = Path(cache_dir) / f"{model_fingerprint(ml_config, trained_on)}.joblib"
            if path.exists():
                try:
                    model = RugRiskModel.load(path)
                    log.info("loaded %s model from %s", trained_on, path.name)
                    return model
                except Exception as exc:  # noqa: BLE001 - a stale/corrupt pickle must not break scoring
                    log.warning("ignoring unreadable model cache %s: %s", path, exc)
    model = RugRiskModel(ml_config)
    if cache_dir is not None:
        try:
            model.save(Path(cache_dir))
        except OSError as exc:
            log.warning("could not persist model to %s: %s", cache_dir, exc)
    return model
