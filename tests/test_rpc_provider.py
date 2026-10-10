"""End-to-end live-provider test with a fake chain and a fake explorer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from config.chains import load_chain_registry
from config.settings import Settings
from src.ingestion.base import ProviderError
from src.ingestion.evm import abi
from src.ingestion.evm.client import FakeEvmClient
from src.ingestion.evm.deployer import trace_deployer
from src.ingestion.evm.explorer import ContractSource, ExplorerTx, FakeExplorer
from src.ingestion.rpc_provider import RPCDataProvider
from src.models.risk_report import ZERO_ADDRESS, Chain, FundingSourceKind, OwnerType, RiskTier
from tests.test_evm_capabilities import HONEYPOT_SOURCE
from tests.test_evm_pools import DEPLOYER, PAIR, PRICE_PAIR, TOKEN, UNI_V2, USDC, WETH, transfer_log

REG = load_chain_registry()
ETH = REG.get("ethereum")
TORNADO_10 = ETH.mixers["Tornado Cash: 10 ETH"]
HOLDER_A = to_checksum_address("0x" + "a1" * 20)
SETTINGS = Settings(_env_file=None)


def ts(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=timezone.utc)


def build_chain() -> FakeEvmClient:
    c = FakeEvmClient(chain_id=1, latest_block=100)
    c.with_code(TOKEN, b"\x60\x80" + function_signature_to_4byte_selector("setSellFee(uint256)"))
    c.returns(abi.erc20_name(TOKEN), "SafeMoonElon2.0")
    c.returns(abi.erc20_symbol(TOKEN), "SME2")
    c.returns(abi.erc20_decimals(TOKEN), 18)
    c.returns(abi.erc20_total_supply(TOKEN), 1_000_000 * 10**18)
    c.returns(abi.owner(TOKEN), DEPLOYER)  # EOA owner (no code at DEPLOYER)
    c.returns(abi.Call(TOKEN, "sellFee()", returns=("uint256",)), 99)
    c.returns(abi.Call(TOKEN, "buyFee()", returns=("uint256",)), 2)
    # pricing + pools
    c.returns(abi.v2_token0(PRICE_PAIR), USDC)
    c.returns(abi.v2_get_reserves(PRICE_PAIR), 2_500_000 * 10**6, 1_000 * 10**18, 0)
    c.returns(abi.erc20_decimals(USDC), 6)
    c.returns(abi.erc20_decimals(WETH), 18)
    for dex in ETH.dexes.values():
        for quote in ETH.quote_tokens.values():
            if dex.kind == "v2":
                c.returns(abi.v2_get_pair(dex.factory, TOKEN, quote), ZERO_ADDRESS)
            else:
                for fee in dex.fee_tiers:
                    c.returns(abi.v3_get_pool(dex.factory, TOKEN, quote, fee), ZERO_ADDRESS)
    c.returns(abi.v2_get_pair(UNI_V2, TOKEN, WETH), PAIR)
    c.returns(abi.v2_token0(PAIR), TOKEN)
    c.returns(abi.v2_get_reserves(PAIR), 400_000 * 10**18, 8 * 10**18, 0)
    c.with_code(PAIR)
    # LP entirely in the deployer's wallet; token supply: 40% in pair, 10% holder A, 50% deployer
    c.logs += [
        transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 1_000, block=10),
        transfer_log(TOKEN, ZERO_ADDRESS, DEPLOYER, 1_000_000 * 10**18, block=9),
        transfer_log(TOKEN, DEPLOYER, PAIR, 400_000 * 10**18, block=10),
        transfer_log(TOKEN, DEPLOYER, HOLDER_A, 100_000 * 10**18, block=11),
    ]
    return c


@pytest.fixture
def chain() -> FakeEvmClient:
    return build_chain()


def build_explorer() -> FakeExplorer:
    ex = FakeExplorer()
    ex.sources[TOKEN] = ContractSource(
        verified=True, contract_name="SafeMoonElon", compiler_version="v0.8.19", abi=[], source=HONEYPOT_SOURCE
    )
    ex.creations[TOKEN] = (DEPLOYER, "0x" + "01" * 32)
    ex.txs[DEPLOYER] = [
        ExplorerTx(
            hash="0x" + "02" * 32, sender=TORNADO_10, to=DEPLOYER, value=10**19, timestamp=ts(7), block_number=1
        ),
        ExplorerTx(
            hash="0x" + "01" * 32,
            sender=DEPLOYER,
            to=None,
            value=0,
            timestamp=ts(8),
            block_number=5,
            is_contract_creation=True,
        ),
        ExplorerTx(
            hash="0x" + "03" * 32,
            sender=DEPLOYER,
            to=None,
            value=0,
            timestamp=ts(9),
            block_number=6,
            is_contract_creation=True,
        ),
    ]
    return ex


@pytest.fixture
def explorer() -> FakeExplorer:
    return build_explorer()


def make_provider(chain, explorer=None, **kw) -> RPCDataProvider:
    return RPCDataProvider(
        SETTINGS,
        registry=REG,
        client_factory=lambda _chain: chain,
        explorer_factory=lambda _chain: explorer,
        simulate_sells=False,
        **kw,
    )


def test_full_snapshot_from_fake_chain(chain, explorer, engine):
    provider = make_provider(chain, explorer, prior_rug_lookup=lambda addr: (2, ["0x" + "ff" * 20]))
    snap = asyncio.run(provider.fetch_snapshot(Chain.ETHEREUM, TOKEN))

    c = snap.contract
    assert (c.name, c.symbol, c.decimals, c.total_supply) == ("SafeMoonElon2.0", "SME2", 18, 1_000_000)
    assert c.owner == DEPLOYER and c.owner_type is OwnerType.EOA and c.ownership_renounced is False
    assert c.source_verified and c.compiler_version == "v0.8.19"
    assert c.sell_tax_pct == 99.0 and c.buy_tax_pct == 2.0
    assert {f.name for f in c.fee_setters} == {"setSellFee", "setBuyFee"}
    assert [f.name for f in c.mint_functions] == ["_rebalanceSupply"] and c.mint_functions[0].hidden
    assert c.has_blacklist and c.has_whitelist_only_transfers and c.can_pause_trading and c.has_selfdestruct

    d = snap.deployer
    assert d.address == DEPLOYER and d.first_funding_kind is FundingSourceKind.MIXER
    assert d.first_funding_label == "Tornado Cash: 10 ETH" and d.first_seen == ts(7)
    assert d.contracts_deployed == 2 and d.prior_rug_count == 2 and len(d.linked_scam_addresses) == 1

    assert len(snap.pools) == 1 and snap.pools[0].liquidity_usd == pytest.approx(2 * 8 * 2500)
    assert snap.pools[0].insider_share == pytest.approx(1.0) and snap.pools[0].locked_share == 0.0
    assert snap.price_usd == pytest.approx(8 * 2500 / 400_000)
    assert snap.fdv_usd == pytest.approx(1_000_000 * 8 * 2500 / 400_000)

    by_addr = {h.address: h for h in snap.holders.top_holders}
    assert by_addr[PAIR].is_dex_or_burn and by_addr[PAIR].label == "uniswap_v2_pair"
    assert by_addr[DEPLOYER].share == pytest.approx(0.5) and by_addr[HOLDER_A].share == pytest.approx(0.1)
    assert snap.holders.holder_count == 3
    assert snap.sell_simulation is None  # simulate_sells=False

    report = engine.score(snap, provider="rpc")
    assert report.risk_tier is RiskTier.CRITICAL and report.hard_fail
    assert {"HIDDEN_MINT_FUNCTION", "LP_MOSTLY_UNLOCKED", "DEPLOYER_MIXER_FUNDED", "SELFDESTRUCT_CAPABILITY"} <= {
        f.code for f in report.flags
    }
    assert report.missing_data == []


def test_degraded_snapshot_without_explorer_or_pools(chain, engine):
    for dex in ETH.dexes.values():
        if dex.kind == "v2":
            chain.returns(abi.v2_get_pair(dex.factory, TOKEN, WETH), ZERO_ADDRESS)
    chain.reverts(abi.owner(TOKEN))
    snap = make_provider(chain, explorer=None).fetch_sync(Chain.ETHEREUM, TOKEN)

    assert snap.contract.source_verified is False and snap.deployer is None
    assert snap.contract.owner is None and snap.contract.owner_type is OwnerType.UNKNOWN
    assert [f.name for f in snap.contract.fee_setters] == ["setSellFee"]  # from bytecode probing
    assert snap.pools == [] and snap.price_usd is None
    assert snap.holders.holder_count == 3  # token transfer logs still replay

    report = engine.score(snap, provider="rpc")
    assert "deployer_tracer" in report.missing_data
    assert {"NO_LIQUIDITY_POOL", "SOURCE_UNVERIFIED", "SELL_SIMULATION_UNAVAILABLE"} <= {f.code for f in report.flags}


def test_clone_is_analysed_through_its_implementation(chain, engine):
    from src.ingestion.evm.fingerprint import EIP1167_PREFIX, EIP1167_SUFFIX, fingerprint
    from src.models.risk_report import ProxyKind

    impl = to_checksum_address("0x" + "4f" * 20)
    impl_code = b"\x60\x80" + function_signature_to_4byte_selector("setSellFee(uint256)") + b"\x60\x40"
    chain.with_code(TOKEN, EIP1167_PREFIX + bytes.fromhex(impl[2:]) + EIP1167_SUFFIX).with_code(impl, impl_code)
    snap = make_provider(chain).fetch_sync(Chain.ETHEREUM, TOKEN)

    assert snap.contract.proxy_kind is ProxyKind.MINIMAL and snap.contract.implementation == impl
    assert [f.name for f in snap.contract.fee_setters] == ["setSellFee"]  # probed from the implementation
    assert snap.contract.code_hash == fingerprint(impl_code).code_hash  # lineage keyed on the shared logic
    report = engine.score(snap, provider="rpc")
    assert "EXTERNAL_DELEGATECALL" not in {f.code for f in report.flags}
    assert "PROXY_UNVERIFIED_IMPLEMENTATION" not in {
        f.code for f in report.flags
    }  # minimal proxies are not upgradeable


def test_deployer_derived_from_mint_without_explorer(chain):
    from src.ingestion.evm.client import TxInfo

    mint_tx = "0x" + f"{9:064x}"  # transfer_log() keys tx hashes by block; the TOKEN mint is at block 9
    chain.transactions[mint_tx] = TxInfo(mint_tx, DEPLOYER, None, 0, 9)
    snap = make_provider(chain, explorer=None, prior_rug_lookup=lambda a: (1, ["0x" + "ff" * 20])).fetch_sync(
        Chain.ETHEREUM, TOKEN
    )
    assert snap.deployer is not None and snap.deployer.address == DEPLOYER
    assert snap.deployer.prior_rug_count == 1 and snap.deployer.first_seen is None
    assert snap.pools[0].insider_share == pytest.approx(1.0)  # LP in the derived deployer's wallet is now recognised


def test_not_a_contract(chain):
    with pytest.raises(ProviderError, match="no bytecode"):
        make_provider(chain).fetch_sync(Chain.ETHEREUM, "0x" + "09" * 20)


def test_owner_classification(chain):
    provider = make_provider(chain)
    timelock = "0x" + "71" * 20
    chain.with_code(timelock).returns(abi.Call(timelock, "getMinDelay()", returns=("uint256",)), 86_400)
    chain.returns(abi.owner(TOKEN), timelock)
    assert provider._owner(chain, TOKEN) == (timelock, OwnerType.TIMELOCK)

    plain = "0x" + "72" * 20
    chain.with_code(plain, b"\x60\x80" * 200).returns(abi.owner(TOKEN), plain)
    assert provider._owner(chain, TOKEN)[1] is OwnerType.CONTRACT

    chain.returns(abi.owner(TOKEN), "0x000000000000000000000000000000000000dEaD")
    assert provider._owner(chain, TOKEN)[1] is OwnerType.RENOUNCED


def test_declared_tax_normalisation(chain):
    provider = make_provider(chain)
    chain.returns(abi.Call(TOKEN, "sellFee()", returns=("uint256",)), 2500)  # basis points
    assert provider._declared_taxes(chain, TOKEN)["sell"] == 25.0
    chain.returns(abi.Call(TOKEN, "sellFee()", returns=("uint256",)), 10**30)  # nonsense magnitude → ignored
    assert "sell" not in provider._declared_taxes(chain, TOKEN)


def test_trace_deployer_without_funding_tx(explorer):
    explorer.txs[DEPLOYER] = [explorer.txs[DEPLOYER][1]]  # only the creation tx
    d = trace_deployer(explorer, ETH, TOKEN)
    assert d.first_funding_kind is FundingSourceKind.UNKNOWN and d.first_funding_source is None
    assert d.contracts_deployed == 1 and d.prior_rug_count is None
    assert trace_deployer(FakeExplorer(), ETH, TOKEN) is None


def test_supports_depends_on_rpc_url(chain):
    assert asyncio.run(make_provider(chain).supports(Chain.BASE)) is True  # injected client factory
    assert asyncio.run(RPCDataProvider(SETTINGS, registry=REG).supports(Chain.BASE)) is False


def test_time_travel_snapshot_excludes_later_logs(chain, explorer):
    chain.timestamps[10] = ts(9)
    provider = make_provider(chain, explorer)
    at_launch = provider.fetch_sync(Chain.ETHEREUM, TOKEN, block=10)  # before the HOLDER_A transfer at block 11
    assert at_launch.block_number == 10 and at_launch.observed_at == ts(9)
    assert {h.address for h in at_launch.holders.top_holders} == {DEPLOYER, PAIR}
    assert at_launch.holders.holder_count == 2

    latest = provider.fetch_sync(Chain.ETHEREUM, TOKEN)
    assert latest.block_number == 100 and latest.holders.holder_count == 3
    assert chain.block is None  # the provider pinned a clone, not the shared client


def test_metadata_and_taxes_are_batched(chain):
    chain.batches.clear()
    make_provider(chain).fetch_sync(Chain.ETHEREUM, TOKEN)
    # metadata (4), declared-tax getters (13), factory probes, pool state – no per-call fan-out
    assert 4 in chain.batches and 13 in chain.batches and len(chain.batches) >= 4


def test_abi_encoding_roundtrip():
    assert abi.decode(("uint256[]",), encode(["uint256[]"], [[1, 2]])) == ((1, 2),)
