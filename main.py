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


@app.command()
def train(
    cache: Annotated[Path | None, typer.Option(help="SQLite cache with labelled snapshots")] = None,
    jsonl: Annotated[Path | None, typer.Option(help="JSONL export with labels")] = None,
    weights: Annotated[Path | None, typer.Option(help="risk_weights.yaml (ML section)")] = None,
    calibrate: Annotated[str, typer.Option(help="sigmoid | isotonic | none")] = "sigmoid",
    folds: Annotated[int, typer.Option(help="Cross-validation folds")] = 5,
    algorithm: Annotated[str | None, typer.Option(help="gbm | random_forest | logistic (default from YAML)")] = None,
    compare: Annotated[
        bool, typer.Option(help="Cross-validate all three classifier families side by side first")
    ] = False,
    model_dir: Annotated[Path | None, typer.Option(help="Where to persist (default RUGSCAN_MODEL_CACHE_DIR)")] = None,
    json_out: Annotated[Path | None, typer.Option("--json-out", "-o")] = None,
) -> None:
    """Train the ML layer on labelled snapshots (replaces the synthetic prior for this configuration)."""
    import json

    from src.backtest import compare_models, load_labelled, train_model

    _utf8_stdout()
    configure_logging("WARNING", console=err)
    store = _open_cache(cache)
    try:
        items = load_labelled(cache=store, jsonl=jsonl)
    finally:
        if store is not None:
            store.close()
    config = load_risk_config(weights)
    target = model_dir or get_settings().model_cache_dir
    cal = None if calibrate.lower() == "none" else calibrate
    comparison = None
    try:
        if compare:
            comparison = compare_models(items, config, folds=folds, calibrate=cal)
        model, metrics = train_model(items, config, calibrate=cal, folds=folds, persist_dir=target, algorithm=algorithm)
    except ValueError as exc:
        err.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    if comparison:
        cmp = Table(title="Classifier comparison (same folds)")
        for col, just in (
            ("Algorithm", "left"),
            ("ROC-AUC", "right"),
            ("PR-AUC", "right"),
            ("F1@0.5", "right"),
            ("FN", "right"),
            ("Brier", "right"),
        ):
            cmp.add_column(col, justify=just)  # type: ignore[arg-type]
        for m in comparison:
            cmp.add_row(
                m.algorithm,
                f"{m.auc_mean:.3f}" if m.auc_mean is not None else "n/a",
                f"{m.ap_mean:.3f}" if m.ap_mean is not None else "n/a",
                f"{m.f1:.3f}",
                str(m.false_negatives),
                f"{m.brier:.4f}",
            )
        out.print(cmp)

    table = Table(
        title=f"{metrics.algorithm}: cross-validated on {metrics.n} snapshots ({metrics.n_rug} rugs), {metrics.folds} folds"
    )
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row(
        "ROC-AUC", f"{metrics.auc_mean:.3f} ± {metrics.auc_std:.3f}" if metrics.auc_mean is not None else "n/a"
    )
    table.add_row("PR-AUC", f"{metrics.ap_mean:.3f}" if metrics.ap_mean is not None else "n/a")
    table.add_row("Precision / Recall / F1 @0.5", f"{metrics.precision:.3f} / {metrics.recall:.3f} / {metrics.f1:.3f}")
    table.add_row("False negatives @0.5", f"{metrics.false_negatives} of {metrics.n_rug} rugs")
    table.add_row("Accuracy @0.5", f"{metrics.accuracy:.3f}")
    table.add_row("Brier score", f"{metrics.brier:.4f}  (0.25 = coin flip)")
    table.add_row("Rows with imputed features", str(metrics.imputed_rows))
    table.add_row("Calibration", model.calibration or "none")
    table.add_row("Feature importances", "  ".join(f"{k} {v:.2f}" for k, v in model.feature_importances().items()))
    out.print(table)
    if target:
        out.print(f"[dim]model persisted to {target} – `analyze` will now prefer it for this configuration[/dim]")
    if json_out:
        payload: dict[str, object] = {"selected": metrics.as_dict()}
        if comparison:
            payload["comparison"] = [m.as_dict() for m in comparison]
        json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@app.command()
