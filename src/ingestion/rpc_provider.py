"""Live Web3.py provider.

Builds a ``TokenSnapshot`` from an RPC endpoint plus (optionally) an
Etherscan-compatible explorer API. Every sub-fetch is isolated: a failure in
one leaves that part of the snapshot ``None`` and is logged, so a single flaky
endpoint degrades the scan instead of aborting it.

Pipeline
--------
1. bytecode + ERC-20 metadata + ``owner()`` (RPC)
2. verified source / ABI (explorer) → capability analysis
3. deployer provenance (explorer)
4. pool discovery, LP holders, native price (RPC)
5. token holder distribution by Transfer-log replay (RPC)
6. sell simulation via ``eth_call`` state overrides (RPC)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from eth_utils import to_checksum_address

from config.chains import ChainRegistry, load_chain_registry
from config.settings import Settings, get_settings
from src.ingestion.base import BaseDataProvider, ProviderError
from src.ingestion.evm import abi
from src.ingestion.evm.capabilities import Capabilities, analyse_abi, analyse_source, merge, probe_bytecode
from src.ingestion.evm.client import EvmClient, Web3EvmClient
from src.ingestion.evm.deployer import find_insider_cluster, trace_deployer
from src.ingestion.evm.explorer import EtherscanClient, Explorer
from src.ingestion.evm.fingerprint import fingerprint
from src.ingestion.evm.pools import PoolDiscovery
from src.ingestion.evm.simulate import SellSimulator
from src.ingestion.evm.trades import analyse_swaps
from src.models.risk_report import (
    BURN_ADDRESSES,
    BytecodeMatch,
    Chain,
    ContractMetadata,
    Holder,
    HolderDistribution,
    OwnerType,
    ProxyKind,
    TokenSnapshot,
)

log = logging.getLogger("rugscan.rpc")

ClientFactory = Callable[[Chain], EvmClient]
ExplorerFactory = Callable[[Chain], Explorer | None]
PriorRugLookup = Callable[[str], tuple[int | None, list[str]]]
SimilarityLookup = Callable[..., list[dict[str, Any]]]  # (code_hash, selectors, min_similarity=) -> matches

SAFE_SINGLETON_CODE_MARKERS = (b"\x5a\xf4",)  # delegatecall-based proxies (Gnosis Safe, minimal proxies)


class RPCDataProvider(BaseDataProvider):
    name = "rpc"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: ChainRegistry | None = None,
        client_factory: ClientFactory | None = None,
        explorer_factory: ExplorerFactory | None = None,
        prior_rug_lookup: PriorRugLookup | None = None,
        similarity_lookup: SimilarityLookup | None = None,
        simulate_sells: bool = True,
        swap_window_blocks: int = 200_000,
    ):
        self.swap_window_blocks = swap_window_blocks
        self.settings = settings or get_settings()
        self.registry = registry or load_chain_registry()
        self._injected_client = client_factory is not None
        self._client_factory = client_factory or self._default_client
        self._explorer_factory = explorer_factory or self._default_explorer
        self._prior_rug_lookup = prior_rug_lookup
        self._similarity_lookup = similarity_lookup
        self.simulate_sells = simulate_sells
        self._clients: dict[Chain, EvmClient] = {}

    # ------------------------------------------------------------------ #
    # factories
    # ------------------------------------------------------------------ #
    def _default_client(self, chain: Chain) -> EvmClient:
        url = self.settings.rpc_url(chain)
        if not url:
            raise ProviderError(f"no RPC URL configured for {chain} (set RUGSCAN_{chain.upper()}_RPC_URL)")
        try:
            from web3 import Web3
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderError("web3 is not installed; install with `pip install .[live]`") from exc
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": self.settings.rpc_timeout_s}))
        return Web3EvmClient(w3, chain_id=self.registry.get(chain).chain_id)

    def _default_explorer(self, chain: Chain) -> Explorer | None:
        cfg = self.registry.get(chain)
        if not cfg.explorer_api:
            return None
        key = self.settings.etherscan_api_key.get_secret_value() if self.settings.etherscan_api_key else None
        if not key:
            log.info("no RUGSCAN_ETHERSCAN_API_KEY – source verification and deployer tracing skipped")
            return None
        return EtherscanClient(cfg.explorer_api, cfg.chain_id, key, timeout=self.settings.rpc_timeout_s)

    def _client(self, chain: Chain) -> EvmClient:
        if chain not in self._clients:
            self._clients[chain] = self._client_factory(chain)
        return self._clients[chain]

    async def supports(self, chain: Chain) -> bool:
        return self._injected_client or self.settings.rpc_url(chain) is not None

    # ------------------------------------------------------------------ #
    async def fetch_snapshot(self, chain: Chain, address: str, *, block: int | None = None) -> TokenSnapshot:
        return await asyncio.to_thread(self.fetch_sync, chain, address, block)

    def fetch_sync(self, chain: Chain, address: str, block: int | None = None) -> TokenSnapshot:
        chain = Chain(chain)
        client = self._client(chain)
        if block is not None:
            client = client.at(block)  # every read below is pinned → snapshot "as of" that block
        cfg = self.registry.get(chain)
        token = to_checksum_address(address)
        block_number = client.block_number()
        now = (client.block_timestamp() if block is not None else None) or datetime.now(timezone.utc)

        code = client.get_code(token)
        if not code:
            raise ProviderError(f"{token} has no bytecode on {chain.value} — not a contract")

        meta = self._erc20_metadata(client, token)
        owner, owner_type = self._owner(client, token)
        explorer = self._safe(lambda: self._explorer_factory(chain), "explorer init")
        source = self._safe(lambda: explorer.get_source(token) if explorer else None, "source lookup")
        caps = self._capabilities(code, source)
        fp = fingerprint(code)
        matches: list[BytecodeMatch] = []
        similarity = self._similarity_lookup
        if similarity is not None:
            raw_matches = self._safe(
                lambda: similarity(fp.code_hash, fp.selectors, min_similarity=0.5), "bytecode similarity"
            )
            matches = [BytecodeMatch.model_validate(m) for m in raw_matches or [] if m.get("address") != token]

        deployer = None
        if explorer is not None:
            deployer = self._safe(
                lambda: trace_deployer(explorer, cfg, token, prior_rug_lookup=self._prior_rug_lookup), "deployer trace"
            )

        discovery = PoolDiscovery(client, cfg, self.registry)
        pools = (
            self._safe(
                lambda: discovery.discover(
                    token, deployer=deployer.address if deployer else None, owner=owner, now=now
                ),
                "pool discovery",
            )
            or []
        )

        price_usd = self._token_price(discovery, pools, meta["decimals"])
        holders = (
            self._safe(lambda: self._holders(discovery, token, pools, cfg), "holder replay") or HolderDistribution()
        )
        if deployer is not None and explorer is not None and holders.top_holders:
            candidates = [h.address for h in holders.circulating_top_n(10) if not h.is_contract]
            deployer_addr, ex = deployer.address, explorer
            funded = self._safe(lambda: find_insider_cluster(ex, deployer_addr, candidates), "insider cluster")
            if funded:
                deployer = deployer.model_copy(update={"funded_wallets": funded})

        trades = None
        if pools and pools[0].dex in cfg.dexes:  # v2-style primary pool – v3 Swap events have a different shape
            primary = pools[0]
            trades = self._safe(
                lambda: analyse_swaps(
                    client,
                    primary.pair_address,
                    token_is_token0=self._is_token0(client, primary.pair_address, token),
                    quote_decimals=discovery.decimals(cfg.quote_tokens[primary.quote_token]),
                    from_block=primary.created_block or max(0, block_number - self.swap_window_blocks),
                    to_block=block_number,
                    created_block=primary.created_block,
                ),
                "swap analysis",
            )

        sell_sim = None
        if self.simulate_sells and pools:
            sell_sim = self._safe(
                lambda: SellSimulator(client, cfg, discovery).simulate(token, pools[0]), "sell simulation"
            )

        proxy_kind = ProxyKind.NONE
        implementation = None
        implementation_verified = None
        if source and source.is_proxy:
            proxy_kind = ProxyKind.UNKNOWN
            implementation = source.implementation
            if implementation and explorer is not None:
                impl_src = self._safe(lambda: explorer.get_source(implementation), "implementation source")
                implementation_verified = bool(impl_src and impl_src.verified)

        declared = self._declared_taxes(client, token)

        contract = ContractMetadata(
            name=meta["name"],
            symbol=meta["symbol"],
            decimals=meta["decimals"],
            total_supply=meta["total_supply"],
            source_verified=bool(source and source.verified),
            compiler_version=source.compiler_version if source else None,
            deployer=deployer.address if deployer else None,
            owner=owner,
            owner_type=owner_type,
            proxy_kind=proxy_kind,
            implementation=implementation,
            implementation_verified=implementation_verified,
            buy_tax_pct=declared.get("buy"),
            sell_tax_pct=declared.get("sell"),
            fee_setters=caps.fee_setters,
            max_tx_setters=caps.max_tx_setters,
            mint_functions=caps.mint_functions,
            has_blacklist=caps.has_blacklist,
            has_whitelist_only_transfers=caps.has_whitelist_only_transfers,
            can_pause_trading=caps.can_pause_trading,
            has_selfdestruct=caps.has_selfdestruct,
            has_delegatecall=caps.has_delegatecall,
            code_hash=fp.code_hash,
            selectors=sorted(fp.selectors),
            bytecode_matches=matches,
        )
        return TokenSnapshot(
            chain=chain,
            address=token,
            observed_at=now,
            block_number=block_number,
            price_usd=price_usd,
            contract=contract,
            pools=pools,
            sell_simulation=sell_sim,
            trades=trades,
            holders=holders,
            deployer=deployer,
        )

    # ------------------------------------------------------------------ #
    # pieces
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_token0(client: EvmClient, pair: str, token: str) -> bool:
        raw = client.try_call(abi.v2_token0(pair))
        if not raw:
            return False
        return bool(abi.v2_token0(pair).one(raw) == token)

    @staticmethod
    def _safe(fn: Callable[[], Any], what: str) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed: %s", what, exc)
            return None

    def _erc20_metadata(self, client: EvmClient, token: str) -> dict[str, Any]:
        calls = [
            abi.erc20_name(token),
            abi.erc20_symbol(token),
            abi.erc20_decimals(token),
            abi.erc20_total_supply(token),
        ]
        values: list[Any] = []
        for call, raw in zip(calls, client.call_many(calls), strict=True):
            try:
                values.append(call.one(raw) if raw else None)
            except Exception:  # noqa: BLE001 - non-standard return encodings (bytes32 names etc.)
                values.append(None)
        name, symbol, decimals, supply_raw = values
        decimals = int(decimals) if decimals is not None else 18
        return {
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "total_supply": supply_raw / 10**decimals if supply_raw is not None else None,
        }

    def _owner(self, client: EvmClient, token: str) -> tuple[str | None, OwnerType]:
        raw = client.try_call(abi.owner(token))
        if raw is None:
            return None, OwnerType.UNKNOWN
        try:
            owner = abi.owner(token).one(raw)
        except Exception:  # noqa: BLE001
            return None, OwnerType.UNKNOWN
        if owner in BURN_ADDRESSES:
            return owner, OwnerType.RENOUNCED
        code = client.get_code(owner)
        if not code:
            return owner, OwnerType.EOA
        if any(m in code for m in SAFE_SINGLETON_CODE_MARKERS) and len(code) < 200:
            return owner, OwnerType.MULTISIG  # proxy-shaped owner: most likely a Safe
        if client.try_call(abi.Call(owner, "getMinDelay()", returns=("uint256",))) is not None:
            return owner, OwnerType.TIMELOCK  # OpenZeppelin TimelockController
        return owner, OwnerType.CONTRACT

    @staticmethod
    def _capabilities(code: bytes, source: Any) -> Capabilities:
        probed = probe_bytecode(code)
        if source is None or not source.verified:
            return probed
        from_source = analyse_source(source.source or "")
        from_abi = analyse_abi(source.abi or [])
        return merge(merge(from_source, from_abi), probed)

    def _declared_taxes(self, client: EvmClient, token: str) -> dict[str, float]:
        """Read common public tax getters; values are normalised to percent."""
        getters = {
            "buy": ("buyTax()", "_buyTax()", "buyFee()", "_buyFee()", "totalBuyTax()", "buyTotalFees()", "taxFee()"),
            "sell": ("sellTax()", "_sellTax()", "sellFee()", "_sellFee()", "totalSellTax()", "sellTotalFees()"),
        }
        calls = [
            (side, abi.Call(token, name, returns=("uint256",))) for side, names in getters.items() for name in names
        ]
        results = client.call_many([c for _, c in calls])
        out: dict[str, float] = {}
        for (side, call), raw in zip(calls, results, strict=True):
            if raw is None or side in out:
                continue
            try:
                value = int(call.one(raw))
            except Exception:  # noqa: BLE001
                continue
            pct = float(value) if value <= 100 else value / 100.0 if value <= 10_000 else None
            if pct is not None:
                out[side] = min(100.0, pct)
        return out

    @staticmethod
    def _token_price(discovery: PoolDiscovery, pools: list, decimals: int) -> float | None:
        for pool in pools:
            if pool.token_reserve and pool.quote_reserve:
                quote_usd = discovery.quote_usd(pool.quote_token, discovery.native_price())
                if quote_usd is not None:
                    return pool.quote_reserve * quote_usd / pool.token_reserve
        return None

    def _holders(self, discovery: PoolDiscovery, token: str, pools: list, cfg: Any) -> HolderDistribution:
        balances, _ = discovery.replay_balances(token)
        if not balances:
            return HolderDistribution()
        supply = sum(v for v in balances.values() if v > 0)
        if supply <= 0:
            return HolderDistribution()
        pair_addrs = {p.pair_address: p.dex for p in pools}
        holders: list[Holder] = []
        for addr, bal in sorted(balances.items(), key=lambda kv: -kv[1])[:25]:
            if bal <= 0:
                continue
            label = None
            dex_or_burn = False
            if addr in pair_addrs:
                label, dex_or_burn = f"{pair_addrs[addr]}_pair", True
            elif self.registry.is_burn(addr):
                label, dex_or_burn = "burn", True
            elif cfg.locker_for(addr):
                label = cfg.locker_for(addr).value
            holders.append(
                Holder(
                    address=addr,
                    share=min(1.0, bal / supply),
                    label=label,
                    is_contract=bool(discovery.client.get_code(addr)),
                    is_dex_or_burn=dex_or_burn,
                )
            )
        excess = sum(h.share for h in holders) - 1.0
        if excess > 0 and holders:
            holders[0] = holders[0].model_copy(update={"share": max(0.0, holders[0].share - excess)})
        return HolderDistribution(holder_count=sum(1 for v in balances.values() if v > 0), top_holders=holders)
