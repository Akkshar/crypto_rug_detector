"""DEX pool discovery, native-asset pricing and LP-holder resolution.

For every DEX in the chain registry we ask the factory for a pool between the
token and each quote asset (wrapped native + stables). Uniswap-v2-style pools
expose reserves and are themselves the LP token, so LP holders can be rebuilt
by replaying the pair's ``Transfer`` logs. v3 pools report liquidity via token
balances; their positions are NFTs, so LP lock status is left unknown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from eth_utils import keccak, to_checksum_address

from config.chains import ChainConfig, ChainRegistry, DexKind
from src.ingestion.evm import abi
from src.ingestion.evm.client import EvmClient, address_from_topic, chunked_ranges
from src.models.risk_report import ZERO_ADDRESS, LiquidityPool, LockerKind, LPHolding

log = logging.getLogger("rugscan.pools")

TRANSFER_TOPIC = keccak(text="Transfer(address,address,uint256)")
Q96 = 2**96


@dataclass(frozen=True)
class NativePrice:
    usd: float
    source: str


class PoolDiscovery:
    def __init__(
        self,
        client: EvmClient,
        chain_cfg: ChainConfig,
        registry: ChainRegistry,
        *,
        log_chunk: int = 50_000,
        max_log_chunks: int = 40,
    ):
        self.client = client
        self.cfg = chain_cfg
        self.registry = registry
        self.log_chunk = log_chunk
        self.max_log_chunks = max_log_chunks
        self._decimals: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # pricing
    # ------------------------------------------------------------------ #
    def decimals(self, token: str) -> int:
        token = to_checksum_address(token)
        if token not in self._decimals:
            raw = self.client.try_call(abi.erc20_decimals(token))
            self._decimals[token] = int(abi.erc20_decimals(token).one(raw)) if raw else 18
        return self._decimals[token]

    def native_price(self) -> NativePrice | None:
        src = self.cfg.native_price_source
        if src is None:
            return None
        stable = self.cfg.quote_tokens[src.stable]
        wn = self.cfg.wrapped_native
        try:
            if src.kind == "v2_pair":
                token0 = abi.v2_token0(src.address).one(self.client.call(abi.v2_token0(src.address)))
                r0, r1, _ = abi.v2_get_reserves(src.address).parse(self.client.call(abi.v2_get_reserves(src.address)))
                native_res, stable_res = (r0, r1) if token0 == wn else (r1, r0)
                if native_res == 0:
                    return None
                usd = (stable_res / 10 ** self.decimals(stable)) / (native_res / 10 ** self.decimals(wn))
            else:  # v3_pool
                token0 = abi.v2_token0(src.address).one(self.client.call(abi.v2_token0(src.address)))
                sqrt_price = abi.v3_slot0(src.address).parse(self.client.call(abi.v3_slot0(src.address)))[0]
                price01 = (sqrt_price / Q96) ** 2  # token1 per token0 (raw units)
                if token0 == wn:
                    usd = price01 * 10 ** (self.decimals(wn) - self.decimals(stable))
                else:
                    usd = (1 / price01) * 10 ** (self.decimals(wn) - self.decimals(stable)) if price01 else 0
            return NativePrice(usd=usd, source=f"{src.kind}:{src.address}")
        except Exception as exc:  # noqa: BLE001
            log.warning("native price lookup failed: %s", exc)
            return None

    def quote_usd(self, quote_symbol: str, native: NativePrice | None) -> float | None:
        if self.cfg.is_stable(quote_symbol):
            return 1.0
        if quote_symbol == self.cfg.native_symbol or self.cfg.quote_tokens.get(quote_symbol) == self.cfg.wrapped_native:
            return native.usd if native else None
        return None

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #
    def discover(
        self, token: str, *, deployer: str | None, owner: str | None, now: datetime | None = None
    ) -> list[LiquidityPool]:
        token = to_checksum_address(token)
        native = self.native_price()
        pools: list[LiquidityPool] = []
        for dex_name, dex in self.cfg.dexes.items():
            for quote_symbol, quote_addr in self.cfg.quote_tokens.items():
                if quote_addr == token:
                    continue
                try:
                    found = (
                        self._discover_v2(
                            dex_name, dex.factory, token, quote_symbol, quote_addr, native, deployer, owner, now
                        )
                        if dex.kind is DexKind.V2
                        else self._discover_v3(
                            dex_name, dex.factory, dex.fee_tiers, token, quote_symbol, quote_addr, native
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s/%s discovery failed: %s", dex_name, quote_symbol, exc)
                    continue
                pools.extend(found)
        return sorted(pools, key=lambda p: -p.liquidity_usd)

    def _discover_v2(
        self,
        dex: str,
        factory: str,
        token: str,
        qsym: str,
        quote: str,
        native: NativePrice | None,
        deployer: str | None,
        owner: str | None,
        now: datetime | None,
    ) -> list[LiquidityPool]:
        pair = abi.v2_get_pair(factory, token, quote).one(self.client.call(abi.v2_get_pair(factory, token, quote)))
        if pair == ZERO_ADDRESS:
            return []
        token0 = abi.v2_token0(pair).one(self.client.call(abi.v2_token0(pair)))
        r0, r1, _ = abi.v2_get_reserves(pair).parse(self.client.call(abi.v2_get_reserves(pair)))
        token_res, quote_res = (r0, r1) if token0 == token else (r1, r0)
        quote_units = quote_res / 10 ** self.decimals(quote)
        quote_usd = self.quote_usd(qsym, native)
        liquidity_usd = 2 * quote_units * quote_usd if quote_usd is not None else 0.0
        holdings, created = self.lp_holdings_v2(pair, deployer=deployer, owner=owner, now=now)
        return [
            LiquidityPool(
                dex=dex,
                pair_address=pair,
                quote_token=qsym,
                liquidity_usd=liquidity_usd,
                token_reserve=token_res / 10 ** self.decimals(token),
                quote_reserve=quote_units,
                created_at=created,
                lp_holdings=holdings,
            )
        ]

    def _discover_v3(
        self, dex: str, factory: str, fees: list[int], token: str, qsym: str, quote: str, native: NativePrice | None
    ) -> list[LiquidityPool]:
        out: list[LiquidityPool] = []
        quote_usd = self.quote_usd(qsym, native)
        for fee in fees:
            pool = abi.v3_get_pool(factory, token, quote, fee).one(
                self.client.call(abi.v3_get_pool(factory, token, quote, fee))
            )
            if pool == ZERO_ADDRESS:
                continue
            quote_bal = abi.erc20_balance_of(quote, pool).one(self.client.call(abi.erc20_balance_of(quote, pool)))
            token_bal = abi.erc20_balance_of(token, pool).one(self.client.call(abi.erc20_balance_of(token, pool)))
            quote_units = quote_bal / 10 ** self.decimals(quote)
            out.append(
                LiquidityPool(
                    dex=f"{dex}_{fee}",
                    pair_address=pool,
                    quote_token=qsym,
                    liquidity_usd=2 * quote_units * quote_usd if quote_usd is not None else 0.0,
                    token_reserve=token_bal / 10 ** self.decimals(token),
                    quote_reserve=quote_units,
                    lp_holdings=[],  # v3 positions are NFTs – lock status not resolved here
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # LP holders (v2 pair == LP token)
    # ------------------------------------------------------------------ #
    def lp_holdings_v2(
        self, pair: str, *, deployer: str | None, owner: str | None, now: datetime | None = None
    ) -> tuple[list[LPHolding], datetime | None]:
        balances, _first_block = self.replay_balances(pair)
        if not balances:
            return [], None
        total = sum(v for v in balances.values() if v > 0)
        if total <= 0:
            return [], None
        insiders = {to_checksum_address(a) for a in (deployer, owner) if a}
        holdings: list[LPHolding] = []
        for holder, bal in sorted(balances.items(), key=lambda kv: -kv[1]):
            if bal <= 0:
                continue
            locker = LockerKind.BURNED if self.registry.is_burn(holder) else self.cfg.locker_for(holder)
            if locker is None and self.client.get_code(holder) and holder not in insiders:
                locker = LockerKind.UNKNOWN_CONTRACT
            holdings.append(
                LPHolding(
                    holder=holder,
                    share=min(1.0, bal / total),
                    locker=locker,
                    is_deployer_or_owner=holder in insiders,
                )
            )
        # Cap share-sum rounding at 1.0 for the schema validator.
        excess = sum(h.share for h in holdings) - 1.0
        if excess > 0 and holdings:
            holdings[0] = holdings[0].model_copy(update={"share": max(0.0, holdings[0].share - excess)})
        return holdings[:25], None

    def replay_balances(self, token: str, *, from_block: int = 0) -> tuple[dict[str, int], int | None]:
        """Rebuild ERC-20 balances from ``Transfer`` logs. Returns (balances, first_block)."""
        token = to_checksum_address(token)
        latest = self.client.block_number()
        balances: dict[str, int] = {}
        first_block: int | None = None
        chunks = 0
        for lo, hi in chunked_ranges(from_block, latest, self.log_chunk):
            chunks += 1
            if chunks > self.max_log_chunks:
                log.warning("log replay for %s truncated after %d chunks", token, self.max_log_chunks)
                break
            for entry in self.client.get_logs(token, [TRANSFER_TOPIC], lo, hi):
                if len(entry.topics) < 3:
                    continue
                sender = address_from_topic(entry.topics[1])
                receiver = address_from_topic(entry.topics[2])
                amount = int.from_bytes(entry.data[:32], "big") if entry.data else 0
                if first_block is None:
                    first_block = entry.block_number
                if sender != ZERO_ADDRESS:
                    balances[sender] = balances.get(sender, 0) - amount
                balances[receiver] = balances.get(receiver, 0) + amount
        return balances, first_block


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
