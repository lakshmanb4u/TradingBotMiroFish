from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .db import init_infra
from .workflow import run_ticker_workflow


class RunResponse(BaseModel):
    ticker: str
    provider_modes: dict
    raw: dict
    normalized: dict
    forecast: dict
    simulation: dict
    report: dict


class RunRequest(BaseModel):
    persist: bool = Field(default=True)


app = FastAPI(title="market-swarm-lab", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    init_infra()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "market-swarm-lab"}


@app.post("/v1/tickers/{ticker}/run", response_model=RunResponse)
def run_ticker(ticker: str, request: RunRequest | None = None) -> dict:
    persist = True if request is None else request.persist
    return run_ticker_workflow(ticker=ticker, persist=persist)
