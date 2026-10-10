"""Base class every heuristic engine derives from.

A heuristic is a pure function ``TokenSnapshot -> HeuristicResult``. It never
performs I/O, never mutates the snapshot, and only raises flags whose codes
exist in ``risk_weights.yaml`` (the points come from there, not from code).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from config.settings import RiskConfig, Thresholds
from src.models.risk_report import (
    HeuristicResult,
    RiskCategory,
    RiskFlag,
    Severity,
    TokenSnapshot,
)

# Float comparisons against YAML thresholds; 0.12 / 0.6 is 0.19999… in IEEE-754.
_EPS = 1e-9


def ge(value: float, threshold: float) -> bool:
    return value >= threshold - _EPS


def gt(value: float, threshold: float) -> bool:
    return value > threshold + _EPS


def lt(value: float, threshold: float) -> bool:
    return value < threshold - _EPS


class BaseHeuristic(ABC):
    name: ClassVar[str]
    category: ClassVar[RiskCategory]

    def __init__(self, config: RiskConfig):
        self.config = config
        self.t: Thresholds = config.thresholds

    @abstractmethod
    def evaluate(self, snapshot: TokenSnapshot) -> HeuristicResult:
        """Inspect the snapshot and return every flag this engine can justify."""

    # ------------------------------------------------------------------ #
    # helpers for subclasses
    # ------------------------------------------------------------------ #
    def _flag(
        self,
        code: str,
        severity: Severity,
        title: str,
        detail: str,
        *,
        category: RiskCategory | None = None,
        evidence: dict[str, object] | None = None,
        **extra: object,
    ) -> RiskFlag:
        return RiskFlag(
            code=code,
            category=category or self.category,
            severity=severity,
            title=title,
            detail=detail,
            points=self.config.points(code),
            evidence={**(evidence or {}), **extra},
        )

    def _result(
        self,
        flags: list[RiskFlag],
        *,
        hard_fail: bool = False,
        hard_fail_reason: str | None = None,
        data_available: bool = True,
        notes: list[str] | None = None,
    ) -> HeuristicResult:
        return HeuristicResult(
            heuristic=self.name,
            category=self.category,
            flags=flags,
            hard_fail=hard_fail,
            hard_fail_reason=hard_fail_reason,
            data_available=data_available,
            notes=notes or [],
        )
