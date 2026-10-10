"""Deployer provenance: who deployed the token, when the wallet first appeared,
where its first gas came from, how many contracts it has launched, and –
when a labelled snapshot cache is available – how many of its past tokens
were marked as rugs."""

from __future__ import annotations

import logging

from eth_utils import to_checksum_address

from config.chains import ChainConfig
from src.ingestion.evm.explorer import Explorer
from src.models.risk_report import DeployerProfile, FundingSourceKind

log = logging.getLogger("rugscan.deployer")


def trace_deployer(
    explorer: Explorer,
    chain_cfg: ChainConfig,
    token: str,
    *,
    tx_limit: int = 1000,
    prior_rug_lookup=None,
) -> DeployerProfile | None:
    creation = explorer.get_contract_creation(token)
    if creation is None:
        return None
    deployer, _creation_tx = creation
    deployer = to_checksum_address(deployer)

    txs = explorer.transactions(deployer, ascending=True, limit=tx_limit)
    first_seen = txs[0].timestamp if txs else None

    funding_source: str | None = None
    funding_kind = FundingSourceKind.UNKNOWN
    funding_label: str | None = None
    for tx in txs:
        if tx.to == deployer and tx.value > 0 and tx.sender != deployer:
            funding_source = tx.sender
            funding_kind, funding_label = chain_cfg.label_funding_source(tx.sender)
            if funding_kind is FundingSourceKind.UNKNOWN:
                funding_kind = FundingSourceKind.EOA  # a plain wallet; could be refined with code check
            break

    creations = sum(1 for tx in txs if tx.sender == deployer and tx.is_contract_creation and not tx.is_error)
    contracts_deployed: int | None = creations if txs else None

    prior_rugs = None
    linked: list[str] = []
    if prior_rug_lookup is not None:
        try:
            prior_rugs, linked = prior_rug_lookup(deployer)
        except Exception as exc:  # noqa: BLE001
            log.debug("prior rug lookup failed: %s", exc)

    return DeployerProfile(
        address=deployer,
        first_seen=first_seen,
        first_funding_source=funding_source,
        first_funding_kind=funding_kind,
        first_funding_label=funding_label,
        contracts_deployed=contracts_deployed,
        prior_rug_count=prior_rugs,
        linked_scam_addresses=linked,
    )
