"""HTTP surface for the detector (FastAPI).

    POST /scan                      score a token (live or fixture provider)
    GET  /history/{chain}/{address} cached reports, newest first
    POST /label                     attach ground truth to a cached token
    GET  /weights                   active flag weights / caps / tiers
    GET  /stats                     cache statistics
    GET  /health

The app is a thin layer over the same objects the CLI uses: one
``ScoringEngine`` (model trained once at start-up), one provider, one cache.
Blocking RPC work already runs in a thread inside the provider, so the event
loop stays free.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from config.settings import Settings, get_settings, load_risk_config
from src import __version__
from src.ingestion.base import BaseDataProvider, ProviderError, SnapshotNotFound
from src.ingestion.mock_provider import MockDataProvider
from src.models.risk_report import Chain, RiskReport
from src.models.scoring_engine import ScoringEngine
from src.storage.snapshot_cache import CachingProvider, SnapshotCache


class ScanRequest(BaseModel):
    chain: Chain
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$|^[A-Za-z0-9_]+$", description="Token address (or fixture name)")
    block: int | None = Field(None, ge=0, description="Observe as of this block (live provider only)")
    max_age_s: int | None = Field(None, ge=0, description="Reuse a cached snapshot younger than this")


class LabelRequest(BaseModel):
    chain: Chain
    address: str
    label: Literal["rug", "benign"] | None = Field(description="null clears the label")


class ScanResponse(BaseModel):
    report: RiskReport
    from_cache: bool
    previous_score: float | None = None


def create_app(
    *,
    settings: Settings | None = None,
    provider_kind: Literal["rpc", "mock"] = "rpc",
    cache_path: Path | str | None = None,
    weights: Path | None = None,
    fixtures_dir: Path | None = None,
):
    from fastapi import FastAPI, HTTPException

    settings = settings or get_settings()
    config = load_risk_config(weights)
    engine = ScoringEngine(config)
    store_path = cache_path or settings.cache_path
    cache = SnapshotCache(store_path) if store_path else None

    provider: BaseDataProvider
    if provider_kind == "mock":
        provider = MockDataProvider(fixtures_dir or settings.fixtures_dir)
    else:
        from src.ingestion.rpc_provider import RPCDataProvider

        provider = RPCDataProvider(
            settings,
            prior_rug_lookup=cache.prior_rugs_by_deployer if cache else None,
            similarity_lookup=cache.similar_contracts if cache else None,
        )

    app = FastAPI(title="rugscan", version=__version__, description="Rug pull / honeypot risk scoring for EVM tokens")
    app.state.engine, app.state.provider, app.state.cache = engine, provider, cache

    @app.get("/health")
    async def health() -> dict[str, Any]:
        supported = [c.value for c in Chain if await provider.supports(c)]
        return {
            "status": "ok",
            "version": __version__,
            "provider": provider.name,
            "chains": supported,
            "model": engine.model.name if engine.model else None,
            "model_trained_on": getattr(engine.model, "trained_on", None),
            "cache": cache is not None,
        }

    @app.post("/scan", response_model=ScanResponse)
    async def scan(req: ScanRequest) -> ScanResponse:
        source: BaseDataProvider = provider
        if cache is not None:
            source = CachingProvider(provider, cache, timedelta(seconds=req.max_age_s) if req.max_age_s else None)
        try:
            if isinstance(provider, MockDataProvider):
                resolved = provider.resolve(req.address)
                if resolved.chain != req.chain:
                    raise SnapshotNotFound(f"fixture {req.address} is on {resolved.chain.value}, not {req.chain.value}")
                req.address = resolved.address
            snapshot = await source.fetch_snapshot(req.chain, req.address, block=req.block)
        except SnapshotNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        report = engine.score(snapshot, provider=provider.name)
        previous = None
        if cache is not None:
            prev = cache.latest_report(report.chain, report.address)
            previous = prev.risk_score if prev else None
            cache.put_report(report)
        from_cache = isinstance(source, CachingProvider) and source.last_hit
        return ScanResponse(report=report, from_cache=from_cache, previous_score=previous)

    @app.get("/history/{chain}/{address}", response_model=list[RiskReport])
    async def history(chain: Chain, address: str, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> list[RiskReport]:
        if cache is None:
            raise HTTPException(status_code=501, detail="no cache configured")
        try:
            return cache.report_history(chain, address, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/label")
    async def label(req: LabelRequest) -> dict[str, Any]:
        if cache is None:
            raise HTTPException(status_code=501, detail="no cache configured")
        try:
            updated = cache.set_label(req.chain, req.address, req.label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="no cached snapshots for that token")
        return {"updated": updated, "label": req.label}

    @app.get("/weights")
    async def weights_endpoint() -> dict[str, Any]:
        return {
            "flags": config.flags,
            "category_caps": {k.value: v for k, v in config.category_caps.items()},
            "tiers": config.tiers.model_dump(),
            "scoring": config.scoring.model_dump(),
        }

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return cache.stats() if cache is not None else {"cache": False}

    return app
