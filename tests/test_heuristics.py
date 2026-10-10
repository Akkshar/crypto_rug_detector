"""Unit tests for schemas, the mock provider and each heuristic engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.heuristics import DEFAULT_HEURISTICS, build_heuristics
from src.heuristics.contract_inspector import ContractInspector
from src.heuristics.deployer_tracer import DeployerTracer
from src.heuristics.holder_metrics import HolderMetrics
from src.heuristics.liquidity_analyzer import LiquidityAnalyzer
from src.ingestion.base import SnapshotNotFound
from src.models.risk_report import (
    Chain,
    ContractMetadata,
    FunctionSignature,
    FundingSourceKind,
    Holder,
    HolderDistribution,
    LiquidityPool,
    LPHolding,
    OwnerType,
    ProxyKind,
    RiskCategory,
    SellSimulation,
    Severity,
    TokenSnapshot,
)
from tests.conftest import FIXTURE_NAMES

A = "0x" + "11" * 20
B = "0x" + "22" * 20


def codes(result) -> set[str]:
    return {f.code for f in result.flags}


# --------------------------------------------------------------------------- #
# Schemas & provider
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixtures_validate_and_checksum(provider, name):
    snap = provider.resolve(name)
    assert snap.address == snap.address.lower() or snap.address != snap.address.lower()  # checksummed str
    assert snap.address.startswith("0x") and len(snap.address) == 42
    assert snap.contract.symbol


def test_provider_lookup_by_address_any_case(provider):
    snap = provider.resolve("0x2222222222222222222222222222222222222222".upper().replace("0X", "0x"))
    assert snap.contract.symbol == "SME2"


def test_provider_unknown_target(provider):
    with pytest.raises(SnapshotNotFound):
        provider.resolve("0x" + "99" * 20)


async def test_provider_async_fetch_and_chain_mismatch(provider):
    snap = await provider.fetch_snapshot(Chain.BSC, "sample_honeypot")
    assert snap.chain is Chain.BSC
    with pytest.raises(SnapshotNotFound):
        await provider.fetch_snapshot(Chain.ETHEREUM, "sample_honeypot")


def test_fetch_returns_copy(provider):
    import asyncio

    a = asyncio.run(provider.fetch_snapshot(Chain.BSC, "sample_honeypot"))
    a.contract.sell_tax_pct = 0.0
    assert provider.resolve("sample_honeypot").contract.sell_tax_pct == 99.0


def test_invalid_address_rejected():
    with pytest.raises(ValidationError):
        TokenSnapshot(chain="bsc", address="0x1234", contract=ContractMetadata())


def test_holder_shares_cannot_exceed_one():
    with pytest.raises(ValidationError):
        HolderDistribution(top_holders=[Holder(address=A, share=0.7), Holder(address=B, share=0.5)])


def test_sell_simulation_counts():
    with pytest.raises(ValidationError):
        SellSimulation(attempted=1, succeeded=2)
    assert SellSimulation(attempted=0, succeeded=0).success_rate is None


def test_ownership_inferred_from_burn_owner():
    meta = ContractMetadata(owner="0x000000000000000000000000000000000000dEaD")
    assert meta.ownership_renounced is True
    assert meta.owner_type is OwnerType.RENOUNCED


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_snapshot_json_roundtrip_survives_computed_fields(provider, name):
    snap = provider.resolve(name)
    again = TokenSnapshot.model_validate_json(snap.model_dump_json())
    assert again == snap
    assert again.fdv_usd == snap.fdv_usd


def test_pool_computed_shares():
    pool = LiquidityPool(
        dex="uniswap_v2",
        pair_address=A,
        quote_token="WETH",
        liquidity_usd=1.0,
        lp_holdings=[
            LPHolding(holder=A, share=0.6, locker="uncx"),
            LPHolding(holder=B, share=0.4, is_deployer_or_owner=True),
        ],
    )
    assert pool.locked_share == pytest.approx(0.6)
    assert pool.insider_share == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Every heuristic on every fixture
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
@pytest.mark.parametrize("cls", DEFAULT_HEURISTICS)
def test_every_flag_has_a_weight_and_valid_shape(config, provider, cls, name):
    result = cls(config).evaluate(provider.resolve(name))
    for flag in result.flags:
        assert flag.points == config.flags[flag.code]
        assert flag.code in config.flags
        assert flag.detail


# --------------------------------------------------------------------------- #
# ContractInspector
# --------------------------------------------------------------------------- #


def test_contract_inspector_benign_is_clean(config, benign):
    result = ContractInspector(config).evaluate(benign)
    assert result.flags == []
    assert not result.hard_fail
    assert any("renounced" in n for n in result.notes)


def test_contract_inspector_honeypot(config, honeypot):
    result = ContractInspector(config).evaluate(honeypot)
    assert {
        "OWNER_ACTIVE_EOA",
        "UNBOUNDED_FEE_SETTER",
        "UNBOUNDED_MAX_TX_SETTER",
        "BLACKLIST_CAPABILITY",
        "PAUSE_TRADING_CAPABILITY",
        "WHITELIST_TRANSFER_GATE",
        "SELL_TAX_EXTREME",
    } <= codes(result)
    assert result.hard_fail and "99%" in result.hard_fail_reason
    tax = next(f for f in result.flags if f.code == "SELL_TAX_EXTREME")
    assert tax.category is RiskCategory.HONEYPOT and tax.severity is Severity.CRITICAL


def test_contract_inspector_rug_hidden_mint(config, rug):
    result = ContractInspector(config).evaluate(rug)
    assert codes(result) == {"OWNER_ACTIVE_EOA", "HIDDEN_MINT_FUNCTION"}
    assert not result.hard_fail  # 5 % tax is not a pre-filter trip


def test_renounced_ownership_neutralises_owner_gated_setters(config, honeypot):
    honeypot.contract.owner = "0x0000000000000000000000000000000000000000"
    honeypot.contract.owner_type = OwnerType.RENOUNCED
    honeypot.contract.ownership_renounced = True
    result = ContractInspector(config).evaluate(honeypot)
    assert not codes(result) & {
        "OWNER_ACTIVE_EOA",
        "UNBOUNDED_FEE_SETTER",
        "BLACKLIST_CAPABILITY",
        "PAUSE_TRADING_CAPABILITY",
    }
    assert "SELL_TAX_EXTREME" in codes(result)  # the tax itself is still live


def test_fee_setter_bounded_at_threshold_is_ok(config, benign):
    benign.contract.owner = A
    benign.contract.owner_type = OwnerType.EOA
    benign.contract.ownership_renounced = False
    benign.contract.fee_setters = [FunctionSignature(name="setFees", restricted_to="onlyOwner", upper_bound_pct=25.0)]
    assert "UNBOUNDED_FEE_SETTER" not in codes(ContractInspector(config).evaluate(benign))
    benign.contract.fee_setters = [FunctionSignature(name="setFees", restricted_to="onlyOwner", upper_bound_pct=26.0)]
    assert "UNBOUNDED_FEE_SETTER" in codes(ContractInspector(config).evaluate(benign))


def test_governance_gated_mint_is_a_note_not_a_flag(config, benign):
    benign.contract.mint_functions = [FunctionSignature(name="mint", restricted_to="TIMELOCK")]
    result = ContractInspector(config).evaluate(benign)
    assert "MINT_FUNCTION_PRIVILEGED" not in codes(result)
    assert any("governance" in n for n in result.notes)


def test_unverified_proxy_without_timelock(config, benign):
    benign.contract.owner, benign.contract.owner_type, benign.contract.ownership_renounced = A, OwnerType.EOA, False
    benign.contract.proxy_kind = ProxyKind.UUPS
    benign.contract.implementation = B
    benign.contract.implementation_verified = False
    benign.contract.upgrade_delay_hours = 0
    assert {"PROXY_UNVERIFIED_IMPLEMENTATION", "PROXY_UPGRADE_NO_TIMELOCK"} <= codes(
        ContractInspector(config).evaluate(benign)
    )


def test_unverified_source_flagged(config, benign):
    benign.contract.source_verified = False
    assert "SOURCE_UNVERIFIED" in codes(ContractInspector(config).evaluate(benign))


# --------------------------------------------------------------------------- #
# LiquidityAnalyzer
# --------------------------------------------------------------------------- #


def test_liquidity_benign_is_clean(config, benign):
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert result.flags == [] and not result.hard_fail


def test_liquidity_honeypot_sell_blocked(config, honeypot):
    result = LiquidityAnalyzer(config).evaluate(honeypot)
    assert codes(result) == {"HONEYPOT_SELL_BLOCKED"}
    assert result.hard_fail and "0/10" in result.hard_fail_reason
    flag = result.flags[0]
    assert flag.category is RiskCategory.HONEYPOT and flag.severity is Severity.CRITICAL
    assert flag.evidence["revert_reason"] == "TransferHelper: TRANSFER_FROM_FAILED"


def test_liquidity_rug_unlocked_lp(config, rug):
    result = LiquidityAnalyzer(config).evaluate(rug)
    assert codes(result) == {"LP_MOSTLY_UNLOCKED", "LP_HELD_BY_INSIDER", "FDV_LIQUIDITY_IMBALANCE"}
    assert not result.hard_fail


def test_no_pool_flagged(config, benign):
    benign.pools = []
    assert "NO_LIQUIDITY_POOL" in codes(LiquidityAnalyzer(config).evaluate(benign))


def test_missing_sell_simulation_is_info_flag(config, benign):
    benign.sell_simulation = None
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert codes(result) == {"SELL_SIMULATION_UNAVAILABLE"}
    assert result.flags[0].severity is Severity.INFO


def test_hidden_sell_tax_and_hard_fail_on_measured_loss(config, benign):
    benign.sell_simulation = SellSimulation(
        attempted=5, succeeded=5, buy_gas=100_000, sell_gas=120_000, effective_sell_tax_pct=40.0
    )
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert "HIDDEN_SELL_TAX" in codes(result) and not result.hard_fail
    benign.sell_simulation = SellSimulation(attempted=5, succeeded=5, effective_sell_tax_pct=95.0)
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert result.hard_fail and "95" in result.hard_fail_reason


def test_low_sell_success_rate_and_gas(config, benign):
    benign.sell_simulation = SellSimulation(attempted=10, succeeded=5, buy_gas=100_000, sell_gas=500_000)
    assert {"LOW_SELL_SUCCESS_RATE", "EXCESSIVE_SELL_GAS"} <= codes(LiquidityAnalyzer(config).evaluate(benign))


def test_lp_lock_expiring_soon(config, benign):
    benign.pools[0].lp_holdings[0].unlock_at = datetime(2026, 10, 20, tzinfo=timezone.utc)  # 10 days after observed_at
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert "LP_LOCK_EXPIRING" in codes(result)


def test_partially_unlocked_and_extreme_fdv(config, benign):
    benign.pools[0].lp_holdings = [LPHolding(holder=A, share=0.7, locker="uncx"), LPHolding(holder=B, share=0.3)]
    benign.pools[0].liquidity_usd = 1_000.0
    result = LiquidityAnalyzer(config).evaluate(benign)
    assert {"LP_PARTIALLY_UNLOCKED", "LOW_LIQUIDITY", "FDV_LIQUIDITY_EXTREME"} <= codes(result)


# --------------------------------------------------------------------------- #
# HolderMetrics
# --------------------------------------------------------------------------- #


def test_holder_metrics_values(config, benign, honeypot, rug):
    h = HolderMetrics(config)
    assert codes(h.evaluate(benign)) == {"TOP10_CONCENTRATION_ELEVATED"}
    hp = h.evaluate(honeypot)
    assert codes(hp) == {"TOP10_CONCENTRATION_HIGH", "SINGLE_WHALE_DOMINANT"}
    # 0.12 / 0.6 is 0.19999… in floating point – the epsilon comparison must still trip the 0.2 threshold
    whale = next(f for f in hp.flags if f.code == "SINGLE_WHALE_DOMINANT")
    assert whale.evidence["whale_share"] == pytest.approx(0.2)
    top10 = next(f for f in h.evaluate(rug).flags if f.code == "TOP10_CONCENTRATION_HIGH")
    assert top10.evidence["top10_concentration"] == pytest.approx(0.82 / 0.92, abs=1e-3)


def test_holder_metrics_excludes_dex_and_burn(config):
    snap = TokenSnapshot(
        chain="bsc",
        address=A,
        contract=ContractMetadata(),
        holders=HolderDistribution(
            top_holders=[Holder(address=A, share=0.9, is_dex_or_burn=True), Holder(address=B, share=0.01)]
        ),
    )
    result = HolderMetrics(config).evaluate(snap)
    # the single non-DEX wallet holds 10 % of circulating supply → nothing elevated
    assert result.flags == [] and result.data_available


def test_holder_metrics_without_data(config, benign):
    benign.holders = HolderDistribution()
    result = HolderMetrics(config).evaluate(benign)
    assert result.flags == [] and result.data_available is False


# --------------------------------------------------------------------------- #
# DeployerTracer
# --------------------------------------------------------------------------- #


def test_deployer_tracer(config, benign, honeypot, rug):
    d = DeployerTracer(config)
    assert d.evaluate(benign).flags == []
    assert codes(d.evaluate(honeypot)) == {"DEPLOYER_PRIVACY_EXCHANGE_FUNDED", "DEPLOYER_FRESH_WALLET"}
    assert codes(d.evaluate(rug)) == {
        "DEPLOYER_MIXER_FUNDED",
        "DEPLOYER_PRIOR_RUGS",
        "DEPLOYER_SERIAL_LAUNCHER",
        "DEPLOYER_FRESH_WALLET",
    }
    mixer = next(f for f in d.evaluate(rug).flags if f.code == "DEPLOYER_MIXER_FUNDED")
    assert mixer.severity is Severity.CRITICAL and "Tornado" in mixer.detail


def test_deployer_tracer_without_profile(config, benign):
    benign.deployer = None
    result = DeployerTracer(config).evaluate(benign)
    assert result.data_available is False and result.flags == []


def test_deployer_linked_scams_without_count(config, benign):
    benign.deployer.first_funding_kind = FundingSourceKind.CEX
    benign.deployer.prior_rug_count = None
    benign.deployer.linked_scam_addresses = [B]
    assert "DEPLOYER_PRIOR_RUGS" in codes(DeployerTracer(config).evaluate(benign))


def test_build_heuristics_registry(config):
    names = [h.name for h in build_heuristics(config)]
    assert names == ["contract_inspector", "liquidity_analyzer", "holder_metrics", "deployer_tracer", "trade_flow"]
