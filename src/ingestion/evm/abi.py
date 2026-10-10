"""Minimal ABI helpers so the live provider can issue raw ``eth_call``s without
building full ``web3.contract`` objects (which makes faking in tests painful)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from eth_abi import decode as _abi_decode
from eth_abi import encode as _abi_encode
from eth_utils import function_signature_to_4byte_selector, to_checksum_address


@lru_cache(maxsize=256)
def selector(signature: str) -> bytes:
    """``"balanceOf(address)"`` → 4-byte selector."""
    return function_signature_to_4byte_selector(signature)


def _arg_types(signature: str) -> list[str]:
    """Top-level argument types of ``name(t1,t2,…)`` – tuple types such as ``(address,uint24)[]`` stay intact."""
    inner = signature[signature.index("(") + 1 : signature.rindex(")")]
    types: list[str] = []
    depth, current = 0, ""
    for ch in inner:
        if ch == "," and depth == 0:
            if current.strip():
                types.append(current.strip())
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        current += ch
    if current.strip():
        types.append(current.strip())
    return types


def encode_call(signature: str, *args: Any) -> bytes:
    types = _arg_types(signature)
    return selector(signature) + (_abi_encode(types, list(args)) if types else b"")


def decode(types: list[str] | tuple[str, ...], data: bytes) -> tuple[Any, ...]:
    if not data:
        raise ValueError("empty return data")
    out = _abi_decode(list(types), data)
    return tuple(to_checksum_address(v) if t == "address" else v for t, v in zip(types, out, strict=True))


@dataclass(frozen=True)
class Call:
    """A prepared read-only call: target + calldata + expected return types."""

    to: str
    signature: str
    args: tuple[Any, ...] = ()
    returns: tuple[str, ...] = ()
    value: int = 0  # native value attached (payable calls, e.g. router buys)

    @property
    def data(self) -> bytes:
        return encode_call(self.signature, *self.args)

    def parse(self, raw: bytes) -> tuple[Any, ...]:
        return decode(self.returns, raw) if self.returns else ()

    def one(self, raw: bytes) -> Any:
        return self.parse(raw)[0]


# Common ERC-20 / DEX calls --------------------------------------------------


def erc20_name(token: str) -> Call:
    return Call(token, "name()", returns=("string",))


def erc20_symbol(token: str) -> Call:
    return Call(token, "symbol()", returns=("string",))


def erc20_decimals(token: str) -> Call:
    return Call(token, "decimals()", returns=("uint8",))


def erc20_total_supply(token: str) -> Call:
    return Call(token, "totalSupply()", returns=("uint256",))


def erc20_balance_of(token: str, holder: str) -> Call:
    return Call(token, "balanceOf(address)", (holder,), returns=("uint256",))


def owner(contract: str) -> Call:
    return Call(contract, "owner()", returns=("address",))


def v2_get_pair(factory: str, a: str, b: str) -> Call:
    return Call(factory, "getPair(address,address)", (a, b), returns=("address",))


def v2_get_reserves(pair: str) -> Call:
    return Call(pair, "getReserves()", returns=("uint112", "uint112", "uint32"))


def v2_token0(pair: str) -> Call:
    return Call(pair, "token0()", returns=("address",))


def v3_get_pool(factory: str, a: str, b: str, fee: int) -> Call:
    return Call(factory, "getPool(address,address,uint24)", (a, b, fee), returns=("address",))


def v3_slot0(pool: str) -> Call:
    return Call(
        pool,
        "slot0()",
        returns=("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"),
    )


def v3_liquidity(pool: str) -> Call:
    return Call(pool, "liquidity()", returns=("uint128",))


def v2_router_get_amounts_out(router: str, amount_in: int, path: list[str]) -> Call:
    return Call(router, "getAmountsOut(uint256,address[])", (amount_in, path), returns=("uint256[]",))


# Multicall3 ------------------------------------------------------------------

#: Same address on Ethereum, BSC, Arbitrum, Base and most other EVM chains.
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3_SIG = "aggregate3((address,bool,bytes)[])"


def encode_aggregate3(calls: list[Call]) -> bytes:
    """Batch many read-only calls into one ``aggregate3`` call (every sub-call may fail)."""
    tuples = [(to_checksum_address(c.to), True, c.data) for c in calls]
    return selector(AGGREGATE3_SIG) + _abi_encode(["(address,bool,bytes)[]"], [tuples])


def decode_aggregate3(raw: bytes) -> list[bytes | None]:
    """``(bool success, bytes returnData)[]`` → return data, or ``None`` for failed/empty sub-calls."""
    (results,) = _abi_decode(["(bool,bytes)[]"], raw)
    return [bytes(data) if ok and data else None for ok, data in results]
