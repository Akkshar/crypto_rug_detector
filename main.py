"""rugscan – CLI entrypoint.

python main.py analyze sample_honeypot
python main.py analyze 0x3333333333333333333333333333333333333333 --json
python main.py analyze 0xABC… --provider rpc --chain base   (needs RUGSCAN_BASE_RPC_URL)
python main.py analyze 0xABC… -p rpc --cache reports/rugscan.db --max-age 3600
python main.py history 0xABC… --cache reports/rugscan.db
python main.py list-fixtures
python main.py weights
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from config.settings import get_settings, load_risk_config
from src.ingestion.base import BaseDataProvider, ProviderError, SnapshotNotFound
from src.ingestion.mock_provider import MockDataProvider
from src.models.risk_report import Chain, RiskReport, RiskTier, TokenSnapshot
from src.models.scoring_engine import ScoringEngine
from src.storage import CachingProvider, SnapshotCache
from src.utils.formatting import render_report
from src.utils.logger import configure_logging

app = typer.Typer(
    name="rugscan",
    help="AI-assisted rug pull / honeypot / liquidity-drain risk scoring for EVM tokens.",
    no_args_is_help=True,
    add_completion=False,
)
out = Console()
err = Console(stderr=True)

TIER_ORDER = [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL]


class ProviderKind(StrEnum):
    MOCK = "mock"
    RPC = "rpc"


def _utf8_stdout() -> None:
    # Windows consoles default to cp1252; report text contains ≥ / × / → glyphs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8")


def _open_cache(cache: Path | None) -> SnapshotCache | None:
    path = cache or get_settings().cache_path
    return SnapshotCache(path) if path else None


def _build_snapshot(
    provider_kind: ProviderKind,
    target: str,
    chain: Chain | None,
    fixtures: Path | None,
    cache: SnapshotCache | None = None,
    max_age: timedelta | None = None,
) -> tuple[BaseDataProvider, TokenSnapshot]:
    settings = get_settings()
    provider: BaseDataProvider
    if provider_kind is ProviderKind.MOCK:
        mock = MockDataProvider(fixtures or settings.fixtures_dir)
        resolved = mock.resolve(target)  # raises SnapshotNotFound
        chain = chain or resolved.chain
        target = resolved.address  # fixture names → addresses so the cache key is stable
        provider = mock
    else:
        from src.ingestion.rpc_provider import RPCDataProvider

        provider = RPCDataProvider(settings)
        chain = chain or Chain.ETHEREUM
        if not asyncio.run(provider.supports(chain)):
            raise ProviderError(f"no RPC URL configured for {chain.value}; set RUGSCAN_{chain.value.upper()}_RPC_URL")
    if cache is not None:
        provider = CachingProvider(provider, cache, max_age)
    return provider, asyncio.run(provider.fetch_snapshot(chain, target))


@app.command()
def analyze(
    target: Annotated[str, typer.Argument(help="Token address, or a fixture name when --provider mock")],
    chain: Annotated[Chain | None, typer.Option("--chain", "-c", help="Chain (auto-detected for fixtures)")] = None,
    provider: Annotated[ProviderKind, typer.Option("--provider", "-p")] = ProviderKind.MOCK,
    fixtures: Annotated[Path | None, typer.Option(help="Fixture directory for the mock provider")] = None,
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml")] = None,
    json_out: Annotated[Path | None, typer.Option("--json-out", "-o", help="Write the JSON report here")] = None,
    json_only: Annotated[bool, typer.Option("--json", help="Print only the JSON report to stdout")] = False,
    fail_on: Annotated[RiskTier | None, typer.Option(help="Exit 1 if the tier is at or above this")] = None,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache for snapshots + report history")] = None,
    max_age: Annotated[int | None, typer.Option(help="Reuse a cached snapshot younger than this many seconds")] = None,
    log_level: Annotated[str, typer.Option(help="DEBUG / INFO / WARNING / ERROR")] = "INFO",
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress [FLAG] explanation logs")] = False,
) -> None:
    """Analyse one token and print its risk report."""
    _utf8_stdout()
    configure_logging("CRITICAL" if quiet else log_level, console=err)

    store = _open_cache(cache)
    try:
        config = load_risk_config(weights)
        data_provider, snapshot = _build_snapshot(
            provider, target, chain, fixtures, store, timedelta(seconds=max_age) if max_age else None
        )
    except (SnapshotNotFound, ProviderError, FileNotFoundError, ValueError) as exc:
        err.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    if isinstance(data_provider, CachingProvider) and data_provider.last_hit:
        err.print(f"[dim]snapshot served from cache (observed {snapshot.observed_at:%Y-%m-%d %H:%M} UTC)[/dim]")

    report: RiskReport = ScoringEngine(config).score(snapshot, provider=data_provider.name)

    if store is not None:
        previous = store.latest_report(report.chain, report.address)
        store.put_report(report)
        if previous is not None:
            diff = SnapshotCache.diff(previous, report)
            style = "yellow" if diff.changed else "dim"
            err.print(f"[{style}]vs previous scan ({previous.generated_at:%Y-%m-%d %H:%M}): {diff.summary()}[/{style}]")
        store.close()

    if json_out:
        json_out.write_text(report.to_json(), encoding="utf-8")
        err.print(f"[dim]JSON report written to {json_out}[/dim]")
    if json_only:
        print(report.to_json())
    else:
        render_report(report, console=out)

    if fail_on and TIER_ORDER.index(report.risk_tier) >= TIER_ORDER.index(fail_on):
        raise typer.Exit(code=1)


@app.command()
def history(
    address: Annotated[str, typer.Argument(help="Token address")],
    chain: Annotated[Chain, typer.Option("--chain", "-c")] = Chain.ETHEREUM,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache (or RUGSCAN_CACHE_PATH)")] = None,
    limit: Annotated[int, typer.Option(help="Most recent N scans")] = 20,
) -> None:
    """Show how a token's score and flags changed across cached scans."""
    _utf8_stdout()
    store = _open_cache(cache)
    if store is None:
        err.print("[bold red]error:[/bold red] no cache given; pass --cache or set RUGSCAN_CACHE_PATH")
        raise typer.Exit(code=2)
    try:
        reports = store.report_history(chain, address, limit)
    except ValueError as exc:
        err.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    finally:
        store.close()
    if not reports:
        err.print(f"no cached reports for {address} on {chain.value}")
        raise typer.Exit(code=1)

    table = Table(title=f"Scan history – {reports[0].token_symbol or address} on {chain.value}")
    table.add_column("Generated (UTC)")
    table.add_column("Score", justify="right")
    table.add_column("Tier")
    table.add_column("Flags", justify="right")
    table.add_column("Change vs previous")
    for newer, older in zip(reports, [*reports[1:], None], strict=True):
        change = SnapshotCache.diff(older, newer).summary() if older else "—"
        table.add_row(
            f"{newer.generated_at:%Y-%m-%d %H:%M}",
            f"{newer.risk_score:.1f}",
            newer.risk_tier.value,
            str(len(newer.flags)),
            change,
        )
    out.print(table)


