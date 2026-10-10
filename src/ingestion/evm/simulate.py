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
        if dex is None or dex.kind is not DexKind.V2:
            log.info("sell simulation skipped: %s is not a v2-style DEX", pool.dex)
            return None
        if self.cfg.quote_tokens.get(pool.quote_token) != self.cfg.wrapped_native:
            log.info("sell simulation skipped: %s pool is not quoted in native", pool.quote_token)
            return None

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
            return self._failed(f"buy reverted: {reason}")
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
    def _dex_for(self, pool: LiquidityPool):
        name = pool.dex
        if name not in self.cfg.dexes and "_" in name and name.rsplit("_", 1)[1].isdigit():
            name = name.rsplit("_", 1)[0]
        return self.cfg.dexes.get(name)

    def _failed(self, reason: str, *, buy_gas: int | None = None) -> SellSimulation:
        return SellSimulation(attempted=self.attempts, succeeded=0, buy_gas=buy_gas, revert_reason=reason)

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
        for slot in range(self.max_slot):
            state = {token: {"stateDiff": {balance_slot_key(PROBE_SENDER, slot): word(planted)}}}
            got = self._try_int(call, state)
            if got == planted:
                return slot
        return None

    def _find_allowance_key(self, token: str, spender: str, planted: int) -> str | None:
        call = abi.Call(token, "allowance(address,address)", (PROBE_SENDER, spender), returns=("uint256",))
        for slot in range(self.max_slot):
            key = allowance_slot_key(PROBE_SENDER, spender, slot)
            if self._try_int(call, {token: {"stateDiff": {key: word(planted)}}}) == planted:
                return key
        return None

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
