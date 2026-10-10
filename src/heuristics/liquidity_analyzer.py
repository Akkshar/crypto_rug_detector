"""Liquidity & trading heuristics.

* LP lock verification – share of LP tokens in recognised lockers / burned vs.
  held by the deployer or owner, plus lock expiry.
* Liquidity depth vs. FDV imbalance.
* Sell-simulation review – success rate, hidden tax, abnormal gas. Zero
  successful sells (or a measured sell loss above the hard-fail tax) trips the
  honeypot pre-filter.
"""

from __future__ import annotations

from datetime import timezone

from src.heuristics.base import BaseHeuristic, ge, gt, lt
from src.models.risk_report import (
    HeuristicResult,
    LiquidityPool,
    RiskCategory,
    RiskFlag,
    Severity,
    TokenSnapshot,
)


class LiquidityAnalyzer(BaseHeuristic):
    name = "liquidity_analyzer"
    category = RiskCategory.LIQUIDITY

    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        flags: list[RiskFlag] = []
        notes: list[str] = []
        hard_fail_reasons: list[str] = []

        pool = snapshot.primary_pool
        if pool is None:
            flags.append(
                self._flag(
                    "NO_LIQUIDITY_POOL",
                    Severity.HIGH,
                    "No liquidity pool found",
                    "No DEX pool detected – token is untradeable or liquidity was already pulled",
                )
            )
        else:
            flags.extend(self._lp_lock_flags(snapshot, pool, notes))
            flags.extend(self._depth_flags(snapshot, notes))

        flags.extend(self._sell_sim_flags(snapshot, notes, hard_fail_reasons))

        return self._result(
            flags,
            hard_fail=bool(hard_fail_reasons),
            hard_fail_reason="; ".join(hard_fail_reasons) or None,
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    def _lp_lock_flags(self, snapshot: TokenSnapshot, pool: LiquidityPool, notes: list[str]) -> list[RiskFlag]:
        flags: list[RiskFlag] = []
        if not pool.lp_holdings:
            notes.append(f"{pool.dex} LP holder distribution unavailable – lock status unknown")
            return flags

        unlocked = 1.0 - pool.locked_share
        lockers = sorted({h.locker.value for h in pool.lp_holdings if h.locker})
        if gt(unlocked, self.t.lp_mostly_unlocked_ratio):
            flags.append(
                self._flag(
                    "LP_MOSTLY_UNLOCKED",
                    Severity.CRITICAL,
                    f"LP mostly unlocked ({unlocked:.0%})",
                    f"Only {pool.locked_share:.0%} of {pool.dex} LP is locked/burned – liquidity can be pulled at any time",
                    dex=pool.dex,
                    locked_share=pool.locked_share,
                    lockers=lockers,
                )
            )
        elif gt(unlocked, self.t.lp_partially_unlocked_ratio):
            flags.append(
                self._flag(
                    "LP_PARTIALLY_UNLOCKED",
                    Severity.MEDIUM,
                    f"LP partially unlocked ({unlocked:.0%})",
                    f"{pool.locked_share:.0%} of {pool.dex} LP is locked/burned ({', '.join(lockers) or 'none'})",
                    dex=pool.dex,
                    locked_share=pool.locked_share,
                    lockers=lockers,
                )
            )
        else:
            notes.append(f"{pool.locked_share:.0%} of {pool.dex} LP locked/burned via {', '.join(lockers)}")

        if gt(pool.insider_share, self.t.lp_insider_ratio):
            flags.append(
                self._flag(
                    "LP_HELD_BY_INSIDER",
                    Severity.HIGH,
                    f"Deployer/owner holds {pool.insider_share:.0%} of LP",
                    "Unlocked LP in insider wallets is the direct mechanism for a liquidity-drain exit",
                    insider_share=pool.insider_share,
                )
            )

        now = snapshot.observed_at if snapshot.observed_at.tzinfo else snapshot.observed_at.replace(tzinfo=timezone.utc)
        for holding in pool.lp_holdings:
            if holding.locker is None or holding.unlock_at is None:
                continue
            unlock = holding.unlock_at if holding.unlock_at.tzinfo else holding.unlock_at.replace(tzinfo=timezone.utc)
            days_left = (unlock - now).total_seconds() / 86_400
            if lt(days_left, self.t.lp_lock_min_days):
                flags.append(
                    self._flag(
                        "LP_LOCK_EXPIRING",
                        Severity.MEDIUM,
                        f"LP lock expires in {max(days_left, 0):.0f} days",
                        f"{holding.share:.0%} of LP in {holding.locker.value} unlocks {unlock.date().isoformat()}",
                        locker=holding.locker.value,
                        days_left=round(days_left, 1),
                        share=holding.share,
                    )
                )
        return flags

    def _depth_flags(self, snapshot: TokenSnapshot, notes: list[str]) -> list[RiskFlag]:
        flags: list[RiskFlag] = []
        liquidity = snapshot.total_liquidity_usd
        if lt(liquidity, self.t.low_liquidity_usd):
            flags.append(
                self._flag(
                    "LOW_LIQUIDITY",
                    Severity.MEDIUM,
                    f"Thin liquidity (${liquidity:,.0f})",
                    f"Total pooled liquidity is below ${self.t.low_liquidity_usd:,.0f}; exits move price violently",
                    liquidity_usd=liquidity,
                )
            )

        fdv = snapshot.fdv_usd
        if fdv is None:
            notes.append("FDV unknown (missing price or supply) – depth ratio skipped")
        elif liquidity > 0:
            ratio = fdv / liquidity
            if ge(ratio, self.t.fdv_liquidity_ratio_extreme):
                flags.append(
                    self._flag(
                        "FDV_LIQUIDITY_EXTREME",
                        Severity.HIGH,
                        f"FDV is {ratio:,.0f}× liquidity",
                        f"FDV ${fdv:,.0f} vs ${liquidity:,.0f} pooled – valuation is almost entirely unbacked",
                        fdv_usd=fdv,
                        liquidity_usd=liquidity,
                        ratio=round(ratio, 1),
                    )
                )
            elif ge(ratio, self.t.fdv_liquidity_ratio_warn):
                flags.append(
                    self._flag(
                        "FDV_LIQUIDITY_IMBALANCE",
                        Severity.MEDIUM,
                        f"FDV is {ratio:,.0f}× liquidity",
                        f"FDV ${fdv:,.0f} vs ${liquidity:,.0f} pooled – inflated valuation relative to depth",
                        fdv_usd=fdv,
                        liquidity_usd=liquidity,
                        ratio=round(ratio, 1),
                    )
                )
            else:
                notes.append(f"FDV/liquidity ratio {ratio:.1f}× within normal range")
        return flags

    def _sell_sim_flags(
        self, snapshot: TokenSnapshot, notes: list[str], hard_fail_reasons: list[str]
    ) -> list[RiskFlag]:
        flags: list[RiskFlag] = []
        sim = snapshot.sell_simulation
        if sim is None or sim.attempted == 0:
            flags.append(
                self._flag(
                    "SELL_SIMULATION_UNAVAILABLE",
                    Severity.INFO,
                    "Sell path not simulated",
                    "No buy→sell simulation available; honeypot status could not be verified",
                    category=RiskCategory.HONEYPOT,
                )
            )
            return flags

        rate = sim.success_rate or 0.0
        if sim.succeeded == 0:
            reason = sim.revert_reason or "no revert reason"
            flags.append(
                self._flag(
                    "HONEYPOT_SELL_BLOCKED",
                    Severity.CRITICAL,
                    "Sell path blocked (0% success)",
                    f"0/{sim.attempted} simulated sells succeeded – revert: {reason}",
                    category=RiskCategory.HONEYPOT,
                    attempted=sim.attempted,
                    revert_reason=sim.revert_reason,
                )
            )
            hard_fail_reasons.append(f"honeypot: 0/{sim.attempted} simulated sells succeeded")
        elif lt(rate, self.t.min_sell_success_rate):
            flags.append(
                self._flag(
                    "LOW_SELL_SUCCESS_RATE",
                    Severity.HIGH,
                    f"Sell success rate {rate:.0%}",
                    f"{sim.succeeded}/{sim.attempted} simulated sells succeeded (< {self.t.min_sell_success_rate:.0%})",
                    category=RiskCategory.HONEYPOT,
                    success_rate=rate,
                )
            )
        else:
            notes.append(f"Sell simulation OK: {sim.succeeded}/{sim.attempted} succeeded")

        declared = snapshot.contract.sell_tax_pct
        measured = sim.effective_sell_tax_pct
        if measured is not None and declared is not None:
            gap = measured - declared
            if gt(gap, self.t.hidden_tax_tolerance_pct):
                flags.append(
                    self._flag(
                        "HIDDEN_SELL_TAX",
                        Severity.HIGH,
                        f"Hidden sell tax (+{gap:g}% over declared)",
                        f"Simulated sell lost {measured:g}% vs declared {declared:g}% – undeclared fee logic",
                        category=RiskCategory.HONEYPOT,
                        declared_pct=declared,
                        measured_pct=measured,
                    )
                )
        if measured is not None and ge(measured, self.config.prefilter.hard_fail_tax_pct):
            hard_fail_reasons.append(f"measured sell loss {measured:g}% ≥ {self.config.prefilter.hard_fail_tax_pct:g}%")

        if sim.sell_gas is not None:
            ratio = sim.sell_gas / sim.buy_gas if sim.buy_gas else None
            if sim.sell_gas > self.t.max_sell_gas or (ratio is not None and gt(ratio, self.t.sell_gas_ratio)):
                flags.append(
                    self._flag(
                        "EXCESSIVE_SELL_GAS",
                        Severity.MEDIUM,
                        f"Sell gas abnormal ({sim.sell_gas:,})",
                        "Sell path consumes far more gas than buy – hidden loops/gas-griefing can make sells fail on-chain",
                        category=RiskCategory.HONEYPOT,
                        sell_gas=sim.sell_gas,
                        buy_gas=sim.buy_gas,
                    )
                )
        return flags
