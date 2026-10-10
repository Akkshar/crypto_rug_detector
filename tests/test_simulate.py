"""Sell-simulation tests against a fake router that honours amountOutMin and
state overrides exactly the way a real node would."""

from __future__ import annotations

from eth_abi import encode
from eth_utils import to_checksum_address

from config.chains import load_chain_registry
from src.ingestion.evm import abi
from src.ingestion.evm.client import CallReverted, FakeEvmClient
from src.ingestion.evm.pools import PoolDiscovery
from src.ingestion.evm.simulate import (
    PROBE_SENDER,
    SellSimulator,
    allowance_slot_key,
    balance_slot_key,
)
from src.models.risk_report import LiquidityPool

REG = load_chain_registry()
ETH = REG.get("ethereum")
WETH = ETH.wrapped_native
ROUTER = ETH.dexes["uniswap_v2"].router
TOKEN = to_checksum_address("0x" + "aa" * 20)
PAIR = to_checksum_address("0x" + "bb" * 20)
BALANCE_SLOT = 3
ALLOWANCE_SLOT = 4
RATE = 100_000  # tokens per wei of ETH, both directions


def make_client(*, buy_tax: float = 0.0, sell_tax: float = 0.0, honeypot: bool = False, max_sell: int | None = None):
    c = FakeEvmClient(chain_id=1)
    c.returns(abi.erc20_decimals(TOKEN), 18)

    def planted(state, key):
        diff = (state or {}).get(TOKEN, {}).get("stateDiff", {})
        raw = diff.get(key)
        return int(raw, 16) if raw else 0

    def amounts_out(call, state, sender):
        amount_in, path = call.args
        out = amount_in * RATE if path[0] == WETH else amount_in // RATE
        return encode(["uint256[]"], [[amount_in, out]])

    def balance_of(call, state, sender):
        (holder,) = call.args
        return encode(["uint256"], [planted(state, balance_slot_key(holder, BALANCE_SLOT))])

    def allowance(call, state, sender):
        owner, spender = call.args
        return encode(["uint256"], [planted(state, allowance_slot_key(owner, spender, ALLOWANCE_SLOT))])

    def buy(call, state, sender):
        min_out, _path, _to, _ = call.args
        actual = int(call.value * RATE * (1 - buy_tax))
        if min_out > actual:
            raise CallReverted(call, "UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT")
        return b""

    def sell(call, state, sender):
        amount_in, min_out, _path, _to, _ = call.args
        if honeypot:
            raise CallReverted(call, "TransferHelper: TRANSFER_FROM_FAILED")
        if planted(state, balance_slot_key(sender, BALANCE_SLOT)) < amount_in:
            raise CallReverted(call, "ERC20: transfer amount exceeds balance")
        if planted(state, allowance_slot_key(sender, ROUTER, ALLOWANCE_SLOT)) < amount_in:
            raise CallReverted(call, "ERC20: insufficient allowance")
        if max_sell is not None and amount_in > max_sell:
            raise CallReverted(call, "Max sell exceeded")
        actual = int((amount_in // RATE) * (1 - sell_tax))
        if min_out > actual:
            raise CallReverted(call, "UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT")
        return b""

    c.on_selector("getAmountsOut(uint256,address[])", amounts_out, to=ROUTER)
    c.on_selector("balanceOf(address)", balance_of, to=TOKEN)
    c.on_selector("allowance(address,address)", allowance, to=TOKEN)
    c.on_selector(
        "swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)", buy, to=ROUTER
    )
    c.on_selector(
        "swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)", sell, to=ROUTER
    )
    return c


def pool(dex: str = "uniswap_v2", quote: str = "WETH") -> LiquidityPool:
    return LiquidityPool(dex=dex, pair_address=PAIR, quote_token=quote, liquidity_usd=50_000)


def simulator(client: FakeEvmClient) -> SellSimulator:
    return SellSimulator(client, ETH, PoolDiscovery(client, ETH, REG), attempts=3)


def test_clean_token_round_trip():
    sim = simulator(make_client(buy_tax=0.01, sell_tax=0.01)).simulate(TOKEN, pool())
    assert sim is not None
    assert sim.attempted == 3 and sim.succeeded == 3 and sim.revert_reason is None
    assert sim.effective_buy_tax_pct is not None and abs(sim.effective_buy_tax_pct - 1.0) < 0.2
    assert sim.effective_sell_tax_pct is not None and abs(sim.effective_sell_tax_pct - 1.0) < 0.2
    assert sim.buy_gas and sim.sell_gas


def test_hidden_sell_tax_is_measured():
    sim = simulator(make_client(buy_tax=0.02, sell_tax=0.40)).simulate(TOKEN, pool())
    assert sim.succeeded == 3
    assert 39.5 <= sim.effective_sell_tax_pct <= 40.5
    assert 1.8 <= sim.effective_buy_tax_pct <= 2.2


def test_honeypot_blocks_every_sell():
    sim = simulator(make_client(honeypot=True)).simulate(TOKEN, pool())
    assert sim.succeeded == 0 and sim.attempted == 3
    assert "TRANSFER_FROM_FAILED" in sim.revert_reason
    assert sim.buy_gas is not None  # the buy itself worked – the classic trap


def test_size_gated_sell_passes_only_small_amounts():
    received = (10**16) * RATE  # what the 0.01 ETH buy yields
    sim = simulator(make_client(max_sell=received // 2)).simulate(TOKEN, pool())
    assert sim.succeeded == 2  # full fails, half and quarter pass
    assert "Max sell" in sim.revert_reason


def test_skips_v3_and_non_native_quotes():
    s = simulator(make_client())
    assert s.simulate(TOKEN, pool(dex="uniswap_v3_3000")) is None
    assert s.simulate(TOKEN, pool(quote="USDC")) is None


def test_unquotable_buy_and_missing_slot():
    c = FakeEvmClient(chain_id=1)
    c.returns(abi.erc20_decimals(TOKEN), 18)
    sim = simulator(c).simulate(TOKEN, pool())
    assert sim.succeeded == 0 and "quote" in sim.revert_reason

    c = make_client()
    # make balanceOf ignore overrides → slot search fails
    c.handlers = [h for h in c.handlers if h[1] != abi.selector("balanceOf(address)")]
    c.on_selector("balanceOf(address)", lambda call, s, snd: encode(["uint256"], [0]), to=TOKEN)
    sim = simulator(c).simulate(TOKEN, pool())
    assert sim.succeeded == 0 and "balances storage slot" in sim.revert_reason


def test_slot_keys_are_solidity_layout():
    # keccak(abi.encode(holder, slot)) – the canonical mapping(address => uint) layout
    from eth_utils import keccak

    assert balance_slot_key(PROBE_SENDER, 0) == "0x" + keccak(encode(["address", "uint256"], [PROBE_SENDER, 0])).hex()
    assert allowance_slot_key(PROBE_SENDER, ROUTER, 1) != balance_slot_key(PROBE_SENDER, 1)
