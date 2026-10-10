"""Uniswap-v3 paths: NPM position ownership, v3 swap decoding, v3 sell simulation, bisected slot search."""

from __future__ import annotations

import math

import pytest
from eth_abi import encode
from eth_utils import to_checksum_address

from config.chains import load_chain_registry
from src.ingestion.evm import abi
from src.ingestion.evm.client import CallReverted, FakeEvmClient, LogEntry, TxInfo, topic_address
from src.ingestion.evm.pools import PoolDiscovery
from src.ingestion.evm.simulate import (
    PROBE_SENDER,
    SellSimulator,
    allowance_slot_key,
    balance_slot_key,
    pool_fee_from_label,
)
from src.ingestion.evm.trades import V3_SWAP_TOPIC, analyse_swaps
from src.ingestion.evm.v3_positions import TRANSFER_TOPIC, V3_MINT_TOPIC, pool_created_block, v3_lp_holdings
from src.models.risk_report import ZERO_ADDRESS, LiquidityPool, LockerKind

REG = load_chain_registry()
BASE = REG.get("base")
WETH = BASE.wrapped_native
V3 = BASE.dexes["uniswap_v3"]
NPM = V3.position_manager
UNCX_V3 = BASE.lockers["uncx"][0]
TOKEN = to_checksum_address("0x" + "aa" * 20)
POOL = to_checksum_address("0x" + "cc" * 20)
DEPLOYER = to_checksum_address("0x" + "dd" * 20)
ALICE = to_checksum_address("0x" + "a1" * 20)
ROUTER = V3.router
FEE = 3000


def npm_mint(token_id: int, to: str, block: int) -> LogEntry:
    topics = (TRANSFER_TOPIC, topic_address(ZERO_ADDRESS), topic_address(to), token_id.to_bytes(32, "big"))
    return LogEntry(NPM, topics, b"", block, "0x" + f"{token_id:064x}")


def pool_mint(block: int) -> LogEntry:
    return LogEntry(POOL, (V3_MINT_TOPIC, topic_address(NPM)), b"\x00" * 96, block, "0x" + f"{block:064x}")


def position(token0: str, token1: str, fee: int, liquidity: int):
    return (0, ZERO_ADDRESS, token0, token1, fee, -887220, 887220, liquidity, 0, 0, 0, 0)


@pytest.fixture
def chain() -> FakeEvmClient:
    c = FakeEvmClient(chain_id=8453, latest_block=1_000)
    token0, token1 = sorted((TOKEN, WETH), key=str.lower)
    c.logs += [
        pool_mint(100),
        npm_mint(1, UNCX_V3, 100),
        npm_mint(2, DEPLOYER, 101),
        npm_mint(3, ALICE, 102),
        npm_mint(4, ALICE, 103),
    ]
    c.with_code(UNCX_V3)
    # 1 & 2 belong to our pool; 3 is another pool; 4 is ours but empty
    for tid, pos in {
        1: position(token0, token1, FEE, 900),
        2: position(token0, token1, FEE, 100),
        3: position(token0, to_checksum_address("0x" + "ee" * 20), FEE, 500),
        4: position(token0, token1, FEE, 0),
    }.items():
        from src.ingestion.evm.v3_positions import positions_call

        c.returns(positions_call(NPM, tid), *pos)
    for tid, holder in {1: UNCX_V3, 2: DEPLOYER, 3: ALICE, 4: ALICE}.items():
        c.returns(abi.Call(NPM, "ownerOf(uint256)", (tid,), returns=("address",)), holder)
    return c


def test_pool_created_block_and_positions(chain):
    assert pool_created_block(chain, POOL) == 100
    token0, token1 = sorted((TOKEN, WETH), key=str.lower)
    holdings = v3_lp_holdings(
        chain,
        BASE,
        REG,
        npm=NPM,
        pool=POOL,
        token0=token0,
        token1=token1,
        fee=FEE,
        from_block=100,
        deployer=DEPLOYER,
        owner=None,
    )
    by = {h.holder: h for h in holdings}
    assert by[UNCX_V3].locker is LockerKind.UNCX and by[UNCX_V3].share == pytest.approx(0.9)
    assert by[DEPLOYER].is_deployer_or_owner and by[DEPLOYER].share == pytest.approx(0.1)
    assert ALICE not in by  # her positions are in another pool / empty
    assert chain.batches[-2:] == [4, 2]  # positions() for 4 ids, ownerOf() for the 2 matches – both batched


