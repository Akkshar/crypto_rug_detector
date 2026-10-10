"""Hybrid rule + ML scoring engine.

Pipeline for one ``TokenSnapshot``:

1. **Heuristics** – every registered engine produces flags with raw points.
2. **Pre-filter** – any heuristic reporting ``hard_fail`` (honeypot sell block,
   ≥90% tax) forces a CRITICAL verdict with a score floor.
3. **Weighted aggregation** – points are summed per category, clipped to that
   category's cap, then summed and clipped to 100 → ``rule_score``.
4. **ML refinement** – the synthetic-trained model contributes a rug
   probability, blended by ``scoring.ml_weight`` and down-weighted when the
   input is out-of-distribution or heavily imputed.
5. **Tiering** – LOW / MEDIUM / HIGH / CRITICAL from ``tiers`` thresholds.
"""

from __future__ import annotations

from collections import defaultdict

from config.settings import RiskConfig, load_risk_config
from src.heuristics import build_heuristics
from src.heuristics.base import BaseHeuristic
from src.models.ml_model import RugRiskModel, extract_features, get_model
from src.models.risk_report import (
    CategoryBreakdown,
    HeuristicResult,
    MLAssessment,
    RiskCategory,
    RiskFlag,
    RiskReport,
    RiskTier,
    TokenSnapshot,
)
from src.utils.logger import get_logger, log_flag, log_note

log = get_logger("scoring")


