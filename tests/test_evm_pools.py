from __future__ import annotations

import pytest
from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from config.chains import load_chain_registry
from src.ingestion.evm import abi
from src.ingestion.evm.client import FakeEvmClient, LogEntry, topic_address
from src.ingestion.evm.pools import TRANSFER_TOPIC, PoolDiscovery
from src.models.risk_report import ZERO_ADDRESS, LockerKind

REG = load_chain_registry()
ETH = REG.get("ethereum")
WETH = ETH.wrapped_native
USDC = ETH.quote_tokens["USDC"]
UNI_V2 = ETH.dexes["uniswap_v2"].factory
UNCX = ETH.lockers["uncx"][0]
DEAD = "0x000000000000000000000000000000000000dEaD"

# The product checksums every address it touches, so fixtures must too.
TOKEN = to_checksum_address("0x" + "aa" * 20)
PAIR = to_checksum_address("0x" + "bb" * 20)
DEPLOYER = to_checksum_address("0x" + "dd" * 20)
PRICE_PAIR = ETH.native_price_source.address


def transfer_log(token: str, frm: str, to: str, amount: int, block: int, idx: int = 0) -> LogEntry:
    return LogEntry(
        address=to_checksum_address(token),
        topics=(TRANSFER_TOPIC, topic_address(frm), topic_address(to)),
        data=amount.to_bytes(32, "big"),
        block_number=block,
        tx_hash="0x" + f"{block:064x}",
        log_index=idx,
    )


@pytest.fixture
def client() -> FakeEvmClient:
    c = FakeEvmClient(chain_id=1, latest_block=100)
    # native price: USDC/WETH v2 pair, token0 = USDC (6 dp). 2,500,000 USDC vs 1,000 WETH → $2,500
    c.returns(abi.v2_token0(PRICE_PAIR), USDC)
    c.returns(abi.v2_get_reserves(PRICE_PAIR), 2_500_000 * 10**6, 1_000 * 10**18, 0)
    c.returns(abi.erc20_decimals(USDC), 6)
    c.returns(abi.erc20_decimals(WETH), 18)
    c.returns(abi.erc20_decimals(TOKEN), 18)
    # every factory/quote combination says "no pool" unless programmed otherwise
    for dex in ETH.dexes.values():
        for quote in ETH.quote_tokens.values():
            if dex.kind == "v2":
                c.returns(abi.v2_get_pair(dex.factory, TOKEN, quote), ZERO_ADDRESS)
            else:
                for fee in dex.fee_tiers:
                    c.returns(abi.v3_get_pool(dex.factory, TOKEN, quote, fee), ZERO_ADDRESS)
    return c


def test_native_price_from_v2_pair(client):
    d = PoolDiscovery(client, ETH, REG)
    price = d.native_price()
    assert price is not None and price.usd == pytest.approx(2500.0)
    assert d.quote_usd("USDC", price) == 1.0 and d.quote_usd("WETH", price) == 2500.0


def test_native_price_from_v3_pool():
    base = REG.get("base")
    pool = base.native_price_source.address
    c = FakeEvmClient(chain_id=8453)
    weth, usdc = base.wrapped_native, base.quote_tokens["USDC"]
    # token0 = WETH (18dp), token1 = USDC (6dp); price of 1 WETH = 3000 USDC
    # sqrtPriceX96 = sqrt(3000e6 / 1e18) * 2^96
    import math

    sqrt_price = int(math.sqrt(3000 * 10**6 / 10**18) * 2**96)
    c.returns(abi.v2_token0(pool), weth)
    c.returns(abi.v3_slot0(pool), sqrt_price, 0, 0, 0, 0, 0, True)
    c.returns(abi.erc20_decimals(weth), 18)
    c.returns(abi.erc20_decimals(usdc), 6)
    price = PoolDiscovery(c, base, REG).native_price()
    assert price.usd == pytest.approx(3000.0, rel=1e-6)


