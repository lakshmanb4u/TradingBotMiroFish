from __future__ import annotations

from fastapi import FastAPI

from .collector_service import MultiSourceCollector

app = FastAPI(title="market-swarm-lab-collector", version="0.1.0")
collector = MultiSourceCollector()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "collector"}


@app.get("/collect/{ticker}")
def collect(ticker: str) -> dict:
    return collector.collect(ticker)