def test_discovery_resolves_v3_lock_status(chain):
    _token0, _ = sorted((TOKEN, WETH), key=str.lower)
    for dex in BASE.dexes.values():
        for quote in BASE.quote_tokens.values():
            if dex.kind == "v2":
                chain.returns(abi.v2_get_pair(dex.factory, TOKEN, quote), ZERO_ADDRESS)
            else:
                for fee in dex.fee_tiers:
                    chain.returns(abi.v3_get_pool(dex.factory, TOKEN, quote, fee), ZERO_ADDRESS)
    chain.returns(abi.v3_get_pool(V3.factory, TOKEN, WETH, FEE), POOL)
    chain.returns(abi.erc20_balance_of(WETH, POOL), 5 * 10**18)
    chain.returns(abi.erc20_balance_of(TOKEN, POOL), 1_000_000 * 10**18)
    chain.returns(abi.erc20_decimals(WETH), 18).returns(abi.erc20_decimals(TOKEN), 18)
    price_pool = BASE.native_price_source.address
    chain.returns(abi.v2_token0(price_pool), WETH)
    chain.returns(abi.v3_slot0(price_pool), int(math.sqrt(3000e6 / 1e18) * 2**96), 0, 0, 0, 0, 0, True)
    chain.returns(abi.erc20_decimals(BASE.quote_tokens["USDC"]), 6)

    pools = PoolDiscovery(chain, BASE, REG).discover(TOKEN, deployer=DEPLOYER, owner=None)
    assert [p.dex for p in pools] == ["uniswap_v3_3000"]
    pool = pools[0]
    assert (
        pool.created_block == 100
        and pool.locked_share == pytest.approx(0.9)
        and pool.insider_share == pytest.approx(0.1)
    )
    assert pool.liquidity_usd == pytest.approx(2 * 5 * 3000)


def test_v3_swap_decoding():
    c = FakeEvmClient(latest_block=50)
    token0, _ = sorted((TOKEN, WETH), key=str.lower)
    token_is_token0 = token0 == TOKEN

    def swap(amount_token: int, amount_quote: int, block: int, trader: str) -> None:
        a0, a1 = (amount_token, amount_quote) if token_is_token0 else (amount_quote, amount_token)
        data = a0.to_bytes(32, "big", signed=True) + a1.to_bytes(32, "big", signed=True) + b"\x00" * 96
        tx = "0x" + f"{block:064x}"
        c.logs.append(LogEntry(POOL, (V3_SWAP_TOPIC, topic_address(ROUTER), topic_address(trader)), data, block, tx))
        c.transactions[tx] = TxInfo(tx, trader, ROUTER, 0, block)

    swap(-5 * 10**18, 10**18, 10, ALICE)  # pool pays out token, takes 1 WETH → buy
    swap(2 * 10**18, -4 * 10**17, 11, ALICE)  # pool takes token, pays 0.4 WETH → sell
    t = analyse_swaps(c, POOL, token_is_token0=token_is_token0, quote_decimals=18, from_block=0, kind="v3")
    assert (t.buys, t.sells) == (1, 1) and t.buy_volume_quote == 1.0 and t.sell_volume_quote == 0.4
    assert t.round_trip_wallets == 1 and t.unique_traders == 1
    assert (
        analyse_swaps(c, POOL, token_is_token0=token_is_token0, quote_decimals=18, from_block=0).swaps == 0
    )  # v2 topic


# --------------------------------------------------------------------------- #
# v3 sell simulation against a fake SwapRouter02
# --------------------------------------------------------------------------- #

BALANCE_SLOT, ALLOWANCE_SLOT = 0, 1
TOKENS_PER_WEI = 2_000  # 1 WETH = 2000 tokens (raw units, both 18 dp)


