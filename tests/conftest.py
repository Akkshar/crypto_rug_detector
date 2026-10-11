from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import config.settings as settings_module
from config.settings import RiskConfig, load_risk_config
from src.ingestion.mock_provider import MockDataProvider
from src.models.risk_report import TokenSnapshot
from src.models.scoring_engine import ScoringEngine

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The developer's .env (real RPC keys, a cache path) must never leak into the suite: tests that assert
# "no RPC configured" would otherwise hit the network. Drop the env file and any RUGSCAN_* variables.
settings_module.Settings.model_config["env_file"] = None
for _name in [n for n in os.environ if n.startswith("RUGSCAN_")]:
    os.environ.pop(_name)
# Likewise, models trained on a real corpus are persisted to .rugscan/models and would replace the
# synthetic model the fixture assertions are written against – point the cache at a throwaway dir.
os.environ["RUGSCAN_MODEL_CACHE_DIR"] = tempfile.mkdtemp(prefix="rugscan-test-models-")
settings_module.get_settings.cache_clear()
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
