"""Seed builder FastAPI app. POST /seed/build"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from seed_builder_service import SeedBuilderService  # noqa: E402

_SEED_DIR = Path("state/seeds")
_SEED_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="market-swarm-lab-seed-builder", version="0.1.0")
_service = SeedBuilderService()


class BuildRequest(BaseModel):
    ticker: str
    normalized_bundle: dict[str, Any]
    forecast: dict[str, Any] | None = Field(default=None)


class SeedResponse(BaseModel):
    ticker: str
    fundamental_summary: str
    retail_sentiment_summary: str
    news_summary: str
    prediction_market_summary: str
    timesfm_summary: str
    key_bullish_points: list[str]
    key_bearish_points: list[str]
    disagreement_level: float
    agent_personas: list[dict[str, Any]] = Field(default_factory=list)
    stored_at: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "seed-builder"}


@app.post("/seed/build", response_model=SeedResponse)
def build_seed(request: BuildRequest) -> dict:
    result = _service.build(
        ticker=request.ticker,
        normalized_bundle=request.normalized_bundle,
        forecast=request.forecast,
    )
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _SEED_DIR / f"{request.ticker.upper()}_{ts}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["stored_at"] = str(path)
    return result
