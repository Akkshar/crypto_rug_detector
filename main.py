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


def _make_provider(
    provider_kind: ProviderKind,
    target: str,
    chain: Chain | None,
    fixtures: Path | None,
    cache: SnapshotCache | None = None,
    max_age: timedelta | None = None,
) -> tuple[BaseDataProvider, Chain, str]:
    """Resolve the provider, chain and canonical target address without fetching."""
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

        # Labelled rugs in the cache feed DeployerProfile.prior_rug_count for live scans.
        provider = RPCDataProvider(
            settings,
            prior_rug_lookup=cache.prior_rugs_by_deployer if cache else None,
            similarity_lookup=cache.similar_contracts if cache else None,
        )
        chain = chain or Chain.ETHEREUM
        if not asyncio.run(provider.supports(chain)):
            raise ProviderError(f"no RPC URL configured for {chain.value}; set RUGSCAN_{chain.value.upper()}_RPC_URL")
    if cache is not None:
        provider = CachingProvider(provider, cache, max_age)
    return provider, chain, target


def _build_snapshot(
    provider_kind: ProviderKind,
    target: str,
    chain: Chain | None,
    fixtures: Path | None,
    cache: SnapshotCache | None = None,
    max_age: timedelta | None = None,
    block: int | None = None,
) -> tuple[BaseDataProvider, TokenSnapshot]:
    provider, chain, target = _make_provider(provider_kind, target, chain, fixtures, cache, max_age)
    return provider, asyncio.run(provider.fetch_snapshot(chain, target, block=block))


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
    at_block: Annotated[
        int | None,
        typer.Option("--at-block", help="Observe the token as of this block (live provider; needs archive node)"),
    ] = None,
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
            provider, target, chain, fixtures, store, timedelta(seconds=max_age) if max_age else None, at_block
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
def watch(
    target: Annotated[str, typer.Argument(help="Token address, or a fixture name when --provider mock")],
    chain: Annotated[Chain | None, typer.Option("--chain", "-c")] = None,
    provider: Annotated[ProviderKind, typer.Option("--provider", "-p")] = ProviderKind.MOCK,
    fixtures: Annotated[Path | None, typer.Option(help="Fixture directory for the mock provider")] = None,
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml")] = None,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache; keeps history across restarts")] = None,
    interval: Annotated[float, typer.Option(help="Seconds between checks")] = 60.0,
    iterations: Annotated[int | None, typer.Option(help="Stop after N checks (default: run until Ctrl-C)")] = None,
    webhook: Annotated[
        str | None, typer.Option(help="Discord/Slack-style webhook URL (or RUGSCAN_WEBHOOK_URL)")
    ] = None,
    liquidity_drop: Annotated[float, typer.Option(help="Alert when liquidity falls by this many percent")] = 30.0,
    log_level: Annotated[str, typer.Option(help="DEBUG / INFO / WARNING / ERROR")] = "WARNING",
) -> None:
    """Re-score a token on an interval and alert on escalation, liquidity drops or LP leaving lockers."""
    from src.watch import ConsoleNotifier, TokenWatcher, WebhookNotifier

    _utf8_stdout()
    configure_logging(log_level, console=err)
    store = _open_cache(cache)
    try:
        config = load_risk_config(weights)
        data_provider, resolved_chain, address = _make_provider(provider, target, chain, fixtures, store, None)
    except (SnapshotNotFound, ProviderError, FileNotFoundError, ValueError) as exc:
        err.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    notifiers: list = [ConsoleNotifier(err)]
    settings = get_settings()
    hook = webhook or (settings.webhook_url.get_secret_value() if settings.webhook_url else None)
    if hook:
        notifiers.append(WebhookNotifier(hook))

    watcher = TokenWatcher(
        data_provider, ScoringEngine(config), notifiers, cache=store, liquidity_drop_pct=liquidity_drop
    )
    err.print(f"[dim]watching {address} on {resolved_chain.value} every {interval:g}s – Ctrl-C to stop[/dim]")
    try:
        n = asyncio.run(watcher.run(resolved_chain, address, interval_s=interval, max_iterations=iterations))
    except KeyboardInterrupt:
        n = -1
    finally:
        if store is not None:
            store.close()
    if n >= 0:
        err.print(f"[dim]{n} check(s) completed[/dim]")


