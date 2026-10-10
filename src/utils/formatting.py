"""Rich terminal rendering for a ``RiskReport``."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.models.risk_report import RiskReport, RiskTier, Severity

TIER_STYLE = {
    RiskTier.LOW: "bold green",
    RiskTier.MEDIUM: "bold yellow",
    RiskTier.HIGH: "bold dark_orange",
    RiskTier.CRITICAL: "bold white on red",
}

SEVERITY_STYLE = {
    Severity.INFO: "dim",
    Severity.LOW: "cyan",
    Severity.MEDIUM: "yellow",
    Severity.HIGH: "dark_orange",
    Severity.CRITICAL: "bold red",
}


def _header(report: RiskReport) -> Panel:
    label = f"{report.token_name or '?'} ({report.token_symbol or '?'})"
    title = Text.assemble(
        (f"{report.risk_tier.value}  ", TIER_STYLE[report.risk_tier]),
        (f"{report.risk_score:.1f}/100", "bold"),
    )
    body = Table.grid(padding=(0, 2))
    body.add_row("Token", label)
    body.add_row("Address", f"{report.address}  [{report.chain.value}]")
    body.add_row("Provider", report.provider)
    body.add_row("Rule score", f"{report.rule_score:.1f}")
    if report.ml:
        suffix = "" if report.ml.reliable else "  (down-weighted: unreliable)"
        body.add_row("ML rug prob.", f"{report.ml.rug_probability:.2f}{suffix}")
        drivers = report.ml.top_drivers(3)
        if drivers:
            body.add_row("ML drivers", "  ".join(f"{name} {delta:+.2f}" for name, delta in drivers))
    if report.hard_fail:
        body.add_row("Pre-filter", Text("HARD FAIL — " + "; ".join(report.hard_fail_reasons), style="bold red"))
    return Panel(body, title=title, border_style=TIER_STYLE[report.risk_tier].split(" on ")[0])


def _breakdown(report: RiskReport) -> Table:
    table = Table(title="Category breakdown", expand=False)
    table.add_column("Category")
    table.add_column("Flags", justify="right")
    table.add_column("Raw", justify="right")
    table.add_column("Capped", justify="right")
    table.add_column("Cap", justify="right")
    for row in report.breakdown:
        table.add_row(
            row.category.value,
            str(row.flag_count),
            f"{row.raw_points:.0f}",
            f"{row.capped_points:.0f}",
            f"{row.cap:.0f}",
        )
    return table


def _flags(report: RiskReport) -> Table:
    table = Table(title="Risk flags", expand=True)
    table.add_column("Severity", width=9)
    table.add_column("Code", style="bold")
    table.add_column("Points", justify="right", width=6)
    table.add_column("Detail", ratio=1)
    for flag in sorted(report.flags, key=lambda f: -f.points):
        table.add_row(
            Text(flag.severity.value, style=SEVERITY_STYLE[flag.severity]),
            flag.code,
            f"+{flag.points:g}",
            flag.detail,
        )
    if not report.flags:
        table.add_row("", "—", "", "No risk flags raised.")
    return table


def render_report(report: RiskReport, console: Console | None = None) -> None:
    console = console or Console()
    parts: list = [_header(report), _breakdown(report), _flags(report)]
    if report.missing_data:
        parts.append(Text("Missing data: " + ", ".join(report.missing_data), style="dim italic"))
    console.print(Group(*parts))
