"""Holder-distribution heuristics: top-10 concentration and single-whale dominance.

Shares are measured against *circulating* supply, i.e. excluding DEX pairs and
burn addresses, so a token with 40% of supply sitting in its own pool is not
penalised for that.
"""

from __future__ import annotations

from src.heuristics.base import BaseHeuristic, ge
from src.models.risk_report import HeuristicResult, RiskCategory, RiskFlag, Severity, TokenSnapshot


class HolderMetrics(BaseHeuristic):
    name = "holder_metrics"
    category = RiskCategory.DISTRIBUTION

    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        dist = snapshot.holders
        if not dist.top_holders:
            return self._result([], data_available=False, notes=["No holder distribution available"])

        circulating = dist.circulating_share()
        if circulating <= 0:
            return self._result([], data_available=False, notes=["Entire tracked supply is in DEX/burn addresses"])

        top10 = dist.circulating_top_n(10)
        concentration = min(1.0, sum(h.share for h in top10) / circulating)
        flags: list[RiskFlag] = []
        notes: list[str] = []
        evidence = {
            "top10_concentration": round(concentration, 4),
            "circulating_share": round(circulating, 4),
            "holder_count": dist.holder_count,
        }

        if ge(concentration, self.t.top10_high):
            flags.append(self._flag(
                "TOP10_CONCENTRATION_HIGH", Severity.HIGH, f"Top-10 wallets hold {concentration:.0%} of circulating supply",
                f"Excluding DEX/burn, 10 wallets control {concentration:.0%} (≥ {self.t.top10_high:.0%}) – coordinated dump risk",
                **evidence,
            ))
        elif ge(concentration, self.t.top10_elevated):
            flags.append(self._flag(
                "TOP10_CONCENTRATION_ELEVATED", Severity.MEDIUM, f"Top-10 wallets hold {concentration:.0%} of circulating supply",
                f"Excluding DEX/burn, 10 wallets control {concentration:.0%} (≥ {self.t.top10_elevated:.0%})",
                **evidence,
            ))
        else:
            notes.append(f"Top-10 concentration {concentration:.0%} of circulating supply – dispersed")

        if top10:
            whale = top10[0]
            whale_share = min(1.0, whale.share / circulating)
            if ge(whale_share, self.t.single_whale):
                label = f" ({whale.label})" if whale.label else ""
                flags.append(self._flag(
                    "SINGLE_WHALE_DOMINANT", Severity.MEDIUM, f"Single wallet holds {whale_share:.0%} of circulating supply",
                    f"{whale.address}{label} alone controls {whale_share:.0%} of non-DEX supply",
                    whale=whale.address, whale_share=round(whale_share, 4), label=whale.label,
                ))
        return self._result(flags, notes=notes)
