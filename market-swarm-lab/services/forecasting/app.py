from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .forecasting_service import TimesFMForecastingService


class ForecastRequest(BaseModel):
    normalized_bundle: dict


app = FastAPI(title="market-swarm-lab-forecasting", version="0.1.0")
service = TimesFMForecastingService()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "forecasting"}


@app.post("/forecast/{ticker}")
def forecast(ticker: str, request: ForecastRequest) -> dict:
    return service.forecast(ticker=ticker, normalized_bundle=request.normalized_bundle)