@app.command("list-fixtures")
def list_fixtures(
    fixtures: Annotated[Path | None, typer.Option(help="Fixture directory")] = None,
) -> None:
    """Show the offline fixtures the mock provider can analyse."""
    provider = MockDataProvider(fixtures or get_settings().fixtures_dir)
    table = Table(title=f"Fixtures in {provider.fixtures_dir}")
    table.add_column("Name", style="bold")
    table.add_column("Chain")
    table.add_column("Address")
    for name, chain, address in provider.available:
        table.add_row(name, chain.value, address)
    out.print(table)


@app.command()
def weights(
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml")] = None,
) -> None:
    """Print the active flag weights and category caps."""
    config = load_risk_config(weights)
    caps = Table(title="Category caps")
    caps.add_column("Category", style="bold")
    caps.add_column("Cap", justify="right")
    for category, cap in config.category_caps.items():
        caps.add_row(category.value, f"{cap:g}")
    flags = Table(title="Flag weights")
    flags.add_column("Code", style="bold")
    flags.add_column("Points", justify="right")
    for code, pts in sorted(config.flags.items(), key=lambda kv: -kv[1]):
        flags.add_row(code, f"{pts:g}")
    out.print(caps, flags)
    out.print(
        f"rule weight {config.scoring.rule_weight:g} · ml weight {config.scoring.ml_weight:g} · "
        f"tiers MEDIUM≥{config.tiers.medium:g} HIGH≥{config.tiers.high:g} CRITICAL≥{config.tiers.critical:g}"
    )


if __name__ == "__main__":
    app()