@app.command()
def label(
    address: Annotated[str, typer.Argument(help="Token address")],
    verdict: Annotated[str, typer.Argument(help="rug | benign | clear")],
    chain: Annotated[Chain, typer.Option("--chain", "-c")] = Chain.ETHEREUM,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache (or RUGSCAN_CACHE_PATH)")] = None,
) -> None:
    """Attach ground truth to a cached token so it feeds backtests and deployer prior-rug counts."""
    from src.backtest.harness import parse_label

    store = _open_cache(cache)
    if store is None:
        err.print("[bold red]error:[/bold red] no cache given; pass --cache or set RUGSCAN_CACHE_PATH")
        raise typer.Exit(code=2)
    value = None if verdict.lower() == "clear" else verdict.lower()
    if value is not None and parse_label(value) is None:
        err.print(f"[bold red]error:[/bold red] unknown verdict {verdict!r}; use rug, benign or clear")
        raise typer.Exit(code=2)
    try:
        updated = store.set_label(chain, address, value)
    finally:
        store.close()
    if not updated:
        err.print(f"no cached snapshots for {address} on {chain.value} – analyze it with --cache first")
        raise typer.Exit(code=1)
    out.print(f"labelled {updated} snapshot(s) of {address} as {value or 'unlabelled'}")


@app.command()
def backtest(
    cache: Annotated[Path | None, typer.Option(help="SQLite cache with labelled snapshots")] = None,
    jsonl: Annotated[Path | None, typer.Option(help="JSONL export (see export-snapshots) with labels")] = None,
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml to evaluate")] = None,
    positive_tier: Annotated[
        RiskTier, typer.Option(help="Tier at/above which a token counts as flagged")
    ] = RiskTier.HIGH,
    json_out: Annotated[Path | None, typer.Option("--json-out", "-o", help="Write metrics JSON here")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = True,
) -> None:
    """Score every labelled snapshot and report AUC, precision/recall and per-flag lift."""
    import json

    from src.backtest import load_labelled, render_backtest, run_backtest

    _utf8_stdout()
    configure_logging("CRITICAL" if quiet else "INFO", console=err)
    store = _open_cache(cache)
    try:
        items = load_labelled(cache=store, jsonl=jsonl)
    finally:
        if store is not None:
            store.close()
    if not items:
        err.print("[bold red]error:[/bold red] no labelled snapshots found; use `label` or pass --jsonl")
        raise typer.Exit(code=2)
    result = run_backtest(ScoringEngine(load_risk_config(weights)), items, positive_tier=positive_tier)
    render_backtest(result, console=out)
    if json_out:
        json_out.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        err.print(f"[dim]metrics written to {json_out}[/dim]")


@app.command("export-snapshots")
def export_snapshots(
    output: Annotated[Path, typer.Argument(help="Destination .jsonl")],
    cache: Annotated[Path | None, typer.Option(help="SQLite cache (or RUGSCAN_CACHE_PATH)")] = None,
    labelled_only: Annotated[bool, typer.Option(help="Only snapshots with a label")] = False,
) -> None:
    """Dump cached snapshots (with labels) to JSONL for sharing or offline backtests."""
    store = _open_cache(cache)
    if store is None:
        err.print("[bold red]error:[/bold red] no cache given; pass --cache or set RUGSCAN_CACHE_PATH")
        raise typer.Exit(code=2)
    try:
        n = store.export_jsonl(output, labelled_only=labelled_only)
    finally:
        store.close()
    out.print(f"wrote {n} snapshot(s) to {output}")


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
