from __future__ import annotations

from eth_utils import to_checksum_address

from src.heuristics.trade_flow import TradeFlow
from src.ingestion.evm.client import FakeEvmClient, LogEntry, TxInfo, topic_address
from src.ingestion.evm.trades import SWAP_TOPIC, analyse_swaps
from src.models.risk_report import TradeActivity

PAIR = to_checksum_address("0x" + "bb" * 20)
ROUTER = to_checksum_address("0x" + "77" * 20)


def wallet(i: int) -> str:
    return to_checksum_address(f"0x{i:040x}")


class SwapBuilder:
    """Appends v2 Swap logs (+ matching transactions) to a fake client. Token is token0, quote is token1."""

    def __init__(self, client: FakeEvmClient):
        self.client = client
        self.n = 0

    def swap(self, trader: str, *, buy: bool, quote: int, block: int) -> None:
        self.n += 1
        tx_hash = "0x" + f"{self.n:064x}"
        # buy: quote in, token out · sell: token in, quote out
        words = (0, quote, 10**18, 0) if buy else (10**18, 0, 0, quote)
        data = b"".join(w.to_bytes(32, "big") for w in words)
        self.client.logs.append(
            LogEntry(PAIR, (SWAP_TOPIC, topic_address(ROUTER), topic_address(ROUTER)), data, block, tx_hash, self.n)
        )
        self.client.transactions[tx_hash] = TxInfo(tx_hash, trader, ROUTER, quote if buy else 0, block)


def analyse(client: FakeEvmClient, **kw) -> TradeActivity:
    return analyse_swaps(client, PAIR, token_is_token0=True, quote_decimals=18, from_block=0, **kw)


def test_counts_directions_traders_and_volume():
    c = FakeEvmClient(latest_block=1_000)
    b = SwapBuilder(c)
    for i in range(1, 6):
        b.swap(wallet(i), buy=True, quote=10**18, block=100 + i)
    b.swap(wallet(1), buy=False, quote=2 * 10**18, block=200)  # wallet 1 round-trips
    b.swap(wallet(9), buy=False, quote=10**18, block=201)

    t = analyse(c)
    assert (t.swaps, t.buys, t.sells) == (7, 5, 2)
    assert (t.unique_buyers, t.unique_sellers, t.unique_traders) == (5, 2, 6)
    assert t.buy_volume_quote == 5.0 and t.sell_volume_quote == 3.0 and t.total_volume_quote == 8.0
    assert t.round_trip_wallets == 1 and t.round_trip_volume_share == 3 / 8
    assert t.top_trader_share == 3 / 8 and t.sell_buy_ratio == 0.4
    assert t.traders_resolved and t.to_block == 1_000


def test_launch_window_and_tx_lookup_cap():
    c = FakeEvmClient(latest_block=500)
    b = SwapBuilder(c)
    for i in range(1, 13):
        b.swap(wallet(i), buy=True, quote=10**17, block=10 + (i % 3))  # blocks 10..12
    b.swap(wallet(20), buy=True, quote=10**17, block=50)
    t = analyse(c, created_block=10, launch_window_blocks=2)
    assert t.launch_window_buys == 12 and t.buys == 13

    capped = analyse(c, max_tx_lookups=3)
    assert capped.traders_resolved is False and capped.unique_traders == 3 and capped.buys == 13


def test_quote_as_token0_direction():
    c = FakeEvmClient(latest_block=100)
    data = b"".join(w.to_bytes(32, "big") for w in (3 * 10**18, 0, 0, 10**18))  # token1 out → buy when token is token1
    c.logs.append(LogEntry(PAIR, (SWAP_TOPIC, topic_address(ROUTER), topic_address(ROUTER)), data, 5, "0x" + "ab" * 32))
    t = analyse_swaps(c, PAIR, token_is_token0=False, quote_decimals=18, from_block=0)
    assert t.buys == 1 and t.buy_volume_quote == 3.0 and t.unique_traders == 0  # no tx → trader unresolved


def activity(**kw) -> TradeActivity:
    base = {"pair_address": PAIR, "from_block": 0, "to_block": 100, "swaps": 1}
    return TradeActivity(**{**base, **kw})


def test_heuristic_no_sells_and_ratio(config, benign):
    h = TradeFlow(config)
    benign.trades = None
    assert h.evaluate(benign).data_available is False

    benign.trades = activity(swaps=60, buys=60, sells=0, unique_buyers=40, unique_traders=40)
    assert {f.code for f in h.evaluate(benign).flags} == {"NO_SELLS_OBSERVED"}

    benign.trades = activity(swaps=105, buys=100, sells=5, unique_buyers=80, unique_sellers=5, unique_traders=82)
    assert {f.code for f in h.evaluate(benign).flags} == {"SELL_BUY_RATIO_LOW"}

    benign.trades = activity(swaps=100, buys=60, sells=40, unique_traders=50)
    result = h.evaluate(benign)
    assert result.flags == [] and any("60 buys / 40 sells" in n for n in result.notes)


def test_heuristic_wash_concentration_and_snipe(config, benign):
    h = TradeFlow(config)
    benign.trades = activity(
        swaps=200,
        buys=100,
        sells=100,
        unique_traders=12,
        round_trip_wallets=3,
        round_trip_volume_share=0.8,
        top_trader_share=0.6,
    )
    assert {f.code for f in h.evaluate(benign).flags} == {"WASH_TRADING_SUSPECTED", "TRADER_CONCENTRATION"}

    benign.trades = activity(swaps=50, buys=40, sells=10, unique_traders=30, created_block=10, launch_window_buys=15)
    assert {f.code for f in h.evaluate(benign).flags} == {"LAUNCH_SNIPED"}

    # unresolved traders → wash/concentration checks are skipped, with a note
    benign.trades = activity(
        swaps=200,
        buys=100,
        sells=100,
        unique_traders=12,
        round_trip_volume_share=0.9,
        top_trader_share=0.9,
        traders_resolved=False,
    )
    result = h.evaluate(benign)
    assert result.flags == [] and any("unresolved" in n for n in result.notes)


def test_provider_wires_swap_analysis():
    from tests.test_rpc_provider import PAIR as PROVIDER_PAIR
    from tests.test_rpc_provider import TOKEN, Chain, build_chain, make_provider

    chain = build_chain()
    data = b"".join(w.to_bytes(32, "big") for w in (0, 10**18, 10**18, 0))
    for i in range(1, 4):
        tx = "0x" + f"{i:064x}"
        chain.logs.append(
            LogEntry(PROVIDER_PAIR, (SWAP_TOPIC, topic_address(ROUTER), topic_address(ROUTER)), data, 10 + i, tx, i)
        )
        chain.transactions[tx] = TxInfo(tx, wallet(i), ROUTER, 10**18, 10 + i)
    snap = make_provider(chain).fetch_sync(Chain.ETHEREUM, TOKEN)
    assert snap.trades is not None and snap.trades.buys == 3 and snap.trades.unique_buyers == 3
    assert snap.trades.created_block == 10 and snap.trades.from_block == 10
