"""Buy → sell round-trip simulation with ``eth_call`` state overrides.

No fork, no private key, no gas spent. A throwaway address is given a native
balance through the ``balance`` override, then:

1. **Buy** – ``swapExactETHForTokensSupportingFeeOnTransferTokens`` with a
   bisected ``amountOutMin``: the router reverts whenever the real output is
   below the minimum, so bisecting between 0 and the pre-tax quote recovers the
   tokens actually received → effective buy tax.
2. **Plant a balance** – locate the token's ``balances`` mapping slot by
   writing a sentinel through ``stateDiff`` until ``balanceOf`` echoes it, do
   the same for the ``allowance`` mapping.
3. **Sell** – ``swapExactTokensForETHSupportingFeeOnTransferTokens`` from the
   planted balance, bisected the same way → effective sell tax. Repeated for
   the full balance, half and a quarter to catch size-gated traps.

Any node that honours state overrides works (geth, erigon, reth, Alchemy,
Infura, QuickNode…). v2-style routers only; v3 pools are skipped.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from config.chains import ChainConfig, DexKind
from src.ingestion.evm import abi
from src.ingestion.evm.client import CallReverted, EvmClient, StateOverride
from src.ingestion.evm.pools import PoolDiscovery
from src.models.risk_report import LiquidityPool, SellSimulation

log = logging.getLogger("rugscan.simulate")

PROBE_SENDER = "0x1111111111111111111111111111111111111111"
ONE_ETHER = 10**18
DEADLINE = 2**64
BUY_SIG = "swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)"
SELL_SIG = "swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)"


def word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def pool_fee_from_label(label: str) -> int | None:
    """``uniswap_v3_3000`` → 3000; v2 labels have no fee suffix."""
    tail = label.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def balance_slot_key(holder: str, slot: int) -> str:
    return "0x" + keccak(encode(["address", "uint256"], [holder, slot])).hex()


def allowance_slot_key(owner: str, spender: str, slot: int) -> str:
    inner = keccak(encode(["address", "uint256"], [owner, slot]))
    return "0x" + keccak(encode(["address", "bytes32"], [spender, inner])).hex()


class SellSimulator:
    def __init__(
        self,
        client: EvmClient,
        cfg: ChainConfig,
        discovery: PoolDiscovery,
        *,
        attempts: int = 3,
        buy_amount_native: int = ONE_ETHER // 100,
        max_slot: int = 30,
    ):
        self.client = client
        self.cfg = cfg
        self.discovery = discovery
        self.attempts = attempts
        self.buy_amount = buy_amount_native
        self.max_slot = max_slot

    # ------------------------------------------------------------------ #
    def simulate(self, token: str, pool: LiquidityPool) -> SellSimulation | None:
        token = to_checksum_address(token)
        dex = self._dex_for(pool)
        if dex is None:
            log.info("sell simulation skipped: %s is not a configured DEX", pool.dex)
            return None
        if self.cfg.quote_tokens.get(pool.quote_token) != self.cfg.wrapped_native:
            log.info("sell simulation skipped: %s pool is not quoted in native", pool.quote_token)
            return None
        if (pool.token_reserve is not None and pool.token_reserve <= 0) or (
            pool.quote_reserve is not None and pool.quote_reserve <= 0
        ):
            # A freshly created pair before liquidity is added: nothing to trade against, nothing to conclude.
            return self._failed("pool has no liquidity yet – nothing to simulate")
        if dex.kind is DexKind.V3:
            return self._simulate_v3(token, pool, dex)

        router, wn = dex.router, self.cfg.wrapped_native
        funded: StateOverride = {PROBE_SENDER: {"balance": hex(ONE_ETHER)}}

        # -- buy ----------------------------------------------------------- #
        buy_quote = self._amounts_out(router, self.buy_amount, [wn, token])
        if not buy_quote:
            return self._failed("router could not quote a buy (no route / empty reserves)")

        def buy_call(min_out: int) -> abi.Call:
            return abi.Call(router, BUY_SIG, (min_out, [wn, token], PROBE_SENDER, DEADLINE), value=self.buy_amount)

        received, reason = self._max_output(buy_call, buy_quote, funded)
        if received is None:
            return self._failed(f"buy reverted: {reason}", attempted=True)
        buy_gas = self._gas(buy_call(0), funded)
        effective_buy_tax = self._tax_pct(received, buy_quote)

        # -- plant balance + allowance ------------------------------------- #
        slot = self._find_balance_slot(token, received)
        if slot is None:
            return self._failed("could not locate balances storage slot (non-standard layout)", buy_gas=buy_gas)
        allowance_key = self._find_allowance_key(token, router, received)
        if allowance_key is None:
            return self._failed("could not locate allowance storage slot (non-standard layout)", buy_gas=buy_gas)

        # -- sell ---------------------------------------------------------- #
        succeeded, sell_gas, worst_tax, last_reason = 0, None, None, None
        for attempt in range(self.attempts):
            amount = received >> attempt  # full, half, quarter …
            if amount == 0:
                break
            sell_quote = self._amounts_out(router, amount, [token, wn])
            if not sell_quote:
                last_reason = "router could not quote the sell"
                continue
            state: StateOverride = {
                PROBE_SENDER: {"balance": hex(ONE_ETHER)},
                token: {"stateDiff": {balance_slot_key(PROBE_SENDER, slot): word(amount), allowance_key: word(amount)}},
            }

            def sell_call(min_out: int, amt: int = amount) -> abi.Call:
                return abi.Call(router, SELL_SIG, (amt, min_out, [token, wn], PROBE_SENDER, DEADLINE))

            out, reason = self._max_output(sell_call, sell_quote, state)
            if out is None:
                last_reason = reason
                continue
            succeeded += 1
            tax = self._tax_pct(out, sell_quote)
            worst_tax = tax if worst_tax is None else max(worst_tax, tax)
            if sell_gas is None:
                sell_gas = self._gas(sell_call(0), state)

        return SellSimulation(
            attempted=self.attempts,
            succeeded=succeeded,
            buy_gas=buy_gas,
            sell_gas=sell_gas,
            effective_buy_tax_pct=effective_buy_tax,
            effective_sell_tax_pct=worst_tax,
            revert_reason=last_reason if succeeded < self.attempts else None,
        )

    # ------------------------------------------------------------------ #
    # Uniswap v3 (exactInputSingle on SwapRouter / SwapRouter02)
    # ------------------------------------------------------------------ #
    def _simulate_v3(self, token: str, pool: LiquidityPool, dex: Any) -> SellSimulation | None:
        router, wn = dex.router, self.cfg.wrapped_native
        fee = pool_fee_from_label(pool.dex)
        if fee is None:
            return None
        quote = self._v3_expected_output(pool.pair_address, token, wn)
        if quote is None:
            return self._failed("could not read pool price (slot0)")
        price_token_per_wei, _ = quote  # raw tokens per 1 wei of native at spot, before the pool fee
        fee_factor = 1 - fee / 1_000_000  # the pool fee is taken from the input of *each* leg
        funded: StateOverride = {PROBE_SENDER: {"balance": hex(ONE_ETHER)}}

        def exact_input(token_in: str, token_out: str, amount_in: int, min_out: int, value: int = 0) -> abi.Call:
            params: tuple[Any, ...]
            if dex.router_kind == "swap_router02":
                sig = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"
                params = (token_in, token_out, fee, PROBE_SENDER, amount_in, min_out, 0)
            else:  # SwapRouter (v1) carries a deadline
                sig = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
                params = (token_in, token_out, fee, PROBE_SENDER, DEADLINE, amount_in, min_out, 0)
            return abi.Call(router, sig, (params,), value=value)

        # -- buy: native → token (router wraps msg.value) ------------------ #
        buy_quote = int(self.buy_amount * price_token_per_wei * fee_factor)
        if buy_quote <= 0:
            return self._failed("pool price implies zero output for the probe buy")
        received, reason = self._max_output(
            lambda m: exact_input(wn, token, self.buy_amount, m, self.buy_amount), buy_quote, funded
        )
        if received is None:
            return self._failed(f"buy reverted: {reason}", attempted=True)
        buy_gas = self._gas(exact_input(wn, token, self.buy_amount, 0, self.buy_amount), funded)
        effective_buy_tax = self._tax_pct(received, buy_quote)

        slot = self._find_balance_slot(token, received)
        if slot is None:
            return self._failed("could not locate balances storage slot (non-standard layout)", buy_gas=buy_gas)
        allowance_key = self._find_allowance_key(token, router, received)
        if allowance_key is None:
            return self._failed("could not locate allowance storage slot (non-standard layout)", buy_gas=buy_gas)

        # -- sell: token → native (WETH stays wrapped; fine for a simulation) -- #
        succeeded, sell_gas, worst_tax, last_reason = 0, None, None, None
        for attempt in range(self.attempts):
            amount = received >> attempt
            if amount == 0:
                break
            sell_quote = int(amount / price_token_per_wei * fee_factor) if price_token_per_wei else 0
            if sell_quote <= 0:
                last_reason = "pool price implies zero output for the sell"
                continue
            state: StateOverride = {
                PROBE_SENDER: {"balance": hex(ONE_ETHER)},
                token: {"stateDiff": {balance_slot_key(PROBE_SENDER, slot): word(amount), allowance_key: word(amount)}},
            }

            def sell_call(min_out: int, amt: int = amount) -> abi.Call:
                return exact_input(token, wn, amt, min_out)

            out, reason = self._max_output(sell_call, sell_quote, state)
            if out is None:
                last_reason = reason
                continue
            succeeded += 1
            tax = self._tax_pct(out, sell_quote)
            worst_tax = tax if worst_tax is None else max(worst_tax, tax)
            if sell_gas is None:
                sell_gas = self._gas(exact_input(token, wn, amount, 0), state)

        return SellSimulation(
            attempted=self.attempts,
            succeeded=succeeded,
            buy_gas=buy_gas,
            sell_gas=sell_gas,
            effective_buy_tax_pct=effective_buy_tax,
            effective_sell_tax_pct=worst_tax,
            revert_reason=last_reason if succeeded < self.attempts else None,
        )

    def _v3_expected_output(self, pool: str, token: str, wn: str) -> tuple[float, bool] | None:
        """Fee-adjusted spot price from slot0: (raw token units per wei of native, token_is_token0).

        Spot price ignores price impact, so measured "tax" on a thin pool includes slippage; the probe
        trade is deliberately tiny (0.01 native) to keep that negligible, and reverts are exact regardless.
        """
        raw0 = self.client.try_call(abi.v2_token0(pool))
        raw_slot = self.client.try_call(abi.v3_slot0(pool))
        if raw0 is None or raw_slot is None:
            return None
        token0 = abi.v2_token0(pool).one(raw0)
        sqrt_price = abi.v3_slot0(pool).parse(raw_slot)[0]
        if sqrt_price == 0:
            return None
        price01 = (sqrt_price / 2**96) ** 2  # raw token1 per raw token0
        token_is_token0 = token0 == token
        tokens_per_wei = (1 / price01) if token_is_token0 else price01
        return tokens_per_wei, token_is_token0

    # ------------------------------------------------------------------ #
    def _dex_for(self, pool: LiquidityPool):
        name = pool.dex
        if name not in self.cfg.dexes and "_" in name and name.rsplit("_", 1)[1].isdigit():
            name = name.rsplit("_", 1)[0]
        return self.cfg.dexes.get(name)

    def _failed(self, reason: str, *, buy_gas: int | None = None, attempted: bool = False) -> SellSimulation:
        """``attempted=False`` means the swap could not even be tried (empty pool, no route, unknown storage
        layout) → attempted=0, which the heuristics read as "not simulated". Only a real on-chain revert of the
        swap itself is an *attempted and failed* sell – the honeypot signal."""
        return SellSimulation(
            attempted=self.attempts if attempted else 0, succeeded=0, buy_gas=buy_gas, revert_reason=reason
        )

    @staticmethod
    def _tax_pct(actual: int, quote: int) -> float:
        if quote <= 0:
            return 0.0
        return round(min(100.0, max(0.0, (1 - actual / quote) * 100.0)), 2)

    def _amounts_out(self, router: str, amount_in: int, path: list[str]) -> int | None:
        call = abi.v2_router_get_amounts_out(router, amount_in, path)
        raw = self.client.try_call(call)
        if raw is None:
            return None
        amounts = call.one(raw)
        return int(amounts[-1]) if amounts else None

    def _gas(self, call: abi.Call, state: StateOverride) -> int | None:
        try:
            return self.client.estimate_gas(
                call.to, call.data, sender=PROBE_SENDER, value=call.value, state_override=state
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("estimate_gas failed: %s", exc)
            return None

    def _max_output(
        self, make_call: Callable[[int], abi.Call], quote: int, state: StateOverride, *, tolerance_bps: int = 10
    ) -> tuple[int | None, str | None]:
        """Largest ``amountOutMin`` the swap still succeeds with ≈ the real output."""
        try:
            self.client.call(make_call(0), state_override=state, sender=PROBE_SENDER)
        except CallReverted as exc:
            return None, exc.reason
        lo, hi = 0, quote
        tolerance = max(1, quote * tolerance_bps // 10_000)
        while hi - lo > tolerance:
            mid = (lo + hi) // 2
            try:
                self.client.call(make_call(mid), state_override=state, sender=PROBE_SENDER)
                lo = mid
            except CallReverted:
                hi = mid
        return lo, None

    def _find_balance_slot(self, token: str, planted: int) -> int | None:
        call = abi.erc20_balance_of(token, PROBE_SENDER)
        return self._bisect_slot(token, call, planted, lambda s: balance_slot_key(PROBE_SENDER, s))

    def _find_allowance_key(self, token: str, spender: str, planted: int) -> str | None:
        call = abi.Call(token, "allowance(address,address)", (PROBE_SENDER, spender), returns=("uint256",))
        slot = self._bisect_slot(token, call, planted, lambda s: allowance_slot_key(PROBE_SENDER, spender, s))
        return allowance_slot_key(PROBE_SENDER, spender, slot) if slot is not None else None

    def _bisect_slot(self, token: str, call: abi.Call, planted: int, key_for: Callable[[int], str]) -> int | None:
        """Locate the mapping slot in O(log n) calls: plant the sentinel in *every* candidate slot at once
        (keys are keccak-derived, so they never collide), then halve the planted set while the getter still
        echoes the sentinel. ~6 calls for 30 candidates instead of 30."""
        candidates = list(range(self.max_slot))

        def echoes(slots: list[int]) -> bool:
            state = {token: {"stateDiff": {key_for(s): word(planted) for s in slots}}}
            return self._try_int(call, state) == planted

        if not candidates or not echoes(candidates):
            return None
        while len(candidates) > 1:
            half = candidates[: len(candidates) // 2]
            candidates = half if echoes(half) else candidates[len(half) :]
        return candidates[0]

    def _try_int(self, call: abi.Call, state: StateOverride) -> int | None:
        try:
            return int(call.one(self.client.call(call, state_override=state)))
        except (CallReverted, ValueError):
            return None


def planted_balance(state: StateOverride | None, token: str, holder: str, slot: int) -> int | None:
    """Test helper: read a planted balance back out of a stateDiff override."""
    if not state:
        return None
    diff: dict[str, Any] = dict(state.get(to_checksum_address(token), {}).get("stateDiff", {}))
    raw = diff.get(balance_slot_key(holder, slot))
    return int(raw, 16) if raw else None
