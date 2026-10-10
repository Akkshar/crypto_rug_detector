"""Runtime settings (env / .env) and the typed schema for ``risk_weights.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.models.risk_report import Chain, RiskCategory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHTS_PATH = PROJECT_ROOT / "config" / "risk_weights.yaml"
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


class Settings(BaseSettings):
    """Secrets and endpoints, read from ``RUGSCAN_*`` env vars or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="RUGSCAN_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # RPC URLs frequently embed provider API keys, so they are secrets too.
    ethereum_rpc_url: SecretStr | None = None
    bsc_rpc_url: SecretStr | None = None
    arbitrum_rpc_url: SecretStr | None = None
    base_rpc_url: SecretStr | None = None
    etherscan_api_key: SecretStr | None = None

    risk_weights_path: Path = DEFAULT_WEIGHTS_PATH
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR
    log_level: str = "INFO"
    rpc_timeout_s: float = Field(20.0, gt=0)

    def rpc_url(self, chain: Chain | str) -> str | None:
        secret: SecretStr | None = getattr(self, f"{Chain(chain).value}_rpc_url")
        return secret.get_secret_value() if secret else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------- #
# risk_weights.yaml schema
# --------------------------------------------------------------------------- #

class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScoringWeights(_Cfg):
    rule_weight: float = Field(0.75, ge=0, le=1)
    ml_weight: float = Field(0.25, ge=0, le=1)
    hard_fail_score_floor: float = Field(95, ge=0, le=100)
    unreliable_ml_factor: float = Field(0.5, ge=0, le=1)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ScoringWeights:
        if abs(self.rule_weight + self.ml_weight - 1.0) > 1e-6:
            raise ValueError("scoring.rule_weight + scoring.ml_weight must equal 1.0")
        return self


class TierThresholds(_Cfg):
    medium: float = 25
    high: float = 50
    critical: float = 75

    @model_validator(mode="after")
    def _ascending(self) -> TierThresholds:
        if not 0 < self.medium < self.high < self.critical <= 100:
            raise ValueError("tiers must satisfy 0 < medium < high < critical <= 100")
        return self


class PrefilterConfig(_Cfg):
    hard_fail_tax_pct: float = Field(90, gt=0, le=100)


class MLConfig(_Cfg):
    enabled: bool = True
    n_samples: int = Field(4000, ge=200)
    seed: int = 7
    ood_percentile: float = Field(0.99, ge=0.5, le=1.0)
    max_imputed_features: int = Field(2, ge=0)


class Thresholds(_Cfg):
    max_reasonable_fee_pct: float = Field(25, ge=0, le=100)
    proxy_min_upgrade_delay_hours: float = Field(24, ge=0)
    tax_elevated_pct: float = Field(10, ge=0, le=100)
    tax_high_pct: float = Field(25, ge=0, le=100)
    lp_locked_ok_ratio: float = Field(0.8, ge=0, le=1)
    lp_mostly_unlocked_ratio: float = Field(0.5, ge=0, le=1)
    lp_partially_unlocked_ratio: float = Field(0.2, ge=0, le=1)
    lp_insider_ratio: float = Field(0.1, ge=0, le=1)
    lp_lock_min_days: float = Field(30, ge=0)
    low_liquidity_usd: float = Field(10_000, ge=0)
    fdv_liquidity_ratio_warn: float = Field(100, gt=0)
    fdv_liquidity_ratio_extreme: float = Field(1000, gt=0)
    min_sell_success_rate: float = Field(0.8, ge=0, le=1)
    hidden_tax_tolerance_pct: float = Field(10, ge=0, le=100)
    max_sell_gas: int = Field(1_000_000, gt=0)
    sell_gas_ratio: float = Field(3.0, gt=1)
    top10_high: float = Field(0.5, ge=0, le=1)
    top10_elevated: float = Field(0.3, ge=0, le=1)
    single_whale: float = Field(0.2, ge=0, le=1)
    fresh_deployer_days: float = Field(7, ge=0)
    serial_launcher_count: int = Field(10, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if not self.tax_elevated_pct < self.tax_high_pct:
            raise ValueError("tax_elevated_pct must be < tax_high_pct")
        if not self.lp_partially_unlocked_ratio < self.lp_mostly_unlocked_ratio:
            raise ValueError("lp_partially_unlocked_ratio must be < lp_mostly_unlocked_ratio")
        if not self.fdv_liquidity_ratio_warn < self.fdv_liquidity_ratio_extreme:
            raise ValueError("fdv_liquidity_ratio_warn must be < fdv_liquidity_ratio_extreme")
        if not self.top10_elevated < self.top10_high:
            raise ValueError("top10_elevated must be < top10_high")
        return self


class RiskConfig(_Cfg):
    version: int = 1
    scoring: ScoringWeights = ScoringWeights()
    tiers: TierThresholds = TierThresholds()
    prefilter: PrefilterConfig = PrefilterConfig()
    ml: MLConfig = MLConfig()
    category_caps: dict[RiskCategory, Annotated[float, Field(ge=0, le=100)]] = {}
    thresholds: Thresholds = Thresholds()
    flags: dict[str, Annotated[float, Field(ge=0, le=100)]] = {}

    def points(self, code: str) -> float:
        try:
            return self.flags[code]
        except KeyError:
            raise KeyError(f"flag code {code!r} has no weight in risk_weights.yaml") from None

    def cap(self, category: RiskCategory) -> float:
        return self.category_caps.get(category, 100.0)


def load_risk_config(path: Path | str | None = None) -> RiskConfig:
    """Load and validate a risk weights YAML file (defaults to ``config/risk_weights.yaml``)."""
    path = Path(path) if path else get_settings().risk_weights_path
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return RiskConfig.model_validate(raw)
