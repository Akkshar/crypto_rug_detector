"""Backtest the engine against labelled snapshots.

Answers the questions that decide whether the detector is *right*, not just
running:

* How well does the composite score rank rugs above benign tokens? (ROC-AUC
  for the final score, the rule-only score and the ML probability separately,
  so you can see which half is pulling its weight.)
* At the configured tier cut-off, what are precision / recall / F1, and which
  threshold would maximise F1?
* Per flag: how often it fires, P(rug | flag) and lift over the base rate –
  the data needed to re-weight ``risk_weights.yaml`` with evidence.

Labels come from the snapshot cache (``rugscan label``) or a JSONL export.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from src.models.risk_report import RiskReport, RiskTier, TokenSnapshot
from src.models.scoring_engine import ScoringEngine
from src.storage.snapshot_cache import SnapshotCache

log = logging.getLogger("rugscan.backtest")

TIER_ORDER = [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL]
RUG_LABELS = {"rug", "scam", "honeypot", "1", "true", "positive"}
BENIGN_LABELS = {"benign", "legit", "safe", "0", "false", "negative"}


@dataclass(frozen=True)
class LabelledSnapshot:
    snapshot: TokenSnapshot
    is_rug: bool
    source: str = "cache"


def parse_label(label: str | None) -> bool | None:
    if label is None:
        return None
    norm = label.strip().lower()
    if norm in RUG_LABELS:
        return True
    if norm in BENIGN_LABELS:
        return False
    return None


def load_labelled(
    *, cache: SnapshotCache | None = None, jsonl: Path | str | None = None, latest_only: bool = True
) -> list[LabelledSnapshot]:
    items: list[LabelledSnapshot] = []
    if cache is not None:
        for snapshot, _provider, label in cache.iter_snapshots(labelled_only=True):
            verdict = parse_label(label)
            if verdict is not None:
                items.append(LabelledSnapshot(snapshot, verdict, "cache"))
    if jsonl is not None:
        with Path(jsonl).open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                verdict = parse_label(record.get("label"))
                if verdict is None:
                    continue
                items.append(LabelledSnapshot(TokenSnapshot.model_validate(record["snapshot"]), verdict, "jsonl"))
    if latest_only:
        newest: dict[tuple[str, str], LabelledSnapshot] = {}
        for item in items:
            key = (item.snapshot.chain.value, item.snapshot.address)
            if key not in newest or item.snapshot.observed_at > newest[key].snapshot.observed_at:
                newest[key] = item
        items = list(newest.values())
    return items


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def roc_auc(scores: list[float], labels: list[bool]) -> float | None:
    """Rank-based AUC (Mann–Whitney). None when one class is missing."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y]
    neg = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


@dataclass(frozen=True)
class ThresholdStats:
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            **asdict(self),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def threshold_stats(scores: list[float], labels: list[bool], threshold: float) -> ThresholdStats:
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y)
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and not y)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < threshold and y)
    tn = len(scores) - tp - fp - fn
    return ThresholdStats(threshold, tp, fp, fn, tn)


@dataclass(frozen=True)
class FlagStats:
    code: str
    fires: int
    rug_hits: int
    precision: float  # P(rug | flag)
    recall: float  # P(flag | rug)
    lift: float  # precision / base rate
    points: float


@dataclass
class BacktestResult:
    n: int
    n_rug: int
    base_rate: float
    auc_score: float | None
    auc_rule: float | None
    auc_ml: float | None
    positive_tier: RiskTier
    at_tier: ThresholdStats
    best_f1: ThresholdStats
    tier_counts: dict[str, dict[str, int]] = field(default_factory=dict)  # tier -> {"rug": n, "benign": n}
    flags: list[FlagStats] = field(default_factory=list)
    hard_fail: ThresholdStats | None = None
    worst_misses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "n": self.n,
            "n_rug": self.n_rug,
            "base_rate": round(self.base_rate, 4),
            "auc": {"score": self.auc_score, "rule": self.auc_rule, "ml": self.auc_ml},
            "positive_tier": self.positive_tier.value,
            "at_tier": self.at_tier.as_dict(),
            "best_f1": self.best_f1.as_dict(),
            "tier_counts": self.tier_counts,
            "flags": [asdict(f) for f in self.flags],
            "hard_fail": self.hard_fail.as_dict() if self.hard_fail else None,
            "worst_misses": self.worst_misses,
        }