def tune(
    cache: Annotated[Path | None, typer.Option(help="SQLite cache with labelled snapshots")] = None,
    jsonl: Annotated[Path | None, typer.Option(help="JSONL export with labels")] = None,
    weights: Annotated[Path | None, typer.Option(help="Starting risk_weights.yaml")] = None,
    positive_tier: Annotated[
        RiskTier, typer.Option(help="Tier at/above which a token counts as flagged")
    ] = RiskTier.HIGH,
    max_fp_rate: Annotated[
        float | None, typer.Option(help="Reject proposals whose false-positive rate exceeds this")
    ] = None,
    passes: Annotated[int, typer.Option(help="Coordinate-descent passes")] = 2,
    output: Annotated[Path, typer.Option("--output", "-o", help="Where to write the proposed YAML")] = Path(
        "risk_weights.tuned.yaml"
    ),
) -> None:
    """Search flag weights that maximise F1 on the labelled corpus and write a proposed YAML."""
    from config.settings import DEFAULT_WEIGHTS_PATH
    from src.backtest import load_labelled, tune_weights, write_proposal

    _utf8_stdout()
    configure_logging("WARNING", console=err)
    store = _open_cache(cache)
    try:
        items = load_labelled(cache=store, jsonl=jsonl)
    finally:
        if store is not None:
            store.close()
    if not items:
        err.print("[bold red]error:[/bold red] no labelled snapshots found; use `label` or pass --jsonl")
        raise typer.Exit(code=2)
    config = load_risk_config(weights)
    result = tune_weights(config, items, positive_tier=positive_tier, max_fp_rate=max_fp_rate, passes=passes)

    table = Table(
        title=f"Tuning on {len(items)} snapshots – F1 {result.baseline_objective:.3f} → {result.best_objective:.3f}"
    )
    table.add_column("Flag", style="bold")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    for code, (before, after) in result.changed.items():
        table.add_row(code, f"{before:g}", f"{after:g}")
    if not result.changed:
        table.add_row("—", "", "no change improved the objective")
    out.print(table)
    path = write_proposal(result, weights or DEFAULT_WEIGHTS_PATH, output)
    out.print(f"proposed weights written to {path}  (try: backtest --weights {path})")


