from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.api import create_app  # noqa: E402

BENIGN = "0x1111111111111111111111111111111111111111"
HONEYPOT = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(provider_kind="mock", cache_path=tmp_path / "api.db")
    with TestClient(app) as c:
        yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["provider"] == "mock" and body["cache"] is True
    assert set(body["chains"]) == {"ethereum", "bsc", "arbitrum", "base"}
    assert body["model"] and body["model_trained_on"] in {"synthetic", "labelled"}


def test_scan_fixture_by_name_and_address(client):
    r = client.post("/scan", json={"chain": "bsc", "address": "sample_honeypot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["risk_tier"] == "CRITICAL" and body["report"]["hard_fail"] is True
    assert body["from_cache"] is False and body["previous_score"] is None
    assert body["report"]["address"] == HONEYPOT

    again = client.post("/scan", json={"chain": "bsc", "address": HONEYPOT, "max_age_s": 999_999}).json()
    assert again["from_cache"] is True and again["previous_score"] == body["report"]["risk_score"]


def test_scan_errors(client):
    assert client.post("/scan", json={"chain": "bsc", "address": "nope"}).status_code == 404
    assert (
        client.post("/scan", json={"chain": "ethereum", "address": "sample_honeypot"}).status_code == 404
    )  # wrong chain
    assert client.post("/scan", json={"chain": "mars", "address": BENIGN}).status_code == 422


def test_history_label_stats_and_weights(client):
    client.post("/scan", json={"chain": "ethereum", "address": BENIGN})
    client.post("/scan", json={"chain": "ethereum", "address": BENIGN})
    hist = client.get(f"/history/ethereum/{BENIGN}", params={"limit": 5}).json()
    assert len(hist) == 2 and all(h["risk_tier"] == "LOW" for h in hist)
    assert client.get("/history/ethereum/0xnot-an-address").status_code == 400

    assert (
        client.post("/label", json={"chain": "ethereum", "address": BENIGN, "label": "benign"}).json()["updated"] >= 1
    )
    assert client.post("/label", json={"chain": "base", "address": BENIGN, "label": "rug"}).status_code == 404
    assert client.post("/label", json={"chain": "ethereum", "address": BENIGN, "label": "maybe"}).status_code == 422

    stats = client.get("/stats").json()
    assert stats["reports"] == 2 and stats["labelled"] >= 1

    weights = client.get("/weights").json()
    assert weights["flags"]["HONEYPOT_SELL_BLOCKED"] == 50 and weights["tiers"]["critical"] == 75
    assert "TRADING" in weights["category_caps"]


def test_app_without_cache(tmp_path: Path):
    with TestClient(create_app(provider_kind="mock", cache_path=None)) as c:
        assert c.get("/health").json()["cache"] is False
        assert c.get(f"/history/ethereum/{BENIGN}").status_code == 501
        assert c.get("/stats").json() == {"cache": False}
        assert c.post("/scan", json={"chain": "ethereum", "address": BENIGN}).json()["report"]["risk_tier"] == "LOW"


def test_serve_cli_help():
    from typer.testing import CliRunner

    from main import app

    assert "HTTP API" in CliRunner().invoke(app, ["serve", "--help"]).stdout
