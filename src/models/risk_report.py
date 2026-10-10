"""Pydantic v2 schemas shared across ingestion, heuristics, scoring and the CLI.

Three layers:

* **Input** – ``TokenSnapshot``: everything a data provider knows about a token.
  Unknown facts are ``None`` (never guessed) so heuristics can distinguish
  "not risky" from "not observed".
* **Heuristic output** – ``RiskFlag`` / ``HeuristicResult``: evidence raised by
  one heuristic engine, carrying the points it contributed.
* **Report** – ``RiskReport``: the final composite score, tier, per-category
  breakdown and the ML refinement, serialisable straight to JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from eth_utils import is_address, to_checksum_address
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


def _checksum(value: str) -> str:
    if not is_address(value):
        raise ValueError(f"invalid EVM address: {value!r}")
    return to_checksum_address(value)


Address = Annotated[str, AfterValidator(_checksum)]
Pct = Annotated[float, Field(ge=0, le=100)]
Ratio = Annotated[float, Field(ge=0, le=1)]

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"
BURN_ADDRESSES: frozenset[str] = frozenset({ZERO_ADDRESS, DEAD_ADDRESS})


class Chain(StrEnum):
    ETHEREUM = "ethereum"
    BSC = "bsc"
    ARBITRUM = "arbitrum"
    BASE = "base"


class RiskTier(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskCategory(StrEnum):
    CONTRACT_PRIVILEGE = "CONTRACT_PRIVILEGE"
    LIQUIDITY = "LIQUIDITY"
    HONEYPOT = "HONEYPOT"
    DISTRIBUTION = "DISTRIBUTION"
    DEPLOYER = "DEPLOYER"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OwnerType(StrEnum):
    RENOUNCED = "renounced"  # owner is 0x0 / dead
    EOA = "eoa"  # externally owned account
    MULTISIG = "multisig"  # Safe-style contract wallet
    TIMELOCK = "timelock"  # governance timelock contract
    CONTRACT = "contract"  # some other contract, not a known timelock
    UNKNOWN = "unknown"


class ProxyKind(StrEnum):
    NONE = "none"
    TRANSPARENT = "transparent"
    UUPS = "uups"
    BEACON = "beacon"
    MINIMAL = "minimal"  # EIP-1167 clone (not upgradeable)
    UNKNOWN = "unknown"


class LockerKind(StrEnum):
    UNCX = "uncx"
    PINKLOCK = "pinklock"
    TEAM_FINANCE = "team_finance"
    BURNED = "burned"  # LP sent to 0x0 / dead
    OTHER_KNOWN = "other_known"
    UNKNOWN_CONTRACT = "unknown_contract"


class FundingSourceKind(StrEnum):
    MIXER = "mixer"  # Tornado Cash, Railgun, ...
    PRIVACY_EXCHANGE = "privacy_exchange"  # FixedFloat, eXch, ...
    CEX = "cex"
    BRIDGE = "bridge"
    EOA = "eoa"
    UNKNOWN = "unknown"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_serialised_computed_fields(cls, data: object) -> object:
        # computed_fields are written by model_dump() but are not inputs; without
        # this, extra="forbid" would reject a model's own JSON on the way back in.
        if isinstance(data, dict) and cls.model_computed_fields:
            return {k: v for k, v in data.items() if k not in cls.model_computed_fields}
        return data


# --------------------------------------------------------------------------- #
# Input snapshot
# --------------------------------------------------------------------------- #


class FunctionSignature(_Strict):
    """A privileged / notable function found in the ABI or bytecode."""

    name: str
    selector: str | None = Field(None, pattern=r"^0x[0-9a-fA-F]{8}$")
    restricted_to: str | None = Field(
        None, description="Role or modifier guarding the call, e.g. 'onlyOwner', 'MINTER_ROLE', None=public"
    )
    upper_bound_pct: Pct | None = Field(
        None, description="Hard cap enforced by the setter (fees/limits); None = unbounded"
    )
    hidden: bool = Field(False, description="Not exposed in the verified ABI but present in bytecode / internal calls")


class ContractMetadata(_Strict):
    name: str | None = None
    symbol: str | None = None
    decimals: int = Field(18, ge=0, le=36)
    total_supply: float | None = Field(None, ge=0)
    source_verified: bool = False
    compiler_version: str | None = None
    deployed_at: datetime | None = None
    deployer: Address | None = None

    # ownership
    owner: Address | None = None
    owner_type: OwnerType = OwnerType.UNKNOWN
    ownership_renounced: bool | None = None

    # proxy / upgradeability
    proxy_kind: ProxyKind = ProxyKind.NONE
    implementation: Address | None = None
    implementation_verified: bool | None = None
    upgrade_delay_hours: float | None = Field(None, ge=0)

    # declared taxes (as read from storage / getters)
    buy_tax_pct: Pct | None = None
    sell_tax_pct: Pct | None = None
    transfer_tax_pct: Pct | None = None

    # capability flags derived from ABI / bytecode analysis
    fee_setters: list[FunctionSignature] = Field(default_factory=list)
    max_tx_setters: list[FunctionSignature] = Field(default_factory=list)
    mint_functions: list[FunctionSignature] = Field(default_factory=list)
    has_blacklist: bool = False
    has_whitelist_only_transfers: bool = False
    can_pause_trading: bool = False
    has_selfdestruct: bool = False
    has_delegatecall: bool = False
    trading_enabled: bool | None = None

    @model_validator(mode="after")
    def _infer_renounced(self) -> ContractMetadata:
        if self.owner is not None and self.ownership_renounced is None:
            self.ownership_renounced = self.owner in BURN_ADDRESSES
        if self.ownership_renounced and self.owner_type == OwnerType.UNKNOWN:
            self.owner_type = OwnerType.RENOUNCED
        return self


class LPHolding(_Strict):
    holder: Address
    share: Ratio = Field(description="Fraction of total LP token supply")
    locker: LockerKind | None = Field(None, description="Set when the holder is a recognised locker / burn")
    unlock_at: datetime | None = None
    is_deployer_or_owner: bool = False


class LiquidityPool(_Strict):
    dex: str = Field(description="e.g. uniswap_v2, uniswap_v3, pancakeswap_v2")
    pair_address: Address
    quote_token: str = Field(description="Symbol of the paired asset (WETH, USDC, WBNB...)")
    liquidity_usd: float = Field(ge=0)
    token_reserve: float | None = Field(None, ge=0)
    quote_reserve: float | None = Field(None, ge=0)
    created_at: datetime | None = None
    lp_holdings: list[LPHolding] = Field(default_factory=list)

    @field_validator("lp_holdings")
    @classmethod
    def _shares_le_one(cls, holdings: list[LPHolding]) -> list[LPHolding]:
        total = sum(h.share for h in holdings)
        if total > 1.0 + 1e-6:
            raise ValueError(f"LP holding shares sum to {total:.4f} > 1")
        return holdings

    @computed_field  # type: ignore[prop-decorator]
    @property
    def locked_share(self) -> float:
        """Fraction of LP held by recognised lockers or burned."""
        return min(1.0, sum(h.share for h in self.lp_holdings if h.locker is not None))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def insider_share(self) -> float:
        return min(1.0, sum(h.share for h in self.lp_holdings if h.is_deployer_or_owner and h.locker is None))


class SellSimulation(_Strict):
    """Outcome of simulating buy → sell round trips (eth_call / local EVM fork)."""

    attempted: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    buy_gas: int | None = Field(None, ge=0)
    sell_gas: int | None = Field(None, ge=0)
    effective_buy_tax_pct: Pct | None = None
    effective_sell_tax_pct: Pct | None = Field(None, description="Value lost on sell vs. quoted amount")
    revert_reason: str | None = None

    @model_validator(mode="after")
    def _counts(self) -> SellSimulation:
        if self.succeeded > self.attempted:
            raise ValueError("succeeded cannot exceed attempted")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_rate(self) -> float | None:
        return None if self.attempted == 0 else self.succeeded / self.attempted


class Holder(_Strict):
    address: Address
    share: Ratio = Field(description="Fraction of total supply")
    label: str | None = Field(None, description="e.g. 'uniswap_v2_pair', 'burn', 'locker', 'cex'")
    is_contract: bool = False
    is_dex_or_burn: bool = False


class HolderDistribution(_Strict):
    holder_count: int | None = Field(None, ge=0)
    top_holders: list[Holder] = Field(default_factory=list)

    @field_validator("top_holders")
    @classmethod
    def _shares_le_one(cls, holders: list[Holder]) -> list[Holder]:
        total = sum(h.share for h in holders)
        if total > 1.0 + 1e-6:
            raise ValueError(f"holder shares sum to {total:.4f} > 1")
        return holders

    def circulating_top_n(self, n: int = 10) -> list[Holder]:
        """Top *n* holders excluding DEX pairs and burn addresses."""
        eligible = [h for h in self.top_holders if not h.is_dex_or_burn]
        return sorted(eligible, key=lambda h: h.share, reverse=True)[:n]

    def circulating_share(self) -> float:
        return max(0.0, 1.0 - sum(h.share for h in self.top_holders if h.is_dex_or_burn))


class DeployerProfile(_Strict):
    address: Address
    first_seen: datetime | None = None
    first_funding_source: Address | None = None
    first_funding_kind: FundingSourceKind = FundingSourceKind.UNKNOWN
    first_funding_label: str | None = Field(None, description="e.g. 'Tornado Cash 10 ETH'")
    contracts_deployed: int | None = Field(None, ge=0)
    prior_rug_count: int | None = Field(None, ge=0, description="Linked past tokens flagged as rugs")
    linked_scam_addresses: list[Address] = Field(default_factory=list)

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.first_seen is None:
            return None
        now = now or datetime.now(timezone.utc)
        first = self.first_seen if self.first_seen.tzinfo else self.first_seen.replace(tzinfo=timezone.utc)
        return max(0.0, (now - first).total_seconds() / 86_400)


class TokenSnapshot(_Strict):
    """Complete observation of a token as returned by a data provider."""

    chain: Chain
    address: Address
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    block_number: int | None = Field(None, ge=0, description="Block the observation was pinned to (live provider)")
    price_usd: float | None = Field(None, ge=0)
    contract: ContractMetadata
    pools: list[LiquidityPool] = Field(default_factory=list)
    sell_simulation: SellSimulation | None = None
    holders: HolderDistribution = Field(default_factory=HolderDistribution)
    deployer: DeployerProfile | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_liquidity_usd(self) -> float:
        return sum(p.liquidity_usd for p in self.pools)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fdv_usd(self) -> float | None:
        if self.price_usd is None or self.contract.total_supply is None:
            return None
        return self.price_usd * self.contract.total_supply

    @property
    def primary_pool(self) -> LiquidityPool | None:
        return max(self.pools, key=lambda p: p.liquidity_usd, default=None)


# --------------------------------------------------------------------------- #
# Heuristic output
# --------------------------------------------------------------------------- #


class RiskFlag(_Strict):
    code: str = Field(pattern=r"^[A-Z0-9_]+$", description="Stable identifier matching risk_weights.yaml")
    category: RiskCategory
    severity: Severity
    title: str
    detail: str
    points: float = Field(ge=0, le=100, description="Raw points contributed before category capping")
    evidence: dict[str, object] = Field(default_factory=dict)


class HeuristicResult(_Strict):
    heuristic: str
    category: RiskCategory
    flags: list[RiskFlag] = Field(default_factory=list)
    hard_fail: bool = Field(False, description="Pre-filter verdict: immediate CRITICAL regardless of score")
    hard_fail_reason: str | None = None
    data_available: bool = Field(True, description="False when the input lacked what this heuristic needs")
    notes: list[str] = Field(default_factory=list)

    @property
    def raw_points(self) -> float:
        return sum(f.points for f in self.flags)


# --------------------------------------------------------------------------- #
# Final report
# --------------------------------------------------------------------------- #


class CategoryBreakdown(_Strict):
    category: RiskCategory
    raw_points: float = Field(ge=0)
    capped_points: float = Field(ge=0)
    cap: float = Field(ge=0)
    flag_count: int = Field(ge=0)


class MLAssessment(_Strict):
    model: str
    rug_probability: Ratio
    anomaly_score: float = Field(description="IsolationForest decision score (lower = more anomalous)")
    is_out_of_distribution: bool = False
    imputed_features: list[str] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    contributions: dict[str, float] = Field(
        default_factory=dict, description="Per-feature change in P(rug) vs. that feature at its training median"
    )
    reliable: bool = True

    def top_drivers(self, n: int = 3) -> list[tuple[str, float]]:
        return sorted(self.contributions.items(), key=lambda kv: -abs(kv[1]))[:n]


class RiskReport(_Strict):
    schema_version: int = 1
    chain: Chain
    address: Address
    token_name: str | None = None
    token_symbol: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str

    risk_score: float = Field(ge=0, le=100)
    risk_tier: RiskTier
    rule_score: float = Field(ge=0, le=100)
    ml: MLAssessment | None = None

    hard_fail: bool = False
    hard_fail_reasons: list[str] = Field(default_factory=list)

    breakdown: list[CategoryBreakdown] = Field(default_factory=list)
    flags: list[RiskFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Human-readable summary lines")
    missing_data: list[str] = Field(default_factory=list, description="Heuristics that could not run")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def critical_flags(self) -> list[str]:
        return [f.code for f in self.flags if f.severity == Severity.CRITICAL]

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
