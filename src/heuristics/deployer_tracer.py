"""Deployer-graph heuristics: funding origin, past rugs, launch cadence, wallet age."""

from __future__ import annotations

from src.heuristics.base import BaseHeuristic, lt
from src.models.risk_report import (
    FundingSourceKind,
    HeuristicResult,
    RiskCategory,
    RiskFlag,
    Severity,
    TokenSnapshot,
)


class DeployerTracer(BaseHeuristic):
    name = "deployer_tracer"
    category = RiskCategory.DEPLOYER

    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        d = snapshot.deployer
        if d is None:
            return self._result([], data_available=False, notes=["No deployer profile available"])

        flags: list[RiskFlag] = []
        notes: list[str] = []
        src = d.first_funding_label or d.first_funding_source or "unknown source"

        if d.first_funding_kind == FundingSourceKind.MIXER:
            flags.append(self._flag(
                "DEPLOYER_MIXER_FUNDED", Severity.CRITICAL, "Deployer funded from a mixer",
                f"First gas funding for {d.address} came from {src} – identity deliberately obscured",
                funding_source=d.first_funding_source, label=d.first_funding_label,
            ))
        elif d.first_funding_kind == FundingSourceKind.PRIVACY_EXCHANGE:
            flags.append(self._flag(
                "DEPLOYER_PRIVACY_EXCHANGE_FUNDED", Severity.HIGH, "Deployer funded via a no-KYC swap service",
                f"First gas funding for {d.address} came from {src}",
                funding_source=d.first_funding_source, label=d.first_funding_label,
            ))
        elif d.first_funding_kind == FundingSourceKind.UNKNOWN:
            notes.append("Deployer funding origin unknown")
        else:
            notes.append(f"Deployer funded from {d.first_funding_kind.value}: {src}")

        prior = d.prior_rug_count or 0
        linked = len(d.linked_scam_addresses)
        if prior > 0 or linked > 0:
            flags.append(self._flag(
                "DEPLOYER_PRIOR_RUGS", Severity.CRITICAL, f"Deployer linked to {max(prior, linked)} prior rug(s)",
                f"{d.address} deployed {prior} token(s) later flagged as rugs and is linked to {linked} known scam address(es)",
                prior_rug_count=prior, linked_scam_addresses=list(d.linked_scam_addresses),
            ))

        if d.contracts_deployed is not None and d.contracts_deployed >= self.t.serial_launcher_count:
            flags.append(self._flag(
                "DEPLOYER_SERIAL_LAUNCHER", Severity.MEDIUM, f"Serial launcher ({d.contracts_deployed} contracts)",
                f"Deployer has deployed {d.contracts_deployed} contracts (≥ {self.t.serial_launcher_count}) – token-farm pattern",
                contracts_deployed=d.contracts_deployed,
            ))

        age = d.age_days(snapshot.observed_at)
        if age is None:
            notes.append("Deployer wallet age unknown")
        elif lt(age, self.t.fresh_deployer_days):
            flags.append(self._flag(
                "DEPLOYER_FRESH_WALLET", Severity.MEDIUM, f"Fresh deployer wallet ({age:.1f} days old)",
                f"Deployer first seen {age:.1f} days before observation (< {self.t.fresh_deployer_days:g}) – no track record",
                age_days=round(age, 2),
            ))
        else:
            notes.append(f"Deployer wallet age {age:.0f} days")

        return self._result(flags, notes=notes)