def test_v2_discovery_with_lp_lock_classification(client):
    # token/WETH pair exists on Uniswap v2: token0 = TOKEN; 1,000,000 TOKEN vs 10 WETH
    client.returns(abi.v2_get_pair(UNI_V2, TOKEN, WETH), PAIR)
    client.returns(abi.v2_token0(PAIR), TOKEN)
    client.returns(abi.v2_get_reserves(PAIR), 1_000_000 * 10**18, 10 * 10**18, 0)
    # LP token (= pair) history: 1000 minted to deployer, 950 moved to UNCX, 30 burned
    client.logs += [
        transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 1000, block=10),
        transfer_log(PAIR, DEPLOYER, UNCX, 950, block=11),
        transfer_log(PAIR, DEPLOYER, DEAD, 30, block=12),
    ]
    client.with_code(UNCX)
    client.returns(abi.erc20_total_supply(PAIR), 1000)  # replay must reconcile with totalSupply

    pools = PoolDiscovery(client, ETH, REG).discover(TOKEN, deployer=DEPLOYER, owner=None)
    assert len(pools) == 1
    pool = pools[0]
    assert pool.dex == "uniswap_v2" and pool.pair_address == PAIR and pool.quote_token == "WETH"
    assert pool.liquidity_usd == pytest.approx(2 * 10 * 2500)  # $50k
    assert pool.token_reserve == pytest.approx(1_000_000) and pool.quote_reserve == pytest.approx(10)

    by_holder = {h.holder: h for h in pool.lp_holdings}
    assert by_holder[UNCX].locker is LockerKind.UNCX and by_holder[UNCX].share == pytest.approx(0.95)
    assert by_holder[DEAD].locker is LockerKind.BURNED and by_holder[DEAD].share == pytest.approx(0.03)
    assert by_holder[DEPLOYER].is_deployer_or_owner and by_holder[DEPLOYER].locker is None
    assert pool.locked_share == pytest.approx(0.98) and pool.insider_share == pytest.approx(0.02)


def test_v3_discovery_reports_liquidity_without_lp_holders(client):
    v3 = ETH.dexes["uniswap_v3"].factory
    pool_addr = "0x" + "cc" * 20
    client.returns(abi.v3_get_pool(v3, TOKEN, WETH, 3000), pool_addr)
    client.returns(abi.erc20_balance_of(WETH, pool_addr), 4 * 10**18)
    client.returns(abi.erc20_balance_of(TOKEN, pool_addr), 500_000 * 10**18)
    pools = PoolDiscovery(client, ETH, REG).discover(TOKEN, deployer=None, owner=None)
    assert [p.dex for p in pools] == ["uniswap_v3_3000"]
    assert pools[0].liquidity_usd == pytest.approx(2 * 4 * 2500) and pools[0].lp_holdings == []


def test_lp_replay_failure_keeps_the_pool(client, caplog):
    """A node that refuses the Transfer-log replay must not make the pool disappear (→ NO_LIQUIDITY_POOL)."""
    client.returns(abi.v2_get_pair(UNI_V2, TOKEN, WETH), PAIR)
    client.returns(abi.v2_token0(PAIR), TOKEN)
    client.returns(abi.v2_get_reserves(PAIR), 1_000_000 * 10**18, 10 * 10**18, 0)

    class NoLogs(FakeEvmClient):
        def get_logs(self, address, topics, from_block, to_block):
            raise RuntimeError("403 Client Error: Forbidden")

    nolog = NoLogs(**{k: getattr(client, k) for k in ("responses", "handlers", "code", "logs", "transactions", "gas")})
    nolog.latest_block = client.latest_block
    with caplog.at_level("WARNING", logger="rugscan.pools"):
        pools = PoolDiscovery(nolog, ETH, REG).discover(TOKEN, deployer=DEPLOYER, owner=None)
    assert len(pools) == 1 and pools[0].liquidity_usd == pytest.approx(50_000)
    assert pools[0].lp_holdings == [] and "lock status unknown" in caplog.text


def test_discovery_survives_a_broken_dex(client):
    client.reverts(abi.v2_get_pair(ETH.dexes["sushiswap_v2"].factory, TOKEN, WETH), "boom")
    assert PoolDiscovery(client, ETH, REG).discover(TOKEN, deployer=None, owner=None) == []