@app.command("import-corpus")
def import_corpus(
    csv_path: Annotated[Path, typer.Argument(help="CSV with columns chain,address,label[,block]")],
    provider: Annotated[ProviderKind, typer.Option("--provider", "-p")] = ProviderKind.MOCK,
    fixtures: Annotated[Path | None, typer.Option(help="Fixture directory for the mock provider")] = None,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache to fill (or RUGSCAN_CACHE_PATH)")] = None,
    at_launch: Annotated[bool, typer.Option(help="Snapshot each token shortly after creation (live provider)")] = False,
    launch_offset: Annotated[int, typer.Option(help="Blocks after creation when --at-launch")] = 1_000,
    resume: Annotated[bool, typer.Option(help="Skip rows that already have a snapshot in the cache")] = False,
    only_chain: Annotated[Chain | None, typer.Option(help="Import only rows for this chain")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = True,
) -> None:
    """Fetch, label and cache every token in a CSV – the raw material for `backtest`, `train` and `tune`."""
    import csv

    from src.backtest.harness import parse_label
    from src.ingestion.rpc_provider import RPCDataProvider

    _utf8_stdout()
    configure_logging("CRITICAL" if quiet else "INFO", console=err)
    store = _open_cache(cache)
    if store is None:
        err.print("[bold red]error:[/bold red] no cache given; pass --cache or set RUGSCAN_CACHE_PATH")
        raise typer.Exit(code=2)

    ok = failed = skipped = 0
    try:
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                address = (row.get("address") or "").strip()
                label = (row.get("label") or "").strip()
                chain_name = (row.get("chain") or "ethereum").strip().lower()
                if only_chain is not None and chain_name != only_chain.value:
                    continue
                if parse_label(label) is None:
                    err.print(f"[yellow]skip[/yellow] {address}: unknown label {label!r}")
                    failed += 1
                    continue
                try:
                    chain = Chain(chain_name)
                    if resume and store.get_latest(chain, address) is not None:
                        skipped += 1
                        continue
                    data_provider, resolved_chain, target = _make_provider(
                        provider, address, chain, fixtures, store, None
                    )
                    block = int(row["block"]) if row.get("block") else None
                    if block is None and at_launch:
                        inner = data_provider.inner if isinstance(data_provider, CachingProvider) else data_provider
                        if isinstance(inner, RPCDataProvider):
                            created = inner.creation_block(resolved_chain, target)
                            block = created + launch_offset if created is not None else None
                    snapshot = asyncio.run(data_provider.fetch_snapshot(resolved_chain, target, block=block))
                    store.put_snapshot(snapshot, data_provider.name, label=label.lower())
                    ok += 1
                    err.print(
                        f"[dim]{snapshot.address} on {resolved_chain.value} → {label}"
                        + (f" @ block {block}" if block else "")
                        + "[/dim]"
                    )
                except Exception as exc:  # noqa: BLE001 - keep importing the rest of the file
                    err.print(f"[yellow]failed[/yellow] {address}: {exc}")
                    failed += 1
    finally:
        store.close()
    out.print(f"imported {ok} snapshot(s), {failed} failed" + (f", {skipped} already cached" if skipped else ""))
    if ok == 0 and skipped == 0:
        raise typer.Exit(code=1)


@app.command()
def discover(
    chain: Annotated[Chain, typer.Option("--chain", "-c")] = Chain.ETHEREUM,
    from_block: Annotated[int | None, typer.Option(help="Start block (default: head − lookback)")] = None,
    lookback: Annotated[int, typer.Option(help="Blocks before head to scan when --from-block is not given")] = 2_000,
    follow: Annotated[bool, typer.Option(help="Keep polling for new listings until Ctrl-C")] = False,
    poll: Annotated[float, typer.Option(help="Seconds between polls when following")] = 15.0,
    max_rounds: Annotated[int | None, typer.Option(help="Stop after N polling rounds")] = None,
    min_tier: Annotated[RiskTier, typer.Option(help="Alert only at/above this tier")] = RiskTier.HIGH,
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml")] = None,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache – every scanned token is stored")] = None,
    webhook: Annotated[
        str | None, typer.Option(help="Discord/Slack-style webhook URL (or RUGSCAN_WEBHOOK_URL)")
    ] = None,
    replay_lookback: Annotated[
        int, typer.Option(help="Blocks of Transfer/Swap history to replay per token (new listings need little)")
    ] = 20_000,
    max_tokens: Annotated[int, typer.Option(help="Score at most this many new tokens per round (oldest first)")] = 50,
    log_level: Annotated[str, typer.Option(help="DEBUG / INFO / WARNING / ERROR")] = "WARNING",
) -> None:
    """Scan DEX factories for newly created pairs and score every new token (live provider only)."""
    from config.chains import load_chain_registry
    from src.ingestion.rpc_provider import RPCDataProvider
    from src.watch import ConsoleNotifier, WebhookNotifier
    from src.watch.discover import Firehose, NewPairScanner

    _utf8_stdout()
    configure_logging(log_level, console=err)
    settings = get_settings()
    store = _open_cache(cache)
    provider = RPCDataProvider(
        settings,
        prior_rug_lookup=store.prior_rugs_by_deployer if store else None,
        similarity_lookup=store.similar_contracts if store else None,
        replay_lookback=replay_lookback,
        swap_window_blocks=replay_lookback,
    )
    if not asyncio.run(provider.supports(chain)):
        err.print(
            f"[bold red]error:[/bold red] no RPC URL configured for {chain.value}; set RUGSCAN_{chain.value.upper()}_RPC_URL"
        )
        raise typer.Exit(code=2)

    notifiers: list = [ConsoleNotifier(err)]
    hook = webhook or (settings.webhook_url.get_secret_value() if settings.webhook_url else None)
    if hook:
        notifiers.append(WebhookNotifier(hook))
    inner: BaseDataProvider = CachingProvider(provider, store, None) if store else provider
    client = provider.client_for(chain)
    scanner = NewPairScanner(client, chain, load_chain_registry().get(chain))
    hose = Firehose(
        inner,
        ScoringEngine(load_risk_config(weights)),
        scanner,
        notifiers,
        cache=store,
        min_tier=min_tier,
        max_tokens_per_round=max_tokens,
    )
    err.print(
        f"[dim]discovering new pairs on {chain.value} ({'following' if follow else 'one pass'}) – Ctrl-C to stop[/dim]"
    )
    try:
        rounds = asyncio.run(
            hose.run(
                client, from_block=from_block, lookback=lookback, follow=follow, poll_s=poll, max_rounds=max_rounds
            )
        )
    except KeyboardInterrupt:
        rounds = -1
    finally:
        if store is not None:
            store.close()
    if rounds >= 0:
        err.print(f"[dim]{rounds} round(s), {len(hose.reports)} token(s) scored[/dim]")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8000,
    provider: Annotated[ProviderKind, typer.Option("--provider", "-p")] = ProviderKind.RPC,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache (or RUGSCAN_CACHE_PATH)")] = None,
    weights: Annotated[Path | None, typer.Option(help="Alternative risk_weights.yaml")] = None,
    fixtures: Annotated[Path | None, typer.Option(help="Fixture directory for the mock provider")] = None,
) -> None:
    """Run the HTTP API (needs the `api` extra: pip install .[api])."""
    try:
        import uvicorn

        from src.api import create_app
    except ImportError as exc:
        err.print(f"[bold red]error:[/bold red] {exc}; install with `pip install .[api]`")
        raise typer.Exit(code=2) from exc

    _utf8_stdout()
    configure_logging("INFO", console=err)
    application = create_app(provider_kind=provider.value, cache_path=cache, weights=weights, fixtures_dir=fixtures)
    err.print(f"[dim]rugscan API on http://{host}:{port}  (docs at /docs) – provider {provider.value}[/dim]")
    uvicorn.run(application, host=host, port=port, log_level="info")


