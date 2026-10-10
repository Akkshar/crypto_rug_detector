"""Trade-flow analytics from Uniswap-v2-style ``Swap`` logs.

``Swap(address indexed sender, uint amount0In, uint amount1In, uint amount0Out,
uint amount1Out, address indexed to)`` tells us direction and size but not who
traded: ``sender`` is the router and ``to`` is the router again when the
output is unwrapped to native. The trader is therefore resolved from the
transaction's ``from`` field, capped so a busy pair can't trigger thousands of
``eth_getTransactionByHash`` calls.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from eth_utils import keccak, to_checksum_address

from src.ingestion.evm.client import EvmClient, chunked_ranges
from src.models.risk_report import TradeActivity

log = logging.getLogger("rugscan.trades")

SWAP_TOPIC = keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)")


@dataclass(frozen=True)
class SwapRecord:
    block: int
    tx_hash: str
    trader: str | None
    is_buy: bool
    quote_amount: int  # raw quote units moved


def _word(data: bytes, index: int) -> int:
    return int.from_bytes(data[32 * index : 32 * (index + 1)], "big")


def analyse_swaps(
    client: EvmClient,
    pair: str,
    *,
    token_is_token0: bool,
    quote_decimals: int,
    from_block: int,
    to_block: int | None = None,
    created_block: int | None = None,
    launch_window_blocks: int = 3,
    max_swaps: int = 2_000,
    max_tx_lookups: int = 400,
    log_chunk: int = 50_000,
) -> TradeActivity:
    pair = to_checksum_address(pair)
    hi = to_block if to_block is not None else client.block_number()
    swaps: list[SwapRecord] = []
    tx_cache: dict[str, str | None] = {}
    lookups = 0
    truncated = False

    for lo, chunk_hi in chunked_ranges(from_block, hi, log_chunk):
        for entry in client.get_logs(pair, [SWAP_TOPIC], lo, chunk_hi):
            if len(entry.data) < 128:
                continue
            a0_in, a1_in, a0_out, a1_out = (_word(entry.data, i) for i in range(4))
            # token0/token1 are fixed per pair; the quote asset is whichever side the token is not.
            token_out = a0_out if token_is_token0 else a1_out
            quote_in = a1_in if token_is_token0 else a0_in
            quote_out = a1_out if token_is_token0 else a0_out
            is_buy = token_out > 0
            quote_amount = quote_in if is_buy else quote_out

            trader: str | None
            if entry.tx_hash in tx_cache:
                trader = tx_cache[entry.tx_hash]
            elif lookups < max_tx_lookups:
                lookups += 1
                tx = client.get_transaction(entry.tx_hash)
                trader = tx.sender if tx else None
                tx_cache[entry.tx_hash] = trader
            else:
                trader = None
                truncated = True
            swaps.append(SwapRecord(entry.block_number, entry.tx_hash, trader, is_buy, quote_amount))
            if len(swaps) >= max_swaps:
                truncated = True
                break
        if len(swaps) >= max_swaps:
            break

    scale = 10**quote_decimals
    buys = [s for s in swaps if s.is_buy]
    sells = [s for s in swaps if not s.is_buy]
    volume_by: dict[str, float] = defaultdict(float)
    bought: set[str] = set()
    sold: set[str] = set()
    for s in swaps:
        if s.trader is None:
            continue
        volume_by[s.trader] += s.quote_amount / scale
        (bought if s.is_buy else sold).add(s.trader)
    total_volume = sum(volume_by.values())
    round_trip = bought & sold
    round_trip_volume = sum(volume_by[t] for t in round_trip)
    top_share = max(volume_by.values()) / total_volume if total_volume > 0 else 0.0

    launch_end = (created_block + launch_window_blocks) if created_block is not None else None
    launch_buys = sum(1 for s in buys if launch_end is not None and s.block <= launch_end)

    if truncated:
        log.info("swap analysis for %s truncated (%d swaps, %d tx lookups)", pair, len(swaps), lookups)

    return TradeActivity(
        pair_address=pair,
        from_block=from_block,
        to_block=hi,
        created_block=created_block,
        swaps=len(swaps),
        buys=len(buys),
        sells=len(sells),
        unique_buyers=len(bought),
        unique_sellers=len(sold),
        unique_traders=len(bought | sold),
        buy_volume_quote=sum(s.quote_amount for s in buys) / scale,
        sell_volume_quote=sum(s.quote_amount for s in sells) / scale,
        round_trip_wallets=len(round_trip),
        round_trip_volume_share=min(1.0, round_trip_volume / total_volume) if total_volume > 0 else 0.0,
        top_trader_share=min(1.0, top_share),
        launch_window_buys=launch_buys,
        launch_window_blocks=launch_window_blocks,
        traders_resolved=not truncated,
    )
