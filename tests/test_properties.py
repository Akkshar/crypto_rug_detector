"""Property-based tests: random-but-valid snapshots must never crash the engine
and must always yield a well-formed report."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from eth_utils import to_checksum_address
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.models.risk_report import (
    ContractMetadata,
    DeployerProfile,
    FunctionSignature,
    FundingSourceKind,
    Holder,
    HolderDistribution,
    LiquidityPool,
    LockerKind,
    LPHolding,
    OwnerType,
    ProxyKind,
    RiskReport,
    RiskTier,
    SellSimulation,
    TokenSnapshot,
)

OBSERVED_AT = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

addresses = st.integers(min_value=1, max_value=2**160 - 1).map(lambda n: to_checksum_address(f"0x{n:040x}"))
pct = st.one_of(st.none(), st.floats(0, 100, allow_nan=False))
past_dt = st.datetimes(min_value=datetime(2016, 1, 1), max_value=datetime(2026, 10, 10)).map(
    lambda d: d.replace(tzinfo=timezone.utc)
)
future_dt = st.datetimes(min_value=datetime(2026, 10, 11), max_value=datetime(2030, 1, 1)).map(
    lambda d: d.replace(tzinfo=timezone.utc)
)


def _normalised_shares(max_n: int):
    """A list of shares summing to at most 1."""
    return st.tuples(
        st.lists(st.floats(0.001, 1, allow_nan=False), min_size=0, max_size=max_n),
        st.floats(0, 1, allow_nan=False),
    ).map(lambda t: [w / sum(t[0]) * t[1] for w in t[0]] if t[0] else [])


function_sigs = st.builds(
    FunctionSignature,
    name=st.sampled_from(["setFees", "setTaxFee", "mint", "_rebalance", "setMaxTx"]),
    restricted_to=st.sampled_from([None, "onlyOwner", "TIMELOCK", "MINTER_ROLE"]),
    upper_bound_pct=pct,
    hidden=st.booleans(),
)

contracts = st.builds(
    ContractMetadata,
    name=st.just("Fuzz"),
    symbol=st.just("FZZ"),
    total_supply=st.one_of(st.none(), st.floats(1, 1e15, allow_nan=False)),
    source_verified=st.booleans(),
    owner=st.one_of(st.none(), addresses, st.just("0x0000000000000000000000000000000000000000")),
    owner_type=st.sampled_from(list(OwnerType)),
    proxy_kind=st.sampled_from(list(ProxyKind)),
    implementation_verified=st.one_of(st.none(), st.booleans()),
    upgrade_delay_hours=st.one_of(st.none(), st.floats(0, 1000, allow_nan=False)),
    buy_tax_pct=pct,
    sell_tax_pct=pct,
    fee_setters=st.lists(function_sigs, max_size=2),
    max_tx_setters=st.lists(function_sigs, max_size=1),
    mint_functions=st.lists(function_sigs, max_size=2),
    has_blacklist=st.booleans(),
    has_whitelist_only_transfers=st.booleans(),
    can_pause_trading=st.booleans(),
)


@st.composite
def pools(draw):
    shares = draw(_normalised_shares(4))
    holdings = [
        LPHolding(
            holder=draw(addresses),
            share=s,
            locker=draw(st.one_of(st.none(), st.sampled_from(list(LockerKind)))),
            unlock_at=draw(st.one_of(st.none(), future_dt)),
            is_deployer_or_owner=draw(st.booleans()),
        )
        for s in shares
    ]
    return LiquidityPool(
        dex="uniswap_v2",
        pair_address=draw(addresses),
        quote_token="WETH",
        liquidity_usd=draw(st.floats(0, 1e8, allow_nan=False)),
        lp_holdings=holdings,
    )


@st.composite
def sell_sims(draw):
    attempted = draw(st.integers(0, 30))
    return SellSimulation(
        attempted=attempted,
        succeeded=draw(st.integers(0, attempted)),
        buy_gas=draw(st.one_of(st.none(), st.integers(21_000, 2_000_000))),
        sell_gas=draw(st.one_of(st.none(), st.integers(21_000, 5_000_000))),
        effective_sell_tax_pct=draw(pct),
        revert_reason=draw(st.one_of(st.none(), st.just("TRANSFER_FAILED"))),
    )


@st.composite
def holder_dists(draw):
    shares = draw(_normalised_shares(15))
    return HolderDistribution(
        holder_count=draw(st.one_of(st.none(), st.integers(0, 100_000))),
        top_holders=[Holder(address=draw(addresses), share=s, is_dex_or_burn=draw(st.booleans())) for s in shares],
    )


deployers = st.builds(
    DeployerProfile,
    address=addresses,
    first_seen=st.one_of(st.none(), past_dt),
    first_funding_kind=st.sampled_from(list(FundingSourceKind)),
    contracts_deployed=st.one_of(st.none(), st.integers(0, 500)),
    prior_rug_count=st.one_of(st.none(), st.integers(0, 20)),
    linked_scam_addresses=st.lists(addresses, max_size=2),
)

snapshots = st.builds(
    TokenSnapshot,
    chain=st.sampled_from(["ethereum", "bsc", "arbitrum", "base"]),
    address=addresses,
    observed_at=st.just(OBSERVED_AT),
    price_usd=st.one_of(st.none(), st.floats(0, 1e4, allow_nan=False)),
    contract=contracts,
    pools=st.lists(pools(), max_size=2),
    sell_simulation=st.one_of(st.none(), sell_sims()),
    holders=holder_dists(),
    deployer=st.one_of(st.none(), deployers),
)

PROPERTY_SETTINGS = settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROPERTY_SETTINGS
@given(snapshot=snapshots)
def test_engine_never_crashes_and_report_is_well_formed(engine, config, snapshot):
    report = engine.score(snapshot, provider="fuzz")

    assert 0.0 <= report.risk_score <= 100.0
    assert 0.0 <= report.rule_score <= 100.0
    assert report.rule_score <= min(100.0, sum(config.category_caps.values()))

    if report.hard_fail:
        assert report.risk_tier is RiskTier.CRITICAL
        assert report.risk_score >= config.scoring.hard_fail_score_floor
        assert report.hard_fail_reasons
    else:
        assert report.risk_tier is engine.tier_for(report.risk_score)

    for row in report.breakdown:
        assert row.capped_points <= row.cap and row.capped_points <= row.raw_points
    for flag in report.flags:
        assert flag.code in config.flags and flag.points == config.flags[flag.code]
    assert report.ml is not None and 0.0 <= report.ml.rug_probability <= 1.0


@PROPERTY_SETTINGS
@given(snapshot=snapshots)
def test_snapshot_and_report_json_roundtrip(engine, snapshot):
    assert TokenSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    report = engine.score(snapshot, provider="fuzz")
    assert RiskReport.model_validate_json(report.to_json()).risk_score == report.risk_score


@PROPERTY_SETTINGS
@given(snapshot=snapshots)
def test_scoring_is_deterministic(engine, snapshot):
    a = engine.score(snapshot, provider="fuzz")
    b = engine.score(snapshot, provider="fuzz")
    assert a.risk_score == b.risk_score and [f.code for f in a.flags] == [f.code for f in b.flags]


@pytest.mark.parametrize("field,value", [("sell_tax_pct", 100.0), ("buy_tax_pct", 95.0)])
def test_extreme_declared_tax_always_hard_fails(engine, benign, field, value):
    setattr(benign.contract, field, value)
    assert engine.score(benign, provider="t").hard_fail
