"""
Collector service FastAPI app.

GET  /collect/sec?ticker=NVDA
GET  /collect/news?ticker=NVDA&limit=10
GET  /collect/polymarket?ticker=NVDA&limit=10
GET  /collect/kalshi?ticker=NVDA&limit=10
POST /collect/ohlcv          body: OHLCVRequest (ticker + optional csv_data)

All endpoints:
- attempt live fetch first
- fall back to fixture automatically
- save raw payload to state/raw/<source>/
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from .collector_service import MultiSourceCollector
from .fetchers import sec, news, polymarket, kalshi, ohlcv, store

app = FastAPI(title="market-swarm-lab-collector", version="0.2.0")
_multi = MultiSourceCollector()


# ──────────────────────────────────────── health

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "collector"}


# ──────────────────────────────────────── SEC EDGAR

@app.get("/collect/sec")
def collect_sec(
    ticker: str = Query(..., description="Ticker symbol, e.g. NVDA"),
    limit: int = Query(5, ge=1, le=20),
) -> dict:
    result = sec.fetch(ticker=ticker, limit=limit)
    path = store.save("sec", ticker, result)
    result["stored_at"] = path
    return result


# ──────────────────────────────────────── News

@app.get("/collect/news")
def collect_news(
    ticker: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    result = news.fetch(ticker=ticker, limit=limit)
    path = store.save("news", ticker, result)
    result["stored_at"] = path
    return result


# ──────────────────────────────────────── Polymarket

@app.get("/collect/polymarket")
def collect_polymarket(
    ticker: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    result = polymarket.fetch(ticker=ticker, limit=limit)
    path = store.save("polymarket", ticker, result)
    result["stored_at"] = path
    return result


# ──────────────────────────────────────── Kalshi

@app.get("/collect/kalshi")
def collect_kalshi(
    ticker: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    result = kalshi.fetch(ticker=ticker, limit=limit)
    path = store.save("kalshi", ticker, result)
    result["stored_at"] = path
    return result


# ──────────────────────────────────────── OHLCV

class OHLCVRequest(BaseModel):
    ticker: str
    csv_data: str | None = Field(
        default=None,
        description="CSV text with columns: date,open,high,low,close,volume. "
                    "If omitted, pulls from Alpha Vantage (requires ALPHAVANTAGE_API_KEY).",
    )
    outputsize: str = Field(default="compact", pattern="^(compact|full)$")


@app.post("/collect/ohlcv")
def collect_ohlcv(request: OHLCVRequest) -> dict:
    if request.csv_data:
        result = ohlcv.parse_csv(ticker=request.ticker, csv_text=request.csv_data)
    else:
        result = ohlcv.fetch(ticker=request.ticker)
    path = store.save("ohlcv", request.ticker, result)
    result["stored_at"] = path
    return result


# ──────────────────────────────────────── collect-all (convenience)

@app.get("/collect/all")
def collect_all(ticker: str = Query(...)) -> dict:
    """Collect all sources for a ticker in one call."""
    return _multi.collect(ticker)
