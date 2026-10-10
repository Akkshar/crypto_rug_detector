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


def find_insider_cluster(
    explorer: Explorer,
    deployer: str,
    candidates: list[str],
    *,
    max_lookups: int = 10,
    tx_limit: int = 5,
) -> list[str]:
    """Wallets among ``candidates`` whose first incoming value transfer came from the deployer.

    "Organic" holders bought with their own money; wallets the deployer seeded with gas before
    launch are the deployer's own hands. One explorer call per candidate, so the caller passes
    only the top-N non-contract holders.
    """
    deployer = to_checksum_address(deployer)
    funded: list[str] = []
    for wallet in candidates[:max_lookups]:
        wallet = to_checksum_address(wallet)
        if wallet == deployer:
            continue
        try:
            txs = explorer.transactions(wallet, ascending=True, limit=tx_limit)
        except Exception as exc:  # noqa: BLE001 - one failed lookup must not sink the scan
            log.debug("insider lookup failed for %s: %s", wallet, exc)
            continue
        for tx in txs:
            if tx.sender == wallet:
                break  # already active before any visible deposit – funded some other way (internal tx, bridge…)
            if tx.to == wallet and tx.value > 0:
                if tx.sender == deployer:
                    funded.append(wallet)
                break  # only the *first* funding matters
    return funded
