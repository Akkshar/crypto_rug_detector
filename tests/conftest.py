from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import RiskConfig, load_risk_config
from src.ingestion.mock_provider import MockDataProvider
from src.models.risk_report import TokenSnapshot
from src.models.scoring_engine import ScoringEngine

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = ("sample_benign_token", "sample_honeypot", "sample_unlocked_rug")


@pytest.fixture(scope="session")
def config() -> RiskConfig:
    return load_risk_config()


@pytest.fixture(scope="session")
def provider() -> MockDataProvider:
    return MockDataProvider(FIXTURES_DIR)


@pytest.fixture(scope="session")
def engine(config: RiskConfig) -> ScoringEngine:
    # Trains the synthetic ML model once for the whole session.
    return ScoringEngine(config)


@pytest.fixture
def benign(provider: MockDataProvider) -> TokenSnapshot:
    return provider.resolve("sample_benign_token").model_copy(deep=True)


@pytest.fixture
def honeypot(provider: MockDataProvider) -> TokenSnapshot:
    return provider.resolve("sample_honeypot").model_copy(deep=True)


@pytest.fixture
def rug(provider: MockDataProvider) -> TokenSnapshot:
    return provider.resolve("sample_unlocked_rug").model_copy(deep=True)
