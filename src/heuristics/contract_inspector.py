"""Smart-contract & privilege heuristics.

Covers ownership state, unbounded fee / limit setters, mint capabilities,
blacklist / pause / whitelist gates, proxy upgrade hazards and the *declared*
buy & sell taxes. Declared taxes at or above ``prefilter.hard_fail_tax_pct``
trip the pre-filter and force a CRITICAL verdict.
"""

from __future__ import annotations

from src.heuristics.base import BaseHeuristic, ge, gt
from src.models.risk_report import (
    FunctionSignature,
    HeuristicResult,
    OwnerType,
    ProxyKind,
    RiskCategory,
    RiskFlag,
    Severity,
    TokenSnapshot,
)

# Roles that imply a governance process rather than a single key.
GOVERNANCE_ROLES = frozenset({"timelock", "governance", "governor", "dao", "onlytimelock", "onlygovernance"})
UPGRADEABLE_PROXIES = frozenset({ProxyKind.TRANSPARENT, ProxyKind.UUPS, ProxyKind.BEACON, ProxyKind.UNKNOWN})


class ContractInspector(BaseHeuristic):
    name = "contract_inspector"
    category = RiskCategory.CONTRACT_PRIVILEGE

    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        c = snapshot.contract
        flags: list[RiskFlag] = []
        notes: list[str] = []
        hard_fail_reasons: list[str] = []

        # --- ownership ------------------------------------------------- #
        renounced = bool(c.ownership_renounced)
        if renounced:
            notes.append("Ownership renounced – owner-gated functions are inert")
        elif c.owner_type == OwnerType.EOA:
            flags.append(
                self._flag(
                    "OWNER_ACTIVE_EOA",
                    Severity.HIGH,
                    "Owner is an active EOA",
                    f"owner() = {c.owner} is a single externally-owned key with full privileged access",
                    owner=c.owner,
                )
            )
        elif c.owner_type == OwnerType.CONTRACT:
            flags.append(
                self._flag(
                    "OWNER_CONTRACT_NO_TIMELOCK",
                    Severity.MEDIUM,
                    "Owner is a non-timelocked contract",
                    f"owner() = {c.owner} is a contract with no recognised timelock – upgrades/changes can be instant",
                    owner=c.owner,
                )
            )
        elif c.owner_type == OwnerType.MULTISIG:
            flags.append(
                self._flag(
                    "OWNER_MULTISIG",
                    Severity.LOW,
                    "Owner is a multisig",
                    f"owner() = {c.owner} is a multisig; privileged actions still possible but require quorum",
                    owner=c.owner,
                )
            )
        elif c.owner_type == OwnerType.TIMELOCK:
            notes.append(f"Owner {c.owner} is a timelock – privileged changes are delayed and visible")
        else:
            notes.append("Owner unknown – ownership heuristics skipped")

        # --- source verification --------------------------------------- #
        if not c.source_verified:
            flags.append(
                self._flag(
                    "SOURCE_UNVERIFIED",
                    Severity.MEDIUM,
                    "Source code not verified",
                    "Contract source is unverified; capability analysis relies on bytecode probing only",
                )
            )

        # --- fee / limit setters --------------------------------------- #
        unbounded_fees = [f for f in c.fee_setters if self._is_unbounded(f)]
        if unbounded_fees and not renounced:
            names = ", ".join(self._describe(f) for f in unbounded_fees)
            flags.append(
                self._flag(
                    "UNBOUNDED_FEE_SETTER",
                    Severity.HIGH,
                    "Fee setter without a safe upper bound",
                    f"{names} can raise taxes above {self.t.max_reasonable_fee_pct:g}% (potentially to 100%) after launch",
                    setters=[f.name for f in unbounded_fees],
                )
            )
        elif unbounded_fees and renounced:
            notes.append(f"Unbounded fee setters present ({len(unbounded_fees)}) but ownership is renounced")

        unbounded_limits = [f for f in c.max_tx_setters if self._is_unbounded(f)]
        if unbounded_limits and not renounced:
            flags.append(
                self._flag(
                    "UNBOUNDED_MAX_TX_SETTER",
                    Severity.MEDIUM,
                    "Max-tx / max-wallet setter without bounds",
                    f"{', '.join(f.name for f in unbounded_limits)} can shrink the transfer limit to block sells",
                    setters=[f.name for f in unbounded_limits],
                )
            )

        # --- minting --------------------------------------------------- #
        # One flag per *capability*, not per function: a contract exposing mint(address,uint256) and
        # mint(uint256) has one problem, not two (a live scan of CAKE double-counted this).
        hidden_mints: list[FunctionSignature] = []
        privileged_mints: list[FunctionSignature] = []
        for fn in c.mint_functions:
            role = (fn.restricted_to or "").lower()
            if fn.hidden:
                hidden_mints.append(fn)
            elif role in GOVERNANCE_ROLES:
                notes.append(f"{fn.name}() is mint-capable but gated by governance role '{fn.restricted_to}'")
            elif renounced and role in {"onlyowner", "owner"}:
                notes.append(f"{fn.name}() is owner-gated and ownership is renounced – inert")
            else:
                privileged_mints.append(fn)
        if hidden_mints:
            names = ", ".join(f"{fn.name}()" for fn in hidden_mints)
            flags.append(
                self._flag(
                    "HIDDEN_MINT_FUNCTION",
                    Severity.CRITICAL,
                    "Hidden supply-expansion function",
                    f"{names} not in the published ABI but can expand supply "
                    f"(guard: {hidden_mints[0].restricted_to or 'none'})",
                    functions=[fn.name for fn in hidden_mints],
                    selectors=[fn.selector for fn in hidden_mints],
                    restricted_to=hidden_mints[0].restricted_to,
                )
            )
        if privileged_mints:
            names = ", ".join(f"{fn.name}()" for fn in privileged_mints)
            flags.append(
                self._flag(
                    "MINT_FUNCTION_PRIVILEGED",
                    Severity.HIGH,
                    "Mint callable by a non-governance role",
                    f"{names} can expand supply; guard = {privileged_mints[0].restricted_to or 'PUBLIC'}",
                    functions=[fn.name for fn in privileged_mints],
                    restricted_to=privileged_mints[0].restricted_to,
                )
            )

        # --- trading gates --------------------------------------------- #
        if c.has_blacklist and not renounced:
            flags.append(
                self._flag(
                    "BLACKLIST_CAPABILITY",
                    Severity.HIGH,
                    "Arbitrary blacklisting",
                    "Owner can block individual addresses from transferring (selective honeypot)",
                )
            )
        if c.can_pause_trading and not renounced:
            flags.append(
                self._flag(
                    "PAUSE_TRADING_CAPABILITY",
                    Severity.MEDIUM,
                    "Trading can be paused",
                    "Owner can disable trading globally while retaining their own exit",
                )
            )
        if c.has_whitelist_only_transfers:
            flags.append(
                self._flag(
                    "WHITELIST_TRANSFER_GATE",
                    Severity.HIGH,
                    "Transfers restricted to a whitelist",
                    "Only whitelisted addresses can transfer / sell – classic honeypot gate",
                    category=RiskCategory.HONEYPOT,
                )
            )
        if c.trading_enabled is False:
            notes.append("trading_enabled = false at observation time")
        if c.has_selfdestruct and not renounced:
            flags.append(
                self._flag(
                    "SELFDESTRUCT_CAPABILITY",
                    Severity.HIGH,
                    "Contract can self-destruct",
                    "selfdestruct is reachable – the token contract (and every balance in it) can be erased",
                )
            )
        if c.has_delegatecall and c.proxy_kind is not ProxyKind.MINIMAL:  # a clone's delegatecall *is* the pattern
            flags.append(
                self._flag(
                    "EXTERNAL_DELEGATECALL",
                    Severity.MEDIUM,
                    "Logic reachable via delegatecall",
                    "delegatecall is present outside a recognised proxy pattern – behaviour can change without a code update",
                )
            )

        # --- bytecode lineage ------------------------------------------ #
        rug_matches = [
            m for m in c.bytecode_matches if m.label == "rug" and ge(m.similarity, self.t.bytecode_similarity_min)
        ]
        benign_best = max((m.similarity for m in c.bytecode_matches if m.label == "benign"), default=0.0)
        if rug_matches:
            best = max(rug_matches, key=lambda m: (m.identical, m.similarity))
            if benign_best >= best.similarity:
                notes.append(
                    f"bytecode resembles a known rug ({best.similarity:.0%}) but a benign contract matches as well – shared template"
                )
            elif best.identical:
                flags.append(
                    self._flag(
                        "BYTECODE_IDENTICAL_TO_KNOWN_RUG",
                        Severity.CRITICAL,
                        "Bytecode identical to a known rug",
                        f"Runtime code hash matches {best.address} on {best.chain.value}, labelled rug – same contract redeployed",
                        match=best.address,
                        chain=best.chain.value,
                        matches=len(rug_matches),
                    )
                )
            else:
                flags.append(
                    self._flag(
                        "BYTECODE_SIMILAR_TO_KNOWN_RUG",
                        Severity.HIGH,
                        f"Bytecode {best.similarity:.0%} similar to a known rug",
                        f"Function-selector set is {best.similarity:.0%} identical to {best.address} on {best.chain.value} (labelled rug)",
                        match=best.address,
                        chain=best.chain.value,
                        similarity=best.similarity,
                        matches=len(rug_matches),
                    )
                )

        # --- proxy ----------------------------------------------------- #
        if c.proxy_kind in UPGRADEABLE_PROXIES:
            if not c.implementation_verified:
                flags.append(
                    self._flag(
                        "PROXY_UNVERIFIED_IMPLEMENTATION",
                        Severity.HIGH,
                        "Upgradeable proxy with unverified logic",
                        f"{c.proxy_kind.value} proxy → implementation {c.implementation or '?'} is unverified; logic can be swapped",
                        proxy_kind=c.proxy_kind.value,
                        implementation=c.implementation,
                    )
                )
            delay = c.upgrade_delay_hours or 0.0
            if not renounced and delay < self.t.proxy_min_upgrade_delay_hours:
                flags.append(
                    self._flag(
                        "PROXY_UPGRADE_NO_TIMELOCK",
                        Severity.MEDIUM,
                        "Proxy upgrades are not timelocked",
                        f"Implementation can change with {delay:g}h delay (< {self.t.proxy_min_upgrade_delay_hours:g}h)",
                        upgrade_delay_hours=delay,
                    )
                )
        elif c.proxy_kind == ProxyKind.MINIMAL:
            notes.append("EIP-1167 minimal proxy – not upgradeable")

        # --- declared taxes -------------------------------------------- #
        for side, pct in (("sell", c.sell_tax_pct), ("buy", c.buy_tax_pct)):
            if pct is None:
                notes.append(f"Declared {side} tax unknown")
                continue
            flag = self._tax_flag(side, pct)
            if flag is not None:
                flags.append(flag)
            if ge(pct, self.config.prefilter.hard_fail_tax_pct):
                hard_fail_reasons.append(f"declared {side} tax {pct:g}% ≥ {self.config.prefilter.hard_fail_tax_pct:g}%")

        return self._result(
            flags,
            hard_fail=bool(hard_fail_reasons),
            hard_fail_reason="; ".join(hard_fail_reasons) or None,
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    def _is_unbounded(self, fn: FunctionSignature) -> bool:
        return fn.upper_bound_pct is None or gt(fn.upper_bound_pct, self.t.max_reasonable_fee_pct)

    @staticmethod
    def _describe(fn: FunctionSignature) -> str:
        bound = "no cap" if fn.upper_bound_pct is None else f"cap {fn.upper_bound_pct:g}%"
        guard = fn.restricted_to or "PUBLIC"
        return f"{fn.name}() [{guard}, {bound}]"

    def _tax_flag(self, side: str, pct: float) -> RiskFlag | None:
        label = side.upper()
        if ge(pct, self.config.prefilter.hard_fail_tax_pct):
            return self._flag(
                f"{label}_TAX_EXTREME",
                Severity.CRITICAL,
                f"Extreme {side} tax ({pct:g}%)",
                f"Declared {side} tax of {pct:g}% confiscates essentially the whole trade",
                category=RiskCategory.HONEYPOT,
                tax_pct=pct,
            )
        if ge(pct, self.t.tax_high_pct):
            return self._flag(
                f"{label}_TAX_HIGH",
                Severity.HIGH,
                f"High {side} tax ({pct:g}%)",
                f"Declared {side} tax of {pct:g}% ≥ {self.t.tax_high_pct:g}%",
                category=RiskCategory.HONEYPOT,
                tax_pct=pct,
            )
        if ge(pct, self.t.tax_elevated_pct):
            return self._flag(
                f"{label}_TAX_ELEVATED",
                Severity.MEDIUM,
                f"Elevated {side} tax ({pct:g}%)",
                f"Declared {side} tax of {pct:g}% ≥ {self.t.tax_elevated_pct:g}%",
                category=RiskCategory.HONEYPOT,
                tax_pct=pct,
            )
        return None
