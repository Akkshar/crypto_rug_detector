"""Typed loader for ``config/chains.yaml``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from eth_utils import to_checksum_address
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from src.models.risk_report import Chain, FundingSourceKind, LockerKind

DEFAULT_CHAINS_PATH = Path(__file__).resolve().parent / "chains.yaml"


def _cs(value: str) -> str:
    return to_checksum_address(value)


Addr = Annotated[str, AfterValidator(_cs)]


class DexKind(StrEnum):
    V2 = "v2"
    V3 = "v3"


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DexConfig(_Cfg):
    kind: DexKind
    factory: Addr
    router: Addr
    fee_tiers: list[int] = Field(default_factory=lambda: [500, 3000, 10000])


class PriceSource(_Cfg):
    kind: str = Field(pattern=r"^(v2_pair|v3_pool)$")
    address: Addr
    stable: str


class ChainConfig(_Cfg):
    chain_id: int
    wrapped_native: Addr
    native_symbol: str
    quote_tokens: dict[str, Addr]
    stablecoins: list[str] = Field(default_factory=list)
    native_price_source: PriceSource | None = None
    dexes: dict[str, DexConfig]
    lockers: dict[str, list[Addr]] = Field(default_factory=dict)
    mixers: dict[str, Addr] = Field(default_factory=dict)
    privacy_exchanges: dict[str, Addr] = Field(default_factory=dict)
    cex_hot_wallets: dict[str, Addr] = Field(default_factory=dict)
    explorer_api: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ChainConfig:
        for s in self.stablecoins:
            if s not in self.quote_tokens:
                raise ValueError(f"stablecoin {s!r} missing from quote_tokens")
        if self.native_price_source and self.native_price_source.stable not in self.quote_tokens:
            raise ValueError("native_price_source.stable must be a quote token")
        for name in self.lockers:
            LockerKind(name)  # must be a known locker kind
        return self

    # ------------------------------------------------------------------ #
    def locker_for(self, address: str) -> LockerKind | None:
        addr = to_checksum_address(address)
        for name, addrs in self.lockers.items():
            if addr in addrs:
                return LockerKind(name)
        return None

    def label_funding_source(self, address: str) -> tuple[FundingSourceKind, str | None]:
        addr = to_checksum_address(address)
        for label, a in self.mixers.items():
            if a == addr:
                return FundingSourceKind.MIXER, label
        for label, a in self.privacy_exchanges.items():
            if a == addr:
                return FundingSourceKind.PRIVACY_EXCHANGE, label
        for label, a in self.cex_hot_wallets.items():
            if a == addr:
                return FundingSourceKind.CEX, label
        return FundingSourceKind.UNKNOWN, None

    def is_stable(self, symbol: str) -> bool:
        return symbol in self.stablecoins

    def decimals_hint(self, symbol: str) -> int:
        # The common 6-decimal stables; everything else is assumed 18 and
        # corrected by an on-chain decimals() read when available.
        return 6 if symbol in {"USDC", "USDT"} else 18


class ChainRegistry(_Cfg):
    chains: dict[Chain, ChainConfig]
    burn_addresses: list[Addr] = Field(default_factory=list)

    def get(self, chain: Chain | str) -> ChainConfig:
        try:
            return self.chains[Chain(chain)]
        except KeyError:
            raise KeyError(f"chain {chain!r} not in chains.yaml") from None

    def is_burn(self, address: str) -> bool:
        return to_checksum_address(address) in self.burn_addresses


@lru_cache(maxsize=2)
def load_chain_registry(path: Path | str | None = None) -> ChainRegistry:
    with Path(path or DEFAULT_CHAINS_PATH).open(encoding="utf-8") as fh:
        return ChainRegistry.model_validate(yaml.safe_load(fh) or {})
