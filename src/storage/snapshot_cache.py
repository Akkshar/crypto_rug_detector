"""SQLite-backed store for token snapshots and the reports scored from them.

Why it exists:

* **Backtesting** – a labelled corpus is just cached snapshots plus a label.
* **Diffs over time** – ``rugscan history`` shows how a token's score and
  flags moved between scans, which is how an in-progress rug shows up.
* **Rate-limit relief** – ``CachingProvider`` short-circuits live fetches
  for tokens scanned within ``max_age``.

Pure stdlib ``sqlite3``; payloads are the models' own JSON so the schema never
has to chase Pydantic field changes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address

from src.ingestion.base import BaseDataProvider
from src.models.risk_report import Chain, RiskReport, TokenSnapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    chain        TEXT NOT NULL,
    address      TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    provider     TEXT NOT NULL,
    label        TEXT,                 -- optional ground truth: 'rug' | 'benign' | NULL
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (chain, address, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_token ON snapshots (chain, address, observed_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    chain         TEXT NOT NULL,
    address       TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    risk_score    REAL NOT NULL,
    risk_tier     TEXT NOT NULL,
    hard_fail     INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    PRIMARY KEY (chain, address, generated_at)
);
CREATE INDEX IF NOT EXISTS idx_reports_token ON reports (chain, address, generated_at DESC);
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReportDiff:
    previous_score: float
    current_score: float
    previous_tier: str
    current_tier: str
    added_flags: list[str] = field(default_factory=list)
    removed_flags: list[str] = field(default_factory=list)

    @property
    def score_delta(self) -> float:
        return round(self.current_score - self.previous_score, 1)

    @property
    def changed(self) -> bool:
        return bool(self.added_flags or self.removed_flags or self.score_delta)

    def summary(self) -> str:
        parts = [f"score {self.previous_score:.1f} → {self.current_score:.1f} ({self.score_delta:+.1f})"]
        if self.previous_tier != self.current_tier:
            parts.append(f"tier {self.previous_tier} → {self.current_tier}")
        if self.added_flags:
            parts.append("new: " + ", ".join(self.added_flags))
        if self.removed_flags:
            parts.append("cleared: " + ", ".join(self.removed_flags))
        return "; ".join(parts)


class SnapshotCache:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SnapshotCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #
    def put_snapshot(self, snapshot: TokenSnapshot, provider: str, label: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.chain.value,
                    snapshot.address,
                    _iso(snapshot.observed_at),
                    provider,
                    label,
                    snapshot.model_dump_json(),
                    _iso(datetime.now(timezone.utc)),
                ),
            )

    def get_latest(self, chain: Chain | str, address: str, max_age: timedelta | None = None) -> TokenSnapshot | None:
        row = self._conn.execute(
            "SELECT payload, observed_at FROM snapshots WHERE chain=? AND address=? ORDER BY observed_at DESC LIMIT 1",
            (Chain(chain).value, to_checksum_address(address)),
        ).fetchone()
        if row is None:
            return None
        if max_age is not None:
            observed = datetime.fromisoformat(row["observed_at"])
            if datetime.now(timezone.utc) - observed > max_age:
                return None
        return TokenSnapshot.model_validate_json(row["payload"])

    def snapshot_history(self, chain: Chain | str, address: str, limit: int = 20) -> list[TokenSnapshot]:
        rows = self._conn.execute(
            "SELECT payload FROM snapshots WHERE chain=? AND address=? ORDER BY observed_at DESC LIMIT ?",
            (Chain(chain).value, to_checksum_address(address), limit),
        ).fetchall()
        return [TokenSnapshot.model_validate_json(r["payload"]) for r in rows]

    def set_label(self, chain: Chain | str, address: str, label: str | None) -> int:
        """Attach ground truth to every snapshot of a token. Returns rows updated."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE snapshots SET label=? WHERE chain=? AND address=?",
                (label, Chain(chain).value, to_checksum_address(address)),
            )
        return cur.rowcount

    def iter_snapshots(
        self, chain: Chain | str | None = None, labelled_only: bool = False
    ) -> Iterator[tuple[TokenSnapshot, str, str | None]]:
        """Yield ``(snapshot, provider, label)`` for every stored snapshot."""
        sql = "SELECT payload, provider, label FROM snapshots"
        clauses, params = [], []
        if chain is not None:
            clauses.append("chain=?")
            params.append(Chain(chain).value)
        if labelled_only:
            clauses.append("label IS NOT NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY chain, address, observed_at"
        for row in self._conn.execute(sql, params):
            yield TokenSnapshot.model_validate_json(row["payload"]), row["provider"], row["label"]

    # ------------------------------------------------------------------ #
    # reports
    # ------------------------------------------------------------------ #
    def put_report(self, report: RiskReport) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    report.chain.value,
                    report.address,
                    _iso(report.generated_at),
                    report.risk_score,
                    report.risk_tier.value,
                    int(report.hard_fail),
                    report.to_json(indent=0),
                ),
            )

    def report_history(self, chain: Chain | str, address: str, limit: int = 20) -> list[RiskReport]:
        """Newest first."""
        rows = self._conn.execute(
            "SELECT payload FROM reports WHERE chain=? AND address=? ORDER BY generated_at DESC LIMIT ?",
            (Chain(chain).value, to_checksum_address(address), limit),
        ).fetchall()
        return [RiskReport.model_validate_json(r["payload"]) for r in rows]

    def recent_reports(self, limit: int = 50) -> list[RiskReport]:
        """All recent reports across tokens, newest first."""
        rows = self._conn.execute(
            "SELECT payload FROM reports ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [RiskReport.model_validate_json(r["payload"]) for r in rows]

    def latest_report(self, chain: Chain | str, address: str) -> RiskReport | None:
        history = self.report_history(chain, address, limit=1)
        return history[0] if history else None

    @staticmethod
    def diff(previous: RiskReport, current: RiskReport) -> ReportDiff:
        before = {f.code for f in previous.flags}
        after = {f.code for f in current.flags}
        return ReportDiff(
            previous_score=previous.risk_score,
            current_score=current.risk_score,
            previous_tier=previous.risk_tier.value,
            current_tier=current.risk_tier.value,
            added_flags=sorted(after - before),
            removed_flags=sorted(before - after),
        )

    # ------------------------------------------------------------------ #
    # bulk import / export (JSONL – one snapshot per line, for backtests)
    # ------------------------------------------------------------------ #
    def export_jsonl(self, path: Path | str, labelled_only: bool = False) -> int:
        n = 0
        with Path(path).open("w", encoding="utf-8") as fh:
            for snapshot, provider, label in self.iter_snapshots(labelled_only=labelled_only):
                fh.write(
                    json.dumps({"provider": provider, "label": label, "snapshot": snapshot.model_dump(mode="json")})
                )
                fh.write("\n")
                n += 1
        return n

    def import_jsonl(self, path: Path | str) -> int:
        n = 0
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                snapshot = TokenSnapshot.model_validate(record["snapshot"])
                self.put_snapshot(snapshot, record.get("provider", "import"), record.get("label"))
                n += 1
        return n

    def prior_rugs_by_deployer(self, deployer: str) -> tuple[int | None, list[str]]:
        """Tokens labelled 'rug' whose contract.deployer matches – feeds DeployerProfile.prior_rug_count."""
        rows = self._conn.execute(
            "SELECT DISTINCT address FROM snapshots WHERE label='rug' AND json_extract(payload, '$.contract.deployer')=?",
            (to_checksum_address(deployer),),
        ).fetchall()
        linked = [r["address"] for r in rows]
        has_labels = self._conn.execute("SELECT 1 FROM snapshots WHERE label IS NOT NULL LIMIT 1").fetchone()
        return (len(linked) if has_labels else None), linked

    def similar_contracts(
        self,
        code_hash: str | None,
        selectors: frozenset[str] | set[str],
        *,
        min_similarity: float = 0.5,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Labelled contracts whose bytecode resembles the given fingerprint.

        Returns dicts shaped like ``BytecodeMatch`` (chain, address, label, similarity, identical),
        best match first. Exact code-hash matches are reported as identical with similarity 1.0.
        """
        rows = self._conn.execute(
            "SELECT chain, address, label, json_extract(payload, '$.contract.code_hash') AS code_hash, "
            "json_extract(payload, '$.contract.selectors') AS selectors "
            "FROM snapshots WHERE label IS NOT NULL AND json_extract(payload, '$.contract.selectors') IS NOT NULL "
            "GROUP BY chain, address"
        ).fetchall()
        matches: list[dict[str, Any]] = []
        mine = set(selectors)
        for row in rows:
            theirs = set(json.loads(row["selectors"]) or [])
            identical = bool(code_hash) and row["code_hash"] == code_hash
            union = mine | theirs
            sim = 1.0 if identical else (len(mine & theirs) / len(union) if union else 0.0)
            if sim >= min_similarity:
                matches.append(
                    {
                        "chain": row["chain"],
                        "address": row["address"],
                        "label": row["label"],
                        "similarity": round(sim, 4),
                        "identical": identical,
                    }
                )
        matches.sort(key=lambda m: (-float(m["identical"]), -m["similarity"]))
        return matches[:limit]

    def stats(self) -> dict[str, int]:
        q = self._conn.execute
        return {
            "snapshots": q("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "tokens": q("SELECT COUNT(DISTINCT chain || address) FROM snapshots").fetchone()[0],
            "labelled": q("SELECT COUNT(*) FROM snapshots WHERE label IS NOT NULL").fetchone()[0],
            "reports": q("SELECT COUNT(*) FROM reports").fetchone()[0],
        }


class CachingProvider(BaseDataProvider):
    """Wrap any provider; serve from cache when a fresh-enough snapshot exists."""

    def __init__(self, inner: BaseDataProvider, cache: SnapshotCache, max_age: timedelta | None = None):
        self.inner = inner
        self.cache = cache
        self.max_age = max_age
        self.name = inner.name
        self.last_hit = False

    async def supports(self, chain: Chain) -> bool:
        return await self.inner.supports(chain)

    async def fetch_snapshot(self, chain: Chain, address: str, *, block: int | None = None) -> TokenSnapshot:
        if self.max_age is not None and block is None:  # historical reads are never served from "latest"
            cached = self.cache.get_latest(chain, address, self.max_age)
            if cached is not None:
                self.last_hit = True
                return cached
        self.last_hit = False
        snapshot = await self.inner.fetch_snapshot(chain, address, block=block)
        self.cache.put_snapshot(snapshot, self.inner.name)
        return snapshot
