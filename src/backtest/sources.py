"""External label sources for building a corpus.

GoPlus Security exposes a free, key-less token-security endpoint whose fields
(``is_honeypot``, ``cannot_sell_all``, ``owner_change_balance`` …) are
themselves heuristic verdicts. Labels derived from it are therefore **weak
labels**: good for bootstrapping a few hundred examples quickly, but partly
circular with this project's own rules, so a backtest on GoPlus-labelled data
measures agreement with GoPlus more than ground truth. Prefer confirmed rugs
(liquidity actually removed, tokens dumped) for the final evaluation and say
which label source each number came from.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from eth_utils import to_checksum_address

from src.models.risk_report import Chain

log = logging.getLogger("rugscan.sources")

GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
CHAIN_IDS = {Chain.ETHEREUM: 1, Chain.BSC: 56, Chain.ARBITRUM: 42161, Chain.BASE: 8453}

#: Any of these set to "1" is treated as a rug/honeypot signal.
RUG_FIELDS = ("is_honeypot", "cannot_sell_all", "owner_change_balance", "selfdestruct", "honeypot_with_same_creator")
#: A "clean" token must have every one of these at "0" (or absent) …
CLEAN_MUST_BE_ZERO = (
    "is_honeypot",
    "cannot_sell_all",
    "cannot_buy",
    "owner_change_balance",
    "selfdestruct",
    "hidden_owner",
    "is_blacklisted",
    "transfer_pausable",
    "is_mintable",
    "external_call",
    "is_proxy",
)
#: … and both of these satisfied.
CLEAN_MAX_TAX = 0.10


@dataclass(frozen=True)
class WeakLabel:
    chain: Chain
    address: str
    label: str | None  # "rug" | "benign" | None (undecided)
    reasons: tuple[str, ...]
    raw: dict[str, Any]


HttpGet = Callable[[str, dict[str, Any]], dict[str, Any]]


def _default_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    import requests

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _flag(info: dict[str, Any], key: str) -> bool:
    return str(info.get(key, "0")) == "1"


def _tax(info: dict[str, Any], key: str) -> float | None:
    try:
        v = info.get(key)
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def classify(info: dict[str, Any], *, benign_if_clean: bool) -> tuple[str | None, tuple[str, ...]]:
    reasons = [k for k in RUG_FIELDS if _flag(info, k)]
    sell_tax = _tax(info, "sell_tax")
    if sell_tax is not None and sell_tax >= 0.9:
        reasons.append(f"sell_tax={sell_tax:.0%}")
    if reasons:
        return "rug", tuple(reasons)
    if not benign_if_clean:
        return None, ()
    dirty = [k for k in CLEAN_MUST_BE_ZERO if _flag(info, k)]
    buy_tax, sell_tax = _tax(info, "buy_tax"), _tax(info, "sell_tax")
    if dirty or any(t is not None and t > CLEAN_MAX_TAX for t in (buy_tax, sell_tax)):
        return None, tuple(dirty)
    if not _flag(info, "is_open_source"):
        return None, ("not open source",)
    lp_locked = any(str(h.get("is_locked", "0")) == "1" for h in info.get("lp_holders", []) or [])
    if not lp_locked:
        return None, ("lp not locked",)
    return "benign", ("clean: open source, lp locked, taxes <= 10%, no risk fields",)


def fetch_goplus(
    chain: Chain, addresses: Iterable[str], *, get: HttpGet | None = None, batch: int = 30
) -> dict[str, dict[str, Any]]:
    """Raw GoPlus records keyed by checksummed address. Missing tokens are simply absent."""
    get = get or _default_get
    chain_id = CHAIN_IDS[Chain(chain)]
    out: dict[str, dict[str, Any]] = {}
    todo = [to_checksum_address(a) for a in addresses]
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        try:
            payload = get(GOPLUS_URL.format(chain_id=chain_id), {"contract_addresses": ",".join(chunk)})
        except Exception as exc:  # noqa: BLE001 - keep going with the other batches
            log.warning("GoPlus batch %d-%d failed: %s", i, i + len(chunk), exc)
            continue
        for addr, info in (payload.get("result") or {}).items():
            try:
                out[to_checksum_address(addr)] = info
            except ValueError:
                continue
    return out


def weak_labels(
    chain: Chain, addresses: Iterable[str], *, benign_if_clean: bool = True, get: HttpGet | None = None
) -> list[WeakLabel]:
    records = fetch_goplus(chain, addresses, get=get)
    labels: list[WeakLabel] = []
    for addr in {to_checksum_address(a) for a in addresses}:
        info = records.get(addr)
        if info is None:
            labels.append(WeakLabel(Chain(chain), addr, None, ("not found at GoPlus",), {}))
            continue
        label, reasons = classify(info, benign_if_clean=benign_if_clean)
        labels.append(WeakLabel(Chain(chain), addr, label, reasons, info))
    return labels