def test_replay_balances_and_unknown_contract_holder(client):
    other_contract = to_checksum_address("0x" + "ee" * 20)
    client.logs += [
        transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 100, block=1),
        transfer_log(PAIR, DEPLOYER, other_contract, 40, block=2),
        transfer_log(PAIR, DEPLOYER, DEPLOYER, 5, block=3),  # self-transfer: no net change
    ]
    client.with_code(other_contract)
    d = PoolDiscovery(client, ETH, REG)
    balances, first = d.replay_balances(PAIR)
    assert balances == {DEPLOYER: 60, other_contract: 40} and first == 1
    holdings, _, created_block = d.lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)
    assert created_block == 1
    assert {h.holder: h.locker for h in holdings} == {DEPLOYER: None, other_contract: LockerKind.UNKNOWN_CONTRACT}


def test_replay_rejected_when_history_exceeds_window(client, caplog):
    # Only part of the LP history is visible in the window: the replayed total (60) ≠ totalSupply (1000).
    client.logs += [transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 60, block=90)]
    client.returns(abi.erc20_total_supply(PAIR), 1000)
    d = PoolDiscovery(client, ETH, REG, replay_lookback=20)
    with caplog.at_level("WARNING", logger="rugscan.pools"):
        holdings, _, first = d.lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)
    assert holdings == [] and first == 90 and "does not reconcile" in caplog.text
    # genesis replay (no lookback) of a consistent history passes
    client.returns(abi.erc20_total_supply(PAIR), 60)
    assert len(PoolDiscovery(client, ETH, REG).lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)[0]) == 1


def test_replay_on_depth_limited_node_still_works_for_new_tokens(client):
    """History that starts inside the node's log window replays completely; older history is rejected by the
    totalSupply reconciliation rather than reported wrong."""
    client.latest_block = 100
    client.min_served_block = 50
    client.logs += [
        transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 100, block=60),
        transfer_log(PAIR, DEPLOYER, UNCX, 40, block=70),
    ]
    client.returns(abi.erc20_total_supply(PAIR), 100)
    client.with_code(UNCX)
    d = PoolDiscovery(client, ETH, REG, log_chunk=10)
    holdings, _, created = d.lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)
    assert created == 60 and {h.holder: h.share for h in holdings} == {DEPLOYER: 0.6, UNCX: 0.4}

    client.min_served_block = 65  # the mint at block 60 is now beyond the horizon → replay cannot reconcile
    assert PoolDiscovery(client, ETH, REG, log_chunk=10).lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)[0] == []


def test_burned_to_zero_is_excluded_from_lp_total(client):
    client.logs += [
        transfer_log(PAIR, ZERO_ADDRESS, DEPLOYER, 100, block=1),
        transfer_log(PAIR, DEPLOYER, ZERO_ADDRESS, 40, block=2),  # v2 burn path – supply shrinks to 60
    ]
    client.returns(abi.erc20_total_supply(PAIR), 60)
    holdings, _, _ = PoolDiscovery(client, ETH, REG).lp_holdings_v2(PAIR, deployer=DEPLOYER, owner=None)
    assert [(h.holder, h.share) for h in holdings] == [(DEPLOYER, 1.0)]


def test_log_replay_truncation_is_logged_not_fatal(client, caplog):
    client.latest_block = 10_000_000
    d = PoolDiscovery(client, ETH, REG, log_chunk=1_000_000, max_log_chunks=2)
    with caplog.at_level("WARNING", logger="rugscan.evm"):  # the window loop lives in the client now
        balances, _ = d.replay_balances(PAIR)
    assert balances == {} and "truncated" in caplog.text


def test_chunk_and_topic_helpers():
    from src.ingestion.evm.client import address_from_topic, chunked_ranges

    assert list(chunked_ranges(0, 10, 4)) == [(0, 3), (4, 7), (8, 10)]
    assert address_from_topic(topic_address(DEPLOYER)) == DEPLOYER  # round-trips to the checksummed form
    assert keccak(text="Transfer(address,address,uint256)") == TRANSFER_TOPIC
    assert encode(["uint256"], [1]) == (1).to_bytes(32, "big")