def run_backtest(
    engine: ScoringEngine, items: Iterable[LabelledSnapshot], *, positive_tier: RiskTier = RiskTier.HIGH
) -> BacktestResult:
    items = list(items)
    if not items:
        raise ValueError("no labelled snapshots to backtest")

    reports: list[RiskReport] = [engine.score(it.snapshot, provider="backtest") for it in items]
    labels = [it.is_rug for it in items]
    scores = [r.risk_score for r in reports]
    rule_scores = [r.rule_score for r in reports]
    ml_scores = [r.ml.rug_probability if r.ml else 0.0 for r in reports]
    n_rug = sum(labels)
    base_rate = n_rug / len(items)

    tier_threshold = {
        RiskTier.LOW: 0.0,
        RiskTier.MEDIUM: engine.config.tiers.medium,
        RiskTier.HIGH: engine.config.tiers.high,
        RiskTier.CRITICAL: engine.config.tiers.critical,
    }[positive_tier]
    at_tier = threshold_stats(scores, labels, tier_threshold)
    candidates = sorted({*scores, tier_threshold})
    best = max((threshold_stats(scores, labels, t) for t in candidates), key=lambda s: (s.f1, -s.threshold))

    tier_counts: dict[str, dict[str, int]] = {t.value: {"rug": 0, "benign": 0} for t in TIER_ORDER}
    for r, y in zip(reports, labels, strict=True):
        tier_counts[r.risk_tier.value]["rug" if y else "benign"] += 1

    flag_fires: dict[str, list[bool]] = {}
    for r, y in zip(reports, labels, strict=True):
        for code in {f.code for f in r.flags}:
            flag_fires.setdefault(code, []).append(y)
    flag_stats = []
    for code, hits in flag_fires.items():
        fires, rug_hits = len(hits), sum(hits)
        precision = rug_hits / fires
        flag_stats.append(
            FlagStats(
                code=code,
                fires=fires,
                rug_hits=rug_hits,
                precision=round(precision, 4),
                recall=round(rug_hits / n_rug, 4) if n_rug else 0.0,
                lift=round(precision / base_rate, 3) if base_rate else 0.0,
                points=engine.config.points(code),
            )
        )
    flag_stats.sort(key=lambda f: (-f.lift, -f.fires))

    hard = [1.0 if r.hard_fail else 0.0 for r in reports]
    hard_fail = threshold_stats(hard, labels, 1.0) if any(hard) else None

    misses: list[dict[str, Any]] = []
    for it, r, y in zip(items, reports, labels, strict=True):
        predicted = r.risk_score >= tier_threshold
        if predicted != y:
            misses.append(
                {
                    "address": it.snapshot.address,
                    "chain": it.snapshot.chain.value,
                    "label": "rug" if y else "benign",
                    "score": r.risk_score,
                    "tier": r.risk_tier.value,
                    "flags": [f.code for f in r.flags][:6],
                }
            )
    misses.sort(key=lambda m: -abs(float(m["score"]) - tier_threshold))

    return BacktestResult(
        n=len(items),
        n_rug=n_rug,
        base_rate=base_rate,
        auc_score=_r(roc_auc(scores, labels)),
        auc_rule=_r(roc_auc(rule_scores, labels)),
        auc_ml=_r(roc_auc(ml_scores, labels)),
        positive_tier=positive_tier,
        at_tier=at_tier,
        best_f1=best,
        tier_counts=tier_counts,
        flags=flag_stats,
        hard_fail=hard_fail,
        worst_misses=misses[:10],
    )


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def render_backtest(result: BacktestResult, console: Console | None = None) -> None:
    console = console or Console()
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
    summary = Table(title=f"Backtest – {result.n} tokens ({result.n_rug} rugs, base rate {result.base_rate:.0%})")
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("AUC – final score", fmt(result.auc_score))
    summary.add_row("AUC – rule score only", fmt(result.auc_rule))
    summary.add_row("AUC – ML probability only", fmt(result.auc_ml))
    t = result.at_tier
    summary.add_row(
        f"At tier ≥ {result.positive_tier.value} (score ≥ {t.threshold:g})",
        f"P {t.precision:.2f}  R {t.recall:.2f}  F1 {t.f1:.2f}  (TP {t.tp} FP {t.fp} FN {t.fn} TN {t.tn})",
    )
    b = result.best_f1
    summary.add_row(
        f"Best F1 threshold (score ≥ {b.threshold:g})", f"P {b.precision:.2f}  R {b.recall:.2f}  F1 {b.f1:.2f}"
    )
    if result.hard_fail:
        h = result.hard_fail
        summary.add_row("Pre-filter hard fails", f"P {h.precision:.2f}  R {h.recall:.2f}  ({h.tp} TP / {h.fp} FP)")
    console.print(summary)

    tiers = Table(title="Verdicts by tier")
    tiers.add_column("Tier")
    tiers.add_column("Rugs", justify="right")
    tiers.add_column("Benign", justify="right")
    for tier, counts in result.tier_counts.items():
        tiers.add_row(tier, str(counts["rug"]), str(counts["benign"]))
    console.print(tiers)

    flags = Table(title="Flag diagnostics (sorted by lift)")
    for col, just in (
        ("Flag", "left"),
        ("Fires", "right"),
        ("P(rug|flag)", "right"),
        ("Recall", "right"),
        ("Lift", "right"),
        ("Points", "right"),
    ):
        flags.add_column(col, justify=just)  # type: ignore[arg-type]
    for f in result.flags:
        flags.add_row(f.code, str(f.fires), f"{f.precision:.2f}", f"{f.recall:.2f}", f"{f.lift:.2f}", f"{f.points:g}")
    console.print(flags)

    if result.worst_misses:
        misses = Table(title="Largest misclassifications")
        for col in ("Address", "Label", "Score", "Tier", "Flags"):
            misses.add_column(col)
        for m in result.worst_misses:
            misses.add_row(
                str(m["address"]), str(m["label"]), f"{m['score']:.1f}", str(m["tier"]), ", ".join(m["flags"])
            )
        console.print(misses)
