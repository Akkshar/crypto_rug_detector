"""Behavioural heuristics over observed swaps.

Static analysis says what a contract *could* do; trade flow shows what is
*happening*. A pool with hundreds of buys and zero sells is a honeypot in
practice whatever the simulator thinks; volume dominated by a handful of
wallets round-tripping is a painted chart; a launch block full of buys is a
bot-run or insider launch.
"""

from __future__ import annotations

from src.heuristics.base import BaseHeuristic, ge, lt
from src.models.risk_report import HeuristicResult, RiskCategory, RiskFlag, Severity, TokenSnapshot


class TradeFlow(BaseHeuristic):
    name = "trade_flow"
    category = RiskCategory.TRADING

    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        t = snapshot.trades
        if t is None:
            return self._result([], data_available=False, notes=["No swap history available"])
        if t.swaps == 0:
            return self._result([], notes=["No swaps observed in the analysed window"])

        flags: list[RiskFlag] = []
        notes: list[str] = []
        th = self.t

        if t.buys >= th.min_buys_for_sell_check and t.sells == 0:
            flags.append(
                self._flag(
                    "NO_SELLS_OBSERVED",
                    Severity.HIGH,
                    f"{t.buys} buys and not a single sell",
                    f"{t.buys} buys from {t.unique_buyers} wallet(s) over blocks {t.from_block}–{t.to_block} with zero sells – "
                    "nobody has been able (or allowed) to exit",
                    buys=t.buys,
                    unique_buyers=t.unique_buyers,
                )
            )
        elif (
            t.buys >= th.min_buys_for_ratio_check
            and t.sell_buy_ratio is not None
            and lt(t.sell_buy_ratio, th.sell_buy_ratio_min)
        ):
            flags.append(
                self._flag(
                    "SELL_BUY_RATIO_LOW",
                    Severity.MEDIUM,
                    f"Only {t.sells} sells against {t.buys} buys",
                    f"Sell/buy ratio {t.sell_buy_ratio:.2f} (< {th.sell_buy_ratio_min:g}) – exits are rare or selectively blocked",
                    buys=t.buys,
                    sells=t.sells,
                    ratio=round(t.sell_buy_ratio, 3),
                )
            )
        else:
            notes.append(f"{t.buys} buys / {t.sells} sells from {t.unique_traders} wallet(s)")

        if (
            t.traders_resolved
            and t.unique_traders >= th.min_traders_for_wash_check
            and ge(t.round_trip_volume_share, th.wash_volume_share)
            and t.round_trip_wallets <= th.wash_max_wallets
        ):
            flags.append(
                self._flag(
                    "WASH_TRADING_SUSPECTED",
                    Severity.MEDIUM,
                    f"{t.round_trip_volume_share:.0%} of volume from {t.round_trip_wallets} round-tripping wallet(s)",
                    f"A handful of wallets both buying and selling generate {t.round_trip_volume_share:.0%} of volume – "
                    "activity is manufactured, not organic demand",
                    round_trip_wallets=t.round_trip_wallets,
                    round_trip_volume_share=round(t.round_trip_volume_share, 4),
                )
            )

        if (
            t.traders_resolved
            and t.unique_traders >= th.min_traders_for_wash_check
            and ge(t.top_trader_share, th.top_trader_share)
        ):
            flags.append(
                self._flag(
                    "TRADER_CONCENTRATION",
                    Severity.MEDIUM,
                    f"One wallet drives {t.top_trader_share:.0%} of volume",
                    f"The most active wallet accounts for {t.top_trader_share:.0%} of swap volume",
                    top_trader_share=round(t.top_trader_share, 4),
                )
            )

        if t.created_block is not None and t.launch_window_buys >= th.launch_snipe_buys:
            flags.append(
                self._flag(
                    "LAUNCH_SNIPED",
                    Severity.MEDIUM,
                    f"{t.launch_window_buys} buys within {t.launch_window_blocks} block(s) of launch",
                    "Bots or pre-positioned insiders bought the launch block – retail entered after the price was set",
                    launch_window_buys=t.launch_window_buys,
                    launch_window_blocks=t.launch_window_blocks,
                )
            )

        if not t.traders_resolved:
            notes.append("Trader identities partially unresolved (lookup cap) – wash/concentration checks skipped")
        return self._result(flags, notes=notes)
