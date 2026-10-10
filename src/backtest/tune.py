"""Evidence-based re-weighting of ``risk_weights.yaml``.

Coordinate descent over flag points: for each flag that fires on the corpus,
try a handful of multipliers, keep the one that improves the objective (F1 at
the positive tier, optionally under a false-positive-rate ceiling), repeat for
a few passes. ML predictions do not depend on weights, so they are memoised
per snapshot and every evaluation is just heuristic re-aggregation.

The result is a *proposal*: review the diff, then copy it into place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config.settings import RiskConfig
from src.backtest.harness import LabelledSnapshot, ThresholdStats, threshold_stats
from src.models.risk_report import MLAssessment, RiskTier, TokenSnapshot
from src.models.scoring_engine import ScoringEngine

log = logging.getLogger("rugscan.tune")

DEFAULT_MULTIPLIERS = (0.0, 0.5, 0.75, 1.25, 1.5, 2.0)


class MemoisedEngine(ScoringEngine):
    """ScoringEngine whose ML refinement is cached per snapshot (weights don't change it)."""

    _ml_cache: dict[tuple[str, str, str], MLAssessment | None] = {}

    def refine(self, snapshot: TokenSnapshot) -> MLAssessment | None:
        key = (snapshot.chain.value, snapshot.address, snapshot.observed_at.isoformat())
        if key not in self._ml_cache:
            self._ml_cache[key] = super().refine(snapshot)
        return self._ml_cache[key]


@dataclass
class TuneStep:
    code: str
    before: float
    after: float
    objective: float


@dataclass
class TuneResult:
    baseline: ThresholdStats
    best: ThresholdStats
    baseline_objective: float
    best_objective: float
    weights: dict[str, float]
    changes: list[TuneStep] = field(default_factory=list)
    positive_tier: RiskTier = RiskTier.HIGH
    max_fp_rate: float | None = None

    @property
    def changed(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for step in self.changes:
            first = out.get(step.code, (step.before, step.after))[0]
            out[step.code] = (first, step.after)
        return {k: v for k, v in out.items() if v[0] != v[1]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "positive_tier": self.positive_tier.value,
            "max_fp_rate": self.max_fp_rate,
            "baseline": {**self.baseline.as_dict(), "objective": self.baseline_objective},
            "best": {**self.best.as_dict(), "objective": self.best_objective},
            "changed": {k: {"before": a, "after": b} for k, (a, b) in self.changed.items()},
            "weights": self.weights,
        }


#: Absolute candidates tried for a flag whose current weight is 0 (multipliers can't lift it off zero).
ZERO_CANDIDATES = (5.0, 10.0, 20.0, 30.0)


def _objective(stats: ThresholdStats, max_fp_rate: float | None, separation: float) -> float:
    """F1 at the positive tier, with mean score separation (rug − benign, in points) as a tiny tie-breaker.

    The tie-breaker is what lets coordinate descent make progress when no single change crosses the tier
    threshold: pushing rugs up / benign down is rewarded even before F1 moves. It is scaled so it can never
    outweigh a real F1 change (±100 points → ±0.01).
    """
    if max_fp_rate is not None:
        fp_rate = stats.fp / (stats.fp + stats.tn) if stats.fp + stats.tn else 0.0
        if fp_rate > max_fp_rate + 1e-9:
            return -1.0
    return stats.f1 + 1e-4 * separation


def tune_weights(
    config: RiskConfig,
    items: list[LabelledSnapshot],
    *,
    positive_tier: RiskTier = RiskTier.HIGH,
    max_fp_rate: float | None = None,
    multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS,
    passes: int = 2,
    model: Any = None,
) -> TuneResult:
    if not items:
        raise ValueError("no labelled snapshots to tune on")

    labels = [it.is_rug for it in items]
    tier_threshold = {
        RiskTier.LOW: 0.0,
        RiskTier.MEDIUM: config.tiers.medium,
        RiskTier.HIGH: config.tiers.high,
        RiskTier.CRITICAL: config.tiers.critical,
    }[positive_tier]

    def evaluate(weights: dict[str, float]) -> tuple[float, ThresholdStats]:
        engine = MemoisedEngine(config.model_copy(update={"flags": weights}), model=model)
        scores = [engine.score(it.snapshot, provider="tune").risk_score for it in items]
        stats = threshold_stats(scores, labels, tier_threshold)
        rug = [s for s, y in zip(scores, labels, strict=True) if y]
        benign = [s for s, y in zip(scores, labels, strict=True) if not y]
        separation = (sum(rug) / len(rug) - sum(benign) / len(benign)) if rug and benign else 0.0
        return _objective(stats, max_fp_rate, separation), stats

    weights = dict(config.flags)
    base_obj, base_stats = evaluate(weights)
    best_obj, best_stats = base_obj, base_stats

    # Only flags that actually fire can change anything (evaluated with every weight > 0 so zeroed flags count).
    probe = MemoisedEngine(config.model_copy(update={"flags": {k: 1.0 for k in config.flags}}), model=model)
    fired = sorted({fl.code for it in items for fl in probe.score(it.snapshot, provider="tune").flags})

    changes: list[TuneStep] = []
    for _ in range(passes):
        improved = False
        for code in fired:
            current = weights[code]
            best_for_code = current
            candidates = [current * m for m in multipliers] if current > 0 else list(ZERO_CANDIDATES)
            for raw in candidates:
                candidate = round(min(100.0, max(0.0, raw)), 1)
                if candidate == current:
                    continue
                trial = {**weights, code: candidate}
                obj, stats = evaluate(trial)
                if obj > best_obj + 1e-9:
                    best_obj, best_stats, best_for_code = obj, stats, candidate
            if best_for_code != current:
                weights[code] = best_for_code
                changes.append(TuneStep(code, current, best_for_code, best_obj))
                improved = True
                log.info("tune: %s %g → %g (objective %.4f)", code, current, best_for_code, best_obj)
        if not improved:
            break

    return TuneResult(
        baseline=base_stats,
        best=best_stats,
        baseline_objective=base_obj,
        best_objective=best_obj,
        weights=weights,
        changes=changes,
        positive_tier=positive_tier,
        max_fp_rate=max_fp_rate,
    )


def write_proposal(result: TuneResult, source_yaml: Path, destination: Path) -> Path:
    """Copy the source YAML with the tuned flag points. Comments are not preserved."""
    with Path(source_yaml).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    raw["flags"] = {code: (int(v) if float(v).is_integer() else v) for code, v in result.weights.items()}
    header = (
        "# Proposed by `rugscan tune` – review before adopting.\n"
        f"# objective {result.baseline_objective:.4f} → {result.best_objective:.4f} "
        f"at tier ≥ {result.positive_tier.value}; {len(result.changed)} flag(s) changed.\n"
    )
    destination = Path(destination)
    destination.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return destination
