from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .reporting_service import ReportingService


class RenderRequest(BaseModel):
    raw_bundle: dict
    normalized_bundle: dict
    forecast: dict
    simulation: dict


app = FastAPI(title="market-swarm-lab-reporting", version="0.1.0")
service = ReportingService()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "reporting"}


@app.post("/render/{ticker}")
def render(ticker: str, request: RenderRequest) -> dict:
    return service.generate(
        ticker=ticker,
        raw_bundle=request.raw_bundle,
        normalized_bundle=request.normalized_bundle,
        forecast=request.forecast,
        simulation=request.simulation,
    )
