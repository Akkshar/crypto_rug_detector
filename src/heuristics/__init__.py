from config.settings import RiskConfig
from src.heuristics.base import BaseHeuristic
from src.heuristics.contract_inspector import ContractInspector
from src.heuristics.deployer_tracer import DeployerTracer
from src.heuristics.holder_metrics import HolderMetrics
from src.heuristics.liquidity_analyzer import LiquidityAnalyzer

DEFAULT_HEURISTICS: tuple[type[BaseHeuristic], ...] = (
    ContractInspector,
    LiquidityAnalyzer,
    HolderMetrics,
    DeployerTracer,
)


def build_heuristics(config: RiskConfig) -> list[BaseHeuristic]:
    return [cls(config) for cls in DEFAULT_HEURISTICS]


__all__ = [
    "DEFAULT_HEURISTICS",
    "BaseHeuristic",
    "ContractInspector",
    "DeployerTracer",
    "HolderMetrics",
    "LiquidityAnalyzer",
    "build_heuristics",
]
