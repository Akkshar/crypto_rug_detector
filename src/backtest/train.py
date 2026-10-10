"""Train the ML layer on labelled snapshots instead of the synthetic prior.

``extract_features`` is shared with inference, so a model trained here scores
exactly the vectors it will see live. Cross-validation reports the numbers you
should actually believe (AUC, Brier) before the final model is fitted on all
rows and persisted with the ``labelled`` fingerprint, which ``get_model``
prefers over the synthetic one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from config.settings import RiskConfig
from src.backtest.harness import LabelledSnapshot, pr_auc, roc_auc
from src.models.ml_model import FEATURE_NAMES, RugRiskModel, extract_features, get_model

log = logging.getLogger("rugscan.train")

MIN_SAMPLES = 30
MIN_PER_CLASS = 8


@dataclass(frozen=True)
class CVMetrics:
    folds: int
    auc_mean: float | None
    auc_std: float | None
    accuracy: float
    brier: float  # mean squared error of P(rug) vs label; lower is better, 0.25 = coin flip
    n: int
    n_rug: int
    imputed_rows: int
    algorithm: str = "gbm"
    ap_mean: float | None = None  # PR-AUC across folds
    precision: float = 0.0  # at P(rug) >= 0.5, pooled out-of-fold
    recall: float = 0.0
    f1: float = 0.0
    false_negatives: int = 0  # rugs the model would have missed at 0.5

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_dataset(items: Iterable[LabelledSnapshot], config: RiskConfig) -> tuple[np.ndarray, np.ndarray, int]:
    rows, labels, imputed = [], [], 0
    for item in items:
        fv = extract_features(item.snapshot, config)
        rows.append([fv.values[n] for n in FEATURE_NAMES])
        labels.append(1.0 if item.is_rug else 0.0)
        imputed += bool(fv.imputed)
    return np.array(rows, dtype=float), np.array(labels, dtype=float), imputed


def cross_validate(
    X: np.ndarray,
    y: np.ndarray,
    config: RiskConfig,
    *,
    folds: int = 5,
    calibrate: str | None = None,
    imputed_rows: int = 0,
) -> CVMetrics:
    from sklearn.model_selection import StratifiedKFold

    folds = max(2, min(folds, int(min(y.sum(), (1 - y).sum()))))
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=config.ml.seed)
    aucs: list[float] = []
    aps: list[float] = []
    preds = np.zeros_like(y)
    for train_idx, test_idx in skf.split(X, y):
        model = RugRiskModel(config.ml, dataset=(X[train_idx], y[train_idx]), calibrate=calibrate)
        p = model.predict_proba_matrix(X[test_idx])
        preds[test_idx] = p
        fold_scores, fold_labels = list(map(float, p)), [bool(v) for v in y[test_idx]]
        auc, ap = roc_auc(fold_scores, fold_labels), pr_auc(fold_scores, fold_labels)
        if auc is not None:
            aucs.append(auc)
        if ap is not None:
            aps.append(ap)
    predicted = preds >= 0.5
    actual = y >= 0.5
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return CVMetrics(
        folds=folds,
        auc_mean=round(float(np.mean(aucs)), 4) if aucs else None,
        auc_std=round(float(np.std(aucs)), 4) if aucs else None,
        accuracy=round(float((predicted == actual).mean()), 4),
        brier=round(float(np.mean((preds - y) ** 2)), 4),
        n=len(y),
        n_rug=int(y.sum()),
        imputed_rows=imputed_rows,
        algorithm=config.ml.algorithm,
        ap_mean=round(float(np.mean(aps)), 4) if aps else None,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        false_negatives=fn,
    )


ALGORITHMS = ("logistic", "random_forest", "gbm")


def compare_models(
    items: list[LabelledSnapshot],
    config: RiskConfig,
    *,
    algorithms: tuple[str, ...] = ALGORITHMS,
    folds: int = 5,
    calibrate: str | None = None,
) -> list[CVMetrics]:
    """Cross-validate every classifier family on the same folds – the baseline comparison the evaluation needs."""
    X, y, imputed = build_dataset(items, config)
    out: list[CVMetrics] = []
    for algorithm in algorithms:
        cfg = config.model_copy(update={"ml": config.ml.model_copy(update={"algorithm": algorithm})})
        out.append(cross_validate(X, y, cfg, folds=folds, calibrate=calibrate, imputed_rows=imputed))
    return out


def train_model(
    items: list[LabelledSnapshot],
    config: RiskConfig,
    *,
    calibrate: str | None = "sigmoid",
    folds: int = 5,
    persist_dir: Path | None = None,
    algorithm: str | None = None,
) -> tuple[RugRiskModel, CVMetrics]:
    if algorithm is not None:
        config = config.model_copy(update={"ml": config.ml.model_copy(update={"algorithm": algorithm})})
    X, y, imputed = build_dataset(items, config)
    n_rug, n_ok = int(y.sum()), int(len(y) - y.sum())
    if len(y) < MIN_SAMPLES or min(n_rug, n_ok) < MIN_PER_CLASS:
        raise ValueError(
            f"need at least {MIN_SAMPLES} labelled snapshots with {MIN_PER_CLASS}+ per class "
            f"(have {len(y)}: {n_rug} rug / {n_ok} benign) – label more tokens first"
        )
    metrics = cross_validate(X, y, config, folds=folds, calibrate=calibrate, imputed_rows=imputed)
    model = RugRiskModel(config.ml, dataset=(X, y), calibrate=calibrate)
    if persist_dir is not None:
        path = model.save(Path(persist_dir))
        get_model.cache_clear()  # make the new labelled model visible to subsequent engines
        log.info("saved labelled model to %s", path)
    return model, metrics
