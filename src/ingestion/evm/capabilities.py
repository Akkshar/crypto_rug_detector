"""Static capability analysis of a token contract from its verified ABI and
Solidity source (falling back to bytecode selector probing when unverified).

This is deliberately a lightweight regex/brace-matching pass – no solc, no
Slither – so it runs anywhere in milliseconds. It answers the questions the
contract inspector heuristic asks:

* which functions can change fees / limits, and are they bounded?
* which functions can expand supply, and are any hidden (no "mint" in name)?
* does the transfer path consult a blacklist, a whitelist or a trading switch?
* does the code contain ``selfdestruct`` or ``delegatecall``?
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from eth_utils import function_signature_to_4byte_selector

from src.models.risk_report import FunctionSignature

FEE_SETTER_RE = re.compile(r"^(set|update|change|modify|adjust)\w*(fee|tax)\w*$", re.I)
LIMIT_SETTER_RE = re.compile(
    r"^(set|update|change|remove)\w*(maxtx|maxtransaction|maxwallet|maxbuy|maxsell|maxamount|limit)\w*$", re.I
)
MINT_NAME_RE = re.compile(r"mint", re.I)
# Mapping reads like ``isBlacklisted[x]``, ``_blacklist[x]``, ``_isWhitelisted[x]``; the leading
# underscore is a word character, so it must be consumed before the keyword rather than anchored past.
BLACKLIST_RE = re.compile(r"(?<![\w])_?(is)?(black|bl|bot|ban|block|snipe)(list|ed|s|ted)?\w*\s*\[", re.I)
WHITELIST_RE = re.compile(r"(?<![\w])_?(is)?(white|allow)(list|ed)\w*\s*\[", re.I)
TRADING_SWITCH_RE = re.compile(
    r"\b(tradingEnabled|tradingOpen|tradingActive|tradingLive|isTradingEnabled|_paused|paused\(\)|canTrade|launched|swapEnabled)\b"
)
SUPPLY_EXPANSION_RE = re.compile(
    r"(\b_mint\s*\(|_?totalSupply\s*\+=|_?totalSupply\s*=\s*_?totalSupply\s*\+|_?tTotal\s*\+=|_balances\s*\[[^\]]+\]\s*\+=)"
)
BOUND_RE = re.compile(r"require\s*\(\s*\(?\s*\w+(?:\s*\+\s*\w+)*\s*<=?\s*(\d+)", re.I)
REVERT_BOUND_RE = re.compile(r"if\s*\(\s*\w+(?:\s*\+\s*\w+)*\s*>=?\s*(\d+)\s*\)\s*revert", re.I)
TRANSFER_FN_RE = re.compile(
    r"^(_transfer|_update|_beforeTokenTransfer|transfer|transferFrom|_tokenTransfer|_transferStandard)$"
)
GOVERNANCE_MODIFIER_RE = re.compile(
    r"only(Owner|Admin|Dev|Marketing|Operator|Manager|Authorized|Team)|onlyRole\(([^)]*)\)", re.I
)

PROBE_SIGNATURES: dict[str, tuple[str, ...]] = {
    "fee_setters": (
        "setTaxFee(uint256)",
        "setSellFee(uint256)",
        "setBuyFee(uint256)",
        "setFees(uint256,uint256)",
        "setFee(uint256)",
        "updateFees(uint256,uint256)",
        "setTaxes(uint256,uint256)",
    ),
    "max_tx_setters": (
        "setMaxTxAmount(uint256)",
        "setMaxTx(uint256)",
        "setMaxWallet(uint256)",
        "setMaxWalletAmount(uint256)",
    ),
    "mint_functions": ("mint(address,uint256)", "mint(uint256)"),
    "blacklist": (
        "blacklist(address)",
        "setBlacklist(address,bool)",
        "addBot(address)",
        "setBots(address[],bool)",
        "blockAccount(address,bool)",
    ),
    "pause": ("pause()", "setTradingEnabled(bool)", "enableTrading()", "openTrading()", "setTrading(bool)"),
}


@dataclass
class Capabilities:
    fee_setters: list[FunctionSignature] = field(default_factory=list)
    max_tx_setters: list[FunctionSignature] = field(default_factory=list)
    mint_functions: list[FunctionSignature] = field(default_factory=list)
    has_blacklist: bool = False
    has_whitelist_only_transfers: bool = False
    can_pause_trading: bool = False
    has_selfdestruct: bool = False
    has_delegatecall: bool = False
    source_analysed: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SolFunction:
    name: str
    params: str
    header: str
    body: str

    @property
    def modifiers(self) -> list[str]:
        return re.findall(r"\b(only\w+(?:\([^)]*\))?)", self.header)

    @property
    def visibility(self) -> str:
        m = re.search(r"\b(public|external|internal|private)\b", self.header)
        return m.group(1) if m else "public"

    @property
    def restricted_to(self) -> str | None:
        mods = self.modifiers
        return mods[0] if mods else None


def extract_functions(source: str) -> list[SolFunction]:
    """Brace-matched function extraction – tolerant of flattened multi-contract files."""
    source = _strip_comments(source)
    out: list[SolFunction] = []
    for m in re.finditer(r"\bfunction\s+(\w+)\s*\(([^)]*)\)([^{;]*)\{", source):
        name, params, header = m.group(1), m.group(2), m.group(3)
        depth, i = 1, m.end()
        while i < len(source) and depth:
            depth += {"{": 1, "}": -1}.get(source[i], 0)
            i += 1
        out.append(SolFunction(name=name, params=params.strip(), header=header, body=source[m.end() : i - 1]))
    return out


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _bound_pct(body: str) -> float | None:
    """Largest literal in a ``require(x <= N)`` / ``if (x > N) revert`` guard, normalised to percent."""
    hits = [int(v) for v in BOUND_RE.findall(body)] + [int(v) for v in REVERT_BOUND_RE.findall(body)]
    if not hits:
        return None
    n = max(hits)
    if n <= 100:
        return float(n)
    if n <= 10_000:
        return n / 100.0  # basis points
    return None


def _sig(fn: SolFunction, *, hidden: bool = False, bound: float | None = None) -> FunctionSignature:
    types = ",".join(p.strip().split()[0] for p in fn.params.split(",") if p.strip()) if fn.params else ""
    sel = (
        "0x" + function_signature_to_4byte_selector(f"{fn.name}({types})").hex()
        if fn.visibility in {"public", "external"}
        else None
    )
    return FunctionSignature(
        name=fn.name, selector=sel, restricted_to=fn.restricted_to, upper_bound_pct=bound, hidden=hidden
    )


def analyse_source(source: str) -> Capabilities:
    caps = Capabilities(source_analysed=True)
    fns = extract_functions(source)
    seen: set[str] = set()
    for fn in fns:
        if fn.name in seen:
            continue
        if fn.visibility in {"internal", "private"} and not SUPPLY_EXPANSION_RE.search(fn.body):
            continue
        seen.add(fn.name)
        if FEE_SETTER_RE.match(fn.name) and fn.visibility in {"public", "external"}:
            caps.fee_setters.append(_sig(fn, bound=_bound_pct(fn.body)))
        elif LIMIT_SETTER_RE.match(fn.name) and fn.visibility in {"public", "external"}:
            caps.max_tx_setters.append(_sig(fn, bound=_bound_pct(fn.body)))
        if (
            SUPPLY_EXPANSION_RE.search(fn.body)
            and fn.name not in {"constructor", "_mint"}
            and fn.visibility in {"public", "external"}
        ):
            caps.mint_functions.append(_sig(fn, hidden=not MINT_NAME_RE.search(fn.name)))

    transfer_bodies = "\n".join(fn.body for fn in fns if TRANSFER_FN_RE.match(fn.name))
    if transfer_bodies:
        caps.has_blacklist = bool(BLACKLIST_RE.search(transfer_bodies))
        if WHITELIST_RE.search(transfer_bodies) and re.search(r"require\s*\([^;]*(white|allow)", transfer_bodies, re.I):
            caps.has_whitelist_only_transfers = True
        caps.can_pause_trading = bool(TRADING_SWITCH_RE.search(transfer_bodies))
    else:
        caps.notes.append("no transfer function found in source – gate analysis skipped")

    caps.has_selfdestruct = bool(re.search(r"\b(selfdestruct|suicide)\s*\(", source))
    caps.has_delegatecall = bool(re.search(r"\.delegatecall\s*\(", source))
    return caps


def analyse_abi(abi: list[dict[str, Any]]) -> Capabilities:
    """ABI-only pass: names and guards are visible, bodies (bounds, hidden mints) are not."""
    caps = Capabilities()
    for item in abi:
        if item.get("type") != "function" or item.get("stateMutability") in {"view", "pure"}:
            continue
        name = item.get("name", "")
        types = ",".join(i.get("type", "") for i in item.get("inputs", []))
        sel = "0x" + function_signature_to_4byte_selector(f"{name}({types})").hex()
        sig = FunctionSignature(name=name, selector=sel)
        if FEE_SETTER_RE.match(name):
            caps.fee_setters.append(sig)
        elif LIMIT_SETTER_RE.match(name):
            caps.max_tx_setters.append(sig)
        elif MINT_NAME_RE.search(name) and "burn" not in name.lower():
            caps.mint_functions.append(sig)
        elif re.search(r"black|bot|ban|block", name, re.I) and types:
            caps.has_blacklist = True
        elif re.search(r"^(pause|unpause|enableTrading|openTrading|setTrading)", name, re.I):
            caps.can_pause_trading = True
    return caps


def probe_bytecode(code: bytes) -> Capabilities:
    caps = Capabilities()
    for key, sigs in PROBE_SIGNATURES.items():
        found = [
            FunctionSignature(name=s.split("(")[0], selector="0x" + function_signature_to_4byte_selector(s).hex())
            for s in sigs
            if function_signature_to_4byte_selector(s) in code
        ]
        if key == "blacklist":
            caps.has_blacklist = bool(found)
        elif key == "pause":
            caps.can_pause_trading = bool(found)
        else:
            setattr(caps, key, found)
    caps.has_selfdestruct = b"\xff" in code and _has_opcode(code, 0xFF)
    caps.has_delegatecall = _has_opcode(code, 0xF4)
    return caps


def _has_opcode(code: bytes, opcode: int) -> bool:
    """Walk the bytecode skipping PUSH immediates so data bytes aren't mistaken for opcodes."""
    i = 0
    while i < len(code):
        op = code[i]
        if op == opcode:
            return True
        i += 1 + (op - 0x5F if 0x60 <= op <= 0x7F else 0)
    return False


def merge(primary: Capabilities, fallback: Capabilities) -> Capabilities:
    """Prefer source-derived facts; fill gaps from the ABI / bytecode pass."""
    out = Capabilities(
        fee_setters=primary.fee_setters or fallback.fee_setters,
        max_tx_setters=primary.max_tx_setters or fallback.max_tx_setters,
        mint_functions=primary.mint_functions or fallback.mint_functions,
        has_blacklist=primary.has_blacklist or fallback.has_blacklist,
        has_whitelist_only_transfers=primary.has_whitelist_only_transfers or fallback.has_whitelist_only_transfers,
        can_pause_trading=primary.can_pause_trading or fallback.can_pause_trading,
        has_selfdestruct=primary.has_selfdestruct or fallback.has_selfdestruct,
        has_delegatecall=primary.has_delegatecall or fallback.has_delegatecall,
        source_analysed=primary.source_analysed or fallback.source_analysed,
        notes=primary.notes + fallback.notes,
    )
    return out