def make_v3_client(*, sell_tax: float = 0.0, honeypot: bool = False) -> FakeEvmClient:
    c = FakeEvmClient(chain_id=8453)
    token0, _ = sorted((TOKEN, WETH), key=str.lower)
    # sqrtPrice such that token1/token0 price matches TOKENS_PER_WEI (or its inverse)
    price01 = (1 / TOKENS_PER_WEI) if token0 == TOKEN else TOKENS_PER_WEI
    c.returns(abi.v2_token0(POOL), token0)
    c.returns(abi.v3_slot0(POOL), int(math.sqrt(price01) * 2**96), 0, 0, 0, 0, 0, True)
    c.returns(abi.erc20_decimals(TOKEN), 18)

    def planted(state, key):
        diff = (state or {}).get(TOKEN, {}).get("stateDiff", {})
        raw = diff.get(key)
        return int(raw, 16) if raw else 0

    c.on_selector(
        "balanceOf(address)",
        lambda call, s, snd: encode(["uint256"], [planted(s, balance_slot_key(call.args[0], BALANCE_SLOT))]),
        to=TOKEN,
    )
    c.on_selector(
        "allowance(address,address)",
        lambda call, s, snd: encode(
            ["uint256"], [planted(s, allowance_slot_key(call.args[0], call.args[1], ALLOWANCE_SLOT))]
        ),
        to=TOKEN,
    )

    fee_factor = 1 - FEE / 1_000_000

    def exact_input(call, state, sender):
        (params,) = call.args
        token_in, _token_out, fee, recipient, amount_in, min_out, _limit = params  # SwapRouter02 layout
        assert fee == FEE and recipient == PROBE_SENDER
        if token_in == WETH:
            assert call.value == amount_in
            actual = int(amount_in * TOKENS_PER_WEI * fee_factor)
        else:
            if honeypot:
                raise CallReverted(call, "STF")
            if planted(state, balance_slot_key(sender, BALANCE_SLOT)) < amount_in:
                raise CallReverted(call, "ERC20: transfer amount exceeds balance")
            if planted(state, allowance_slot_key(sender, ROUTER, ALLOWANCE_SLOT)) < amount_in:
                raise CallReverted(call, "ERC20: insufficient allowance")
            actual = int(amount_in / TOKENS_PER_WEI * fee_factor * (1 - sell_tax))
        if min_out > actual:
            raise CallReverted(call, "Too little received")
        return encode(["uint256"], [actual])

    c.on_selector("exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))", exact_input, to=ROUTER)
    return c


def v3_pool() -> LiquidityPool:
    return LiquidityPool(dex="uniswap_v3_3000", pair_address=POOL, quote_token="WETH", liquidity_usd=50_000)


def test_v3_simulation_clean_and_taxed_and_honeypot():
    sim = SellSimulator(make_v3_client(), BASE, PoolDiscovery(make_v3_client(), BASE, REG), attempts=2).simulate(
        TOKEN, v3_pool()
    )
    assert (
        sim is not None and sim.succeeded == 2 and sim.effective_buy_tax_pct < 0.5 and sim.effective_sell_tax_pct < 0.5
    )

    taxed = make_v3_client(sell_tax=0.30)
    sim = SellSimulator(taxed, BASE, PoolDiscovery(taxed, BASE, REG), attempts=2).simulate(TOKEN, v3_pool())
    assert sim.succeeded == 2 and 29 <= sim.effective_sell_tax_pct <= 31

    hp = make_v3_client(honeypot=True)
    sim = SellSimulator(hp, BASE, PoolDiscovery(hp, BASE, REG), attempts=2).simulate(TOKEN, v3_pool())
    assert sim.succeeded == 0 and "STF" in sim.revert_reason and sim.buy_gas is not None


def test_slot_search_is_logarithmic():
    c = make_v3_client()
    sim = SellSimulator(c, BASE, PoolDiscovery(c, BASE, REG), attempts=1, max_slot=30)
    c.calls.clear()
    assert sim._find_balance_slot(TOKEN, 12345) == BALANCE_SLOT
    balance_calls = [k for k in c.calls if k.signature == "balanceOf(address)"]
    assert len(balance_calls) <= 1 + math.ceil(math.log2(30))  # 6, not 30
    assert sim._find_allowance_key(TOKEN, ROUTER, 7) == allowance_slot_key(PROBE_SENDER, ROUTER, ALLOWANCE_SLOT)

    # a token whose getter never echoes the sentinel is ruled out in a single call
    c.handlers = [h for h in c.handlers if h[1] != abi.selector("balanceOf(address)")]
    c.on_selector("balanceOf(address)", lambda call, s, snd: encode(["uint256"], [0]), to=TOKEN)
    c.calls.clear()
    assert sim._find_balance_slot(TOKEN, 12345) is None
    assert len([k for k in c.calls if k.signature == "balanceOf(address)"]) == 1


def test_pool_fee_label_and_router_kind_defaults():
    assert pool_fee_from_label("uniswap_v3_3000") == 3000 and pool_fee_from_label("uniswap_v2") is None
    assert REG.get("ethereum").dexes["uniswap_v3"].router_kind == "swap_router"
    assert BASE.dexes["uniswap_v3"].router_kind == "swap_router02"
    assert BASE.dexes["uniswap_v2"].router_kind == "v2"
    from config.chains import DexConfig

    with pytest.raises(ValueError):
        DexConfig(kind="v2", factory=POOL, router=ROUTER, router_kind="swap_router")
