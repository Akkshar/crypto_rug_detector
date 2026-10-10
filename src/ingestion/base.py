"""Abstract data provider contract.

A provider turns ``(chain, address)`` into a fully-validated ``TokenSnapshot``.
Heuristics never talk to the network directly; they only consume snapshots, so
the same engine runs against offline fixtures, cached JSON, or a live RPC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.risk_report import Chain, TokenSnapshot


class ProviderError(RuntimeError):
    """Base class for provider failures."""


class SnapshotNotFound(ProviderError):
    """Raised when the provider has no data for the requested token."""


class BaseDataProvider(ABC):
    #: Short identifier written into ``RiskReport.provider``.
    name: str = "base"

    @abstractmethod
    async def fetch_snapshot(self, chain: Chain, address: str, *, block: int | None = None) -> TokenSnapshot:
        """Return a validated snapshot or raise :class:`SnapshotNotFound`.

        ``block`` pins the observation to a historical block where the provider
        supports it (live RPC); offline providers may ignore it.
        """

    async def supports(self, chain: Chain) -> bool:
        """Whether this provider can serve the given chain. Defaults to True."""
        return True
