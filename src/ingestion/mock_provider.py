"""Offline provider backed by JSON fixtures in ``tests/fixtures``.

Lookups work by token address (any case) or by fixture stem, e.g. both
``0x1111…1111`` and ``sample_benign_token`` resolve to the benign fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from eth_utils import is_address, to_checksum_address

from src.ingestion.base import BaseDataProvider, SnapshotNotFound
from src.models.risk_report import Chain, TokenSnapshot


class MockDataProvider(BaseDataProvider):
    name = "mock"

    def __init__(self, fixtures_dir: Path | str):
        self.fixtures_dir = Path(fixtures_dir)
        if not self.fixtures_dir.is_dir():
            raise FileNotFoundError(f"fixtures directory not found: {self.fixtures_dir}")
        self._by_address: dict[str, TokenSnapshot] = {}
        self._by_name: dict[str, TokenSnapshot] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.fixtures_dir.glob("*.json")):
            snapshot = TokenSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
            self._by_address[snapshot.address] = snapshot
            self._by_name[path.stem] = snapshot

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> list[tuple[str, Chain, str]]:
        """``(fixture_name, chain, address)`` for every loaded fixture."""
        return [(name, s.chain, s.address) for name, s in self._by_name.items()]

    def resolve(self, key: str) -> TokenSnapshot:
        if key in self._by_name:
            return self._by_name[key]
        if is_address(key):
            snapshot = self._by_address.get(to_checksum_address(key))
            if snapshot is not None:
                return snapshot
        raise SnapshotNotFound(f"no fixture for {key!r}; available: {sorted(self._by_name)}")

    async def fetch_snapshot(self, chain: Chain, address: str) -> TokenSnapshot:
        snapshot = self.resolve(address)
        if snapshot.chain != chain:
            raise SnapshotNotFound(f"fixture for {address} is on {snapshot.chain}, not {chain}")
        # Return a copy so callers mutating the snapshot can't poison the cache.
        return snapshot.model_copy(deep=True)
