"""
Forecasting service FastAPI app.

POST /forecast
  Input:
    ticker   - e.g. "NVDA"
    series   - {"close": [], "volume": [], "vwap": [], "rsi": []}
    horizon  - int, default 5

  Output:
    forecast        - list of point forecasts (length == horizon)
    quantiles       - {"p10": [], "p50": [], "p90": []}
    direction       - "up" | "down" | "sideways"
    confidence      - float 0..1
    provider_mode   - "timesfm_2p5_pytorch" | "local_fallback" | ...
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .forecasting_service import TimesFMForecastingService

app = FastAPI(title="market-swarm-lab-forecasting", version="0.2.0")
_service = TimesFMForecastingService()


class SeriesInput(BaseModel):
    close: list[float] = Field(default_factory=list)
    volume: list[float] = Field(default_factory=list)
    vwap: list[float] = Field(default_factory=list)
    rsi: list[float] = Field(default_factory=list)


class ForecastRequest(BaseModel):
    ticker: str
    series: SeriesInput
    horizon: int = Field(default=5, ge=1, le=256)


class Quantiles(BaseModel):
    p10: list[float]
    p50: list[float]
    p90: list[float]


class ForecastResponse(BaseModel):
    ticker: str
    provider_mode: str
    horizon: int
    forecast: list[float]
    quantiles: Quantiles
    direction: str
    confidence: float
    fallback_reason: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "forecasting"}


@app.post("/forecast", response_model=ForecastResponse)
def forecast(request: ForecastRequest) -> dict:
    return _service.run_api(
        ticker=request.ticker,
        series={
            "close": request.series.close,
            "volume": request.series.volume,
            "vwap": request.series.vwap,
            "rsi": request.series.rsi,
        },
        horizon=request.horizon,
    )
