"""Live Web3.py provider — **stub**.

What works today (needs the ``live`` extra and an ``RUGSCAN_<CHAIN>_RPC_URL``):

* basic ERC-20 metadata (``name``/``symbol``/``decimals``/``totalSupply``)
* ``owner()`` lookup and EOA-vs-contract classification
* bytecode-level selector probing for a small set of privileged functions

What is intentionally **not** implemented yet and surfaces as ``None`` /
empty in the snapshot (heuristics then report ``data_available=False``):

* liquidity pool discovery and LP-locker resolution
* buy/sell simulation (requires a forked EVM or a router ``eth_call`` harness)
* holder distribution (needs an indexer such as Etherscan / Covalent / Dune)
* deployer funding-origin tracing (needs tx-history indexing + label sets)

Everything the stub returns is still a valid ``TokenSnapshot`` so the rest of
the pipeline runs end-to-end — it simply scores with less evidence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from config.settings import Settings, get_settings
from src.ingestion.base import BaseDataProvider, ProviderError
from src.models.risk_report import (
    BURN_ADDRESSES,
    Chain,
    ContractMetadata,
    FunctionSignature,
    OwnerType,
    TokenSnapshot,
)

log = logging.getLogger("rugscan.rpc")

ERC20_ABI: list[dict[str, Any]] = [
    {"name": "name", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
    {
        "name": "totalSupply",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint256"}],
    },
    {"name": "owner", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
]

# Selectors whose presence in the runtime bytecode is a strong hint of a capability.
PROBE_SIGNATURES: dict[str, tuple[str, ...]] = {
    "fee_setters": ("setTaxFee(uint256)", "setSellFee(uint256)", "setBuyFee(uint256)", "setFees(uint256,uint256)"),
    "max_tx_setters": ("setMaxTxAmount(uint256)", "setMaxTx(uint256)", "setMaxWallet(uint256)"),
    "mint_functions": ("mint(address,uint256)", "mint(uint256)"),
    "blacklist": ("blacklist(address)", "setBlacklist(address,bool)", "addBot(address)", "setBots(address[],bool)"),
    "pause": ("pause()", "setTradingEnabled(bool)", "enableTrading()"),
}


class RPCDataProvider(BaseDataProvider):
    name = "rpc"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._clients: dict[Chain, Any] = {}

    async def supports(self, chain: Chain) -> bool:
        return self.settings.rpc_url(chain) is not None

    def _web3(self, chain: Chain):
        if chain in self._clients:
            return self._clients[chain]
        url = self.settings.rpc_url(chain)
        if not url:
            raise ProviderError(f"no RPC URL configured for {chain} (set RUGSCAN_{chain.upper()}_RPC_URL)")
        try:
            from web3 import Web3
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderError("web3 is not installed; install with `pip install .[live]`") from exc
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": self.settings.rpc_timeout_s}))
        self._clients[chain] = w3
        return w3

    # ------------------------------------------------------------------ #
    async def fetch_snapshot(self, chain: Chain, address: str) -> TokenSnapshot:
        # web3's HTTP provider is blocking; push it off the event loop.
        return await asyncio.to_thread(self._fetch_sync, chain, address)

    def _fetch_sync(self, chain: Chain, address: str) -> TokenSnapshot:
        w3 = self._web3(chain)
        token = to_checksum_address(address)
        code = bytes(w3.eth.get_code(token))
        if not code:
            raise ProviderError(f"{token} has no bytecode on {chain} — not a contract")

        contract = w3.eth.contract(address=token, abi=ERC20_ABI)
        meta: dict[str, Any] = {}
        for fn in ("name", "symbol", "decimals", "totalSupply", "owner"):
            try:
                meta[fn] = contract.functions[fn]().call()
            except Exception as exc:
                log.debug("%s.%s() unavailable: %s", token, fn, exc)
                meta[fn] = None

        decimals = meta["decimals"] if meta["decimals"] is not None else 18
        supply = meta["totalSupply"] / 10**decimals if meta["totalSupply"] is not None else None

        owner = meta["owner"]
        owner_type = OwnerType.UNKNOWN
        if owner is not None:
            owner = to_checksum_address(owner)
            if owner in BURN_ADDRESSES:
                owner_type = OwnerType.RENOUNCED
            else:
                owner_type = OwnerType.CONTRACT if w3.eth.get_code(owner) else OwnerType.EOA

        probes = {key: self._probe(code, sigs) for key, sigs in PROBE_SIGNATURES.items()}

        metadata = ContractMetadata(
            name=meta["name"],
            symbol=meta["symbol"],
            decimals=decimals,
            total_supply=supply,
            source_verified=False,  # TODO: Etherscan-style verification lookup
            owner=owner,
            owner_type=owner_type,
            fee_setters=probes["fee_setters"],
            max_tx_setters=probes["max_tx_setters"],
            mint_functions=probes["mint_functions"],
            has_blacklist=bool(probes["blacklist"]),
            can_pause_trading=bool(probes["pause"]),
        )
        return TokenSnapshot(chain=chain, address=token, contract=metadata)

    @staticmethod
    def _probe(code: bytes, signatures: tuple[str, ...]) -> list[FunctionSignature]:
        found: list[FunctionSignature] = []
        for sig in signatures:
            selector = function_signature_to_4byte_selector(sig)
            if selector in code:
                # Bytecode alone can't tell us the guard or bound; leave them unknown.
                found.append(FunctionSignature(name=sig.split("(")[0], selector="0x" + selector.hex()))
        return found
