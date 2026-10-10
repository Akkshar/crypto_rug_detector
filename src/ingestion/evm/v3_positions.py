"""Uniswap-v3 LP ownership via NonfungiblePositionManager (NPM) NFTs.

A v3 pool has no LP token; liquidity lives in NPM positions (ERC-721) keyed
by tokenId. To learn who controls a pool's liquidity:

1. replay NPM ``Transfer(0x0 → owner, tokenId)`` mint logs since the pool was
   created (capped),
2. batch ``positions(tokenId)`` and keep the ones matching the pool's
   (token0, token1, fee) with liquidity > 0,
3. batch ``ownerOf(tokenId)`` and aggregate liquidity per current owner.

Owners are classified exactly like v2 LP holders: known locker contracts
(UNCX v3 lockers, Team Finance…), burn, deployer/owner, unknown contracts.
"""

from __future__ import annotations

import logging

from eth_utils import keccak, to_checksum_address

from config.chains import ChainConfig, ChainRegistry
from src.ingestion.evm import abi
from src.ingestion.evm.client import EvmClient, address_from_topic, chunked_ranges, topic_address
from src.models.risk_report import ZERO_ADDRESS, LockerKind, LPHolding

log = logging.getLogger("rugscan.v3")

TRANSFER_TOPIC = keccak(text="Transfer(address,address,uint256)")
V3_MINT_TOPIC = keccak(text="Mint(address,address,int24,int24,uint128,uint256,uint256)")
POSITIONS_RETURNS = (
    "uint96",
    "address",
    "address",
    "address",
    "uint24",
    "int24",
    "int24",
    "uint128",
    "uint256",
    "uint256",
    "uint128",
    "uint128",
)


def positions_call(npm: str, token_id: int) -> abi.Call:
    return abi.Call(npm, "positions(uint256)", (token_id,), returns=POSITIONS_RETURNS)


def owner_of_call(npm: str, token_id: int) -> abi.Call:
    return abi.Call(npm, "ownerOf(uint256)", (token_id,), returns=("address",))


def pool_created_block(client: EvmClient, pool: str, *, log_chunk: int = 50_000, max_chunks: int = 40) -> int | None:
    """Block of the pool's first ``Mint`` – the closest thing a v3 pool has to a creation event we can cheaply find."""
    latest = client.block_number()
    for i, (lo, hi) in enumerate(chunked_ranges(0, latest, log_chunk)):
        if i >= max_chunks:
            break
        entries = client.get_logs(to_checksum_address(pool), [V3_MINT_TOPIC], lo, hi)
        if entries:
            return min(e.block_number for e in entries)
    return None


def v3_lp_holdings(
    client: EvmClient,
    cfg: ChainConfig,
    registry: ChainRegistry,
    *,
    npm: str,
    pool: str,
    token0: str,
    token1: str,
    fee: int,
    from_block: int,
    deployer: str | None,
    owner: str | None,
    max_positions: int = 2_000,
    log_chunk: int = 50_000,
) -> list[LPHolding]:
    npm = to_checksum_address(npm)
    token0, token1 = to_checksum_address(token0), to_checksum_address(token1)
    latest = client.block_number()

    token_ids: list[int] = []
    for lo, hi in chunked_ranges(from_block, latest, log_chunk):
        for entry in client.get_logs(npm, [TRANSFER_TOPIC, topic_address(ZERO_ADDRESS)], lo, hi):
            if len(entry.topics) >= 4:
                token_ids.append(int.from_bytes(entry.topics[3], "big"))
            if len(token_ids) >= max_positions:
                break
        if len(token_ids) >= max_positions:
            log.warning("v3 position scan for %s capped at %d NPM mints", pool, max_positions)
            break
    if not token_ids:
        return []

    matching: dict[int, int] = {}  # tokenId -> liquidity
    for token_id, raw in zip(token_ids, client.call_many([positions_call(npm, t) for t in token_ids]), strict=True):
        if raw is None:
            continue
        try:
            fields = positions_call(npm, token_id).parse(raw)
        except Exception:  # noqa: BLE001 - unexpected ABI shape on a fork
            continue
        p_token0, p_token1, p_fee, liquidity = fields[2], fields[3], int(fields[4]), int(fields[7])
        if p_token0 == token0 and p_token1 == token1 and p_fee == fee and liquidity > 0:
            matching[token_id] = liquidity
    if not matching:
        return []

    ids = list(matching)
    per_owner: dict[str, int] = {}
    for token_id, raw in zip(ids, client.call_many([owner_of_call(npm, t) for t in ids]), strict=True):
        if raw is None:
            continue
        holder = owner_of_call(npm, token_id).one(raw)
        per_owner[holder] = per_owner.get(holder, 0) + matching[token_id]

    total = sum(per_owner.values())
    insiders = {to_checksum_address(a) for a in (deployer, owner) if a}
    holdings: list[LPHolding] = []
    for holder, liq in sorted(per_owner.items(), key=lambda kv: -kv[1]):
        locker = LockerKind.BURNED if registry.is_burn(holder) else cfg.locker_for(holder)
        if locker is None and holder not in insiders and client.get_code(holder):
            locker = LockerKind.UNKNOWN_CONTRACT
        holdings.append(
            LPHolding(
                holder=holder, share=min(1.0, liq / total), locker=locker, is_deployer_or_owner=holder in insiders
            )
        )
    excess = sum(h.share for h in holdings) - 1.0
    if excess > 0 and holdings:
        holdings[0] = holdings[0].model_copy(update={"share": max(0.0, holdings[0].share - excess)})
    return holdings[:25]


def topic_for_address(address: str) -> bytes:
    return topic_address(address)


__all__ = ["address_from_topic", "pool_created_block", "v3_lp_holdings"]