@app.command("label-from-goplus")
def label_from_goplus(
    chain: Annotated[Chain, typer.Option("--chain", "-c")] = Chain.ETHEREUM,
    cache: Annotated[Path | None, typer.Option(help="SQLite cache whose unlabelled tokens get weak labels")] = None,
    addresses: Annotated[Path | None, typer.Option(help="Text/CSV file of addresses instead of the cache")] = None,
    benign_if_clean: Annotated[
        bool, typer.Option(help="Also label clean tokens benign (open source, LP locked, low tax)")
    ] = True,
    overwrite: Annotated[bool, typer.Option(help="Replace existing labels")] = False,
    csv_out: Annotated[
        Path | None, typer.Option("--csv-out", "-o", help="Write a chain,address,label CSV for import-corpus")
    ] = None,
) -> None:
    """Weak-label tokens with GoPlus token-security verdicts (free API, no key). See `sources.py` for caveats."""
    import csv

    from src.backtest.sources import weak_labels

    _utf8_stdout()
    store = _open_cache(cache)
    targets: list[str] = []
    if addresses is not None:
        for line in addresses.read_text(encoding="utf-8").splitlines():
            cell = line.split(",")[0].strip()
            if cell.startswith("0x") and len(cell) == 42:
                targets.append(cell)
    elif store is not None:
        targets = sorted({s.address for s, _, lbl in store.iter_snapshots(chain=chain) if overwrite or lbl is None})
    if not targets:
        err.print(
            "[bold red]error:[/bold red] nothing to label; pass --addresses or a --cache with snapshots on that chain"
        )
        raise typer.Exit(code=2)

    results = weak_labels(chain, targets, benign_if_clean=benign_if_clean)
    counts = {"rug": 0, "benign": 0, "undecided": 0}
    rows: list[tuple[str, str, str]] = []
    try:
        for r in results:
            if r.label is None:
                counts["undecided"] += 1
                err.print(f"[dim]{r.address}: undecided ({', '.join(r.reasons) or 'no signal'})[/dim]")
                continue
            counts[r.label] += 1
            rows.append((chain.value, r.address, r.label))
            if store is not None:
                store.set_label(chain, r.address, r.label)
            err.print(f"{r.address}: [bold]{r.label}[/bold] – {', '.join(r.reasons)}")
    finally:
        if store is not None:
            store.close()
    if csv_out:
        with csv_out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["chain", "address", "label"])
            w.writerows(rows)
        err.print(f"[dim]{len(rows)} labelled row(s) written to {csv_out}[/dim]")
    out.print(
        f"GoPlus weak labels on {chain.value}: {counts['rug']} rug, {counts['benign']} benign, {counts['undecided']} undecided"
    )
    out.print("[dim]Weak labels partly overlap this project's own rules – report them as such in any evaluation.[/dim]")


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