class ScoringEngine:
    def __init__(
        self,
        config: RiskConfig | None = None,
        heuristics: list[BaseHeuristic] | None = None,
        model: RugRiskModel | None = None,
    ):
        self.config = config or load_risk_config()
        self.heuristics = heuristics if heuristics is not None else build_heuristics(self.config)
        self.model: RugRiskModel | None
        if self.config.ml.enabled:
            self.model = model or get_model(self.config.ml)
        else:
            self.model = None

    # ------------------------------------------------------------------ #
    def run_heuristics(self, snapshot: TokenSnapshot) -> list[HeuristicResult]:
        results: list[HeuristicResult] = []
        for heuristic in self.heuristics:
            result = heuristic.evaluate(snapshot)
            log.info(
                "[bold]── %s[/bold] (%d flag(s), %g raw pts)", result.heuristic, len(result.flags), result.raw_points
            )
            for flag in result.flags:
                log_flag(log, flag.severity.value, flag.title, flag.points)
            for note in result.notes:
                log_note(log, note)
            if not result.data_available:
                log.warning("[yellow]\\[SKIP][/yellow] %s: insufficient data", result.heuristic)
            if result.hard_fail:
                log.error("[bold red]\\[HARD FAIL][/bold red] %s: %s", result.heuristic, result.hard_fail_reason)
            results.append(result)
        return results

    def aggregate(self, results: list[HeuristicResult]) -> tuple[float, list[CategoryBreakdown], list[RiskFlag]]:
        flags = [f for r in results for f in r.flags]
        raw: dict[RiskCategory, float] = defaultdict(float)
        counts: dict[RiskCategory, int] = defaultdict(int)
        for flag in flags:
            raw[flag.category] += flag.points
            counts[flag.category] += 1

        breakdown: list[CategoryBreakdown] = []
        total = 0.0
        for category in RiskCategory:
            cap = self.config.cap(category)
            capped = min(cap, raw[category])
            if raw[category] > cap:
                log.info("[dim]cap[/dim] %s: %g raw → %g (cap %g)", category.value, raw[category], capped, cap)
            total += capped
            breakdown.append(
                CategoryBreakdown(
                    category=category,
                    raw_points=raw[category],
                    capped_points=capped,
                    cap=cap,
                    flag_count=counts[category],
                )
            )
        rule_score = min(100.0, total)
        log.info("[bold]Rule-based score: %.1f[/bold] (sum of capped categories = %g)", rule_score, total)
        return rule_score, breakdown, flags

    def refine(self, snapshot: TokenSnapshot) -> MLAssessment | None:
        if self.model is None:
            return None
        fv = extract_features(snapshot, self.config)
        pred = self.model.predict(fv)
        reliable = not pred.is_out_of_distribution and len(fv.imputed) <= self.config.ml.max_imputed_features
        assessment = MLAssessment(
            model=self.model.name,
            rug_probability=pred.rug_probability,
            anomaly_score=pred.anomaly_score,
            is_out_of_distribution=pred.is_out_of_distribution,
            imputed_features=list(fv.imputed),
            features=fv.values,
            reliable=reliable,
        )
        log.info(
            "[bold]ML refinement:[/bold] rug_probability=%.2f anomaly=%.3f ood=%s imputed=%s",
            pred.rug_probability,
            pred.anomaly_score,
            pred.is_out_of_distribution,
            list(fv.imputed) or "none",
        )
        if not reliable:
            log.warning(
                "[yellow]ML output marked unreliable – weight reduced ×%g[/yellow]",
                self.config.scoring.unreliable_ml_factor,
            )
        return assessment

    def combine(self, rule_score: float, ml: MLAssessment | None, hard_fail: bool) -> tuple[float, RiskTier]:
        s = self.config.scoring
        if ml is None:
            final = rule_score
        else:
            w_ml = s.ml_weight * (1.0 if ml.reliable else s.unreliable_ml_factor)
            final = (1.0 - w_ml) * rule_score + w_ml * ml.rug_probability * 100.0
            log.info(
                "Blend: %.2f×rule(%.1f) + %.2f×ml(%.1f) = %.1f",
                1 - w_ml,
                rule_score,
                w_ml,
                ml.rug_probability * 100,
                final,
            )
        if hard_fail:
            floored = max(final, s.hard_fail_score_floor)
            if floored != final:
                log.info("Pre-filter floor applied: %.1f → %.1f", final, floored)
            final = floored
        final = round(min(100.0, max(0.0, final)), 1)
        tier = RiskTier.CRITICAL if hard_fail else self.tier_for(final)
        return final, tier

    def tier_for(self, score: float) -> RiskTier:
        t = self.config.tiers
        if score >= t.critical:
            return RiskTier.CRITICAL
        if score >= t.high:
            return RiskTier.HIGH
        if score >= t.medium:
            return RiskTier.MEDIUM
        return RiskTier.LOW

    # ------------------------------------------------------------------ #
    def score(self, snapshot: TokenSnapshot, provider: str = "unknown") -> RiskReport:
        log.info("[bold cyan]Scoring %s on %s[/bold cyan]", snapshot.address, snapshot.chain.value)
        results = self.run_heuristics(snapshot)
        hard_fail_reasons = [r.hard_fail_reason or r.heuristic for r in results if r.hard_fail]
        hard_fail = bool(hard_fail_reasons)

        rule_score, breakdown, flags = self.aggregate(results)
        ml = self.refine(snapshot)
        final, tier = self.combine(rule_score, ml, hard_fail)

        warnings = [f"PRE-FILTER HARD FAIL: {reason}" for reason in hard_fail_reasons]
        warnings += [f"[{f.severity.value}] {f.title} — {f.detail}" for f in sorted(flags, key=lambda f: -f.points)]

        log.info("[bold]Final: %s  %.1f/100[/bold]", tier.value, final)
        return RiskReport(
            chain=snapshot.chain,
            address=snapshot.address,
            token_name=snapshot.contract.name,
            token_symbol=snapshot.contract.symbol,
            provider=provider,
            risk_score=final,
            risk_tier=tier,
            rule_score=rule_score,
            ml=ml,
            hard_fail=hard_fail,
            hard_fail_reasons=hard_fail_reasons,
            breakdown=breakdown,
            flags=flags,
            warnings=warnings,
            missing_data=[r.heuristic for r in results if not r.data_available],
        )
