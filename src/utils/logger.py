"""Logging setup plus the ``[FLAG]`` explanation lines the scoring engine emits."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

ROOT_LOGGER = "rugscan"

_SEVERITY_STYLE = {
    "INFO": "dim",
    "LOW": "cyan",
    "MEDIUM": "yellow",
    "HIGH": "dark_orange",
    "CRITICAL": "bold red",
}


def configure_logging(level: str = "INFO", console: Console | None = None) -> logging.Logger:
    """Attach a single Rich handler to the ``rugscan`` logger (idempotent)."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level.upper())
    if not any(isinstance(h, RichHandler) for h in logger.handlers):
        handler = RichHandler(
            console=console or Console(stderr=True),
            show_path=False,
            show_time=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")


def log_flag(logger: logging.Logger, severity: str, title: str, points: float) -> None:
    """Emit ``[FLAG] High Sell Tax Detected (99%): +40 Risk Points``."""
    style = _SEVERITY_STYLE.get(severity, "")
    logger.info("[%s]\\[FLAG][/%s] %s: [bold]+%g Risk Points[/bold]", style, style, title, points)


def log_note(logger: logging.Logger, message: str) -> None:
    logger.info("[dim]\\[NOTE][/dim] %s", message)
