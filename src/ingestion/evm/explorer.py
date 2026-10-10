"""Etherscan-compatible (v2 multichain API) explorer client plus a fake for tests.

Only the endpoints the provider needs:

* ``getsourcecode``        – verification status, ABI, flattened source, proxy info
* ``getcontractcreation``  – deployer address + creation tx
* ``txlist``               – a wallet's earliest transactions (funding origin, age)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from eth_utils import to_checksum_address

log = logging.getLogger("rugscan.explorer")


@dataclass(frozen=True)
class ContractSource:
    verified: bool
    contract_name: str | None = None
    compiler_version: str | None = None
    abi: list[dict[str, Any]] | None = None
    source: str | None = None
    is_proxy: bool = False
    implementation: str | None = None


@dataclass(frozen=True)
class ExplorerTx:
    hash: str
    sender: str
    to: str | None
    value: int
    timestamp: datetime
    block_number: int
    is_contract_creation: bool = False
    is_error: bool = False


class Explorer(Protocol):
    def get_source(self, address: str) -> ContractSource: ...

    def get_contract_creation(self, address: str) -> tuple[str, str] | None: ...

    def transactions(self, address: str, *, ascending: bool = True, limit: int = 25) -> list[ExplorerTx]: ...


# --------------------------------------------------------------------------- #


class EtherscanClient:
    def __init__(
        self, api_url: str, chain_id: int, api_key: str | None, *, timeout: float = 20.0, min_interval: float = 0.25
    ):
        self.api_url = api_url
        self.chain_id = chain_id
        self.api_key = api_key
        self.timeout = timeout
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, **params: Any) -> Any:
        import requests

        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        query = {"chainid": self.chain_id, **params}
        if self.api_key:
            query["apikey"] = self.api_key
        resp = requests.get(self.api_url, params=query, timeout=self.timeout)
        self._last = time.monotonic()
        resp.raise_for_status()
        payload = resp.json()
        if str(payload.get("status")) != "1" and payload.get("message") != "OK":
            # "No transactions found" is status 0 with an empty result – not an error.
            if isinstance(payload.get("result"), list):
                return payload["result"]
            raise RuntimeError(f"explorer error: {payload.get('message')} – {payload.get('result')}")
        return payload["result"]

    def get_source(self, address: str) -> ContractSource:
        result = self._get(module="contract", action="getsourcecode", address=to_checksum_address(address))
        item = result[0] if isinstance(result, list) and result else {}
        source = item.get("SourceCode") or ""
        if source.startswith("{{"):  # standard-json input wrapped in extra braces
            try:
                parsed = json.loads(source[1:-1])
                source = "\n".join(v.get("content", "") for v in parsed.get("sources", {}).values())
            except json.JSONDecodeError:
                pass
        abi_raw = item.get("ABI") or ""
        abi = None
        if abi_raw and abi_raw != "Contract source code not verified":
            try:
                abi = json.loads(abi_raw)
            except json.JSONDecodeError:
                abi = None
        return ContractSource(
            verified=bool(source.strip()),
            contract_name=item.get("ContractName") or None,
            compiler_version=item.get("CompilerVersion") or None,
            abi=abi,
            source=source or None,
            is_proxy=str(item.get("Proxy", "0")) == "1",
            implementation=to_checksum_address(item["Implementation"]) if item.get("Implementation") else None,
        )

    def get_contract_creation(self, address: str) -> tuple[str, str] | None:
        try:
            result = self._get(
                module="contract", action="getcontractcreation", contractaddresses=to_checksum_address(address)
            )
        except RuntimeError as exc:
            log.debug("getcontractcreation failed: %s", exc)
            return None
        if not result:
            return None
        item = result[0]
        return to_checksum_address(item["contractCreator"]), item["txHash"]

    def transactions(self, address: str, *, ascending: bool = True, limit: int = 25) -> list[ExplorerTx]:
        result = self._get(
            module="account",
            action="txlist",
            address=to_checksum_address(address),
            startblock=0,
            endblock=99_999_999,
            page=1,
            offset=limit,
            sort="asc" if ascending else "desc",
        )
        out: list[ExplorerTx] = []
        for tx in result or []:
            out.append(
                ExplorerTx(
                    hash=tx["hash"],
                    sender=to_checksum_address(tx["from"]),
                    to=to_checksum_address(tx["to"]) if tx.get("to") else None,
                    value=int(tx.get("value", 0)),
                    timestamp=datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc),
                    block_number=int(tx["blockNumber"]),
                    is_contract_creation=not tx.get("to") or bool(tx.get("contractAddress")),
                    is_error=str(tx.get("isError", "0")) == "1",
                )
            )
        return out


# --------------------------------------------------------------------------- #


@dataclass
class FakeExplorer:
    sources: dict[str, ContractSource] = field(default_factory=dict)
    creations: dict[str, tuple[str, str]] = field(default_factory=dict)
    txs: dict[str, list[ExplorerTx]] = field(default_factory=dict)

    def get_source(self, address: str) -> ContractSource:
        return self.sources.get(to_checksum_address(address), ContractSource(verified=False))

    def get_contract_creation(self, address: str) -> tuple[str, str] | None:
        return self.creations.get(to_checksum_address(address))

    def transactions(self, address: str, *, ascending: bool = True, limit: int = 25) -> list[ExplorerTx]:
        txs = sorted(
            self.txs.get(to_checksum_address(address), []), key=lambda t: t.block_number, reverse=not ascending
        )
        return txs[:limit]
