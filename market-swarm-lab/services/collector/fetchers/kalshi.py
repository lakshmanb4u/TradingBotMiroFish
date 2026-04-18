"""Kalshi fetcher — public markets API (no key for read-only market data)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"

_TICKER_KEYWORDS = {
    "NVDA": ["nvidia", "semiconductor", "ai", "chip"],
    "SPY":  ["s&p", "stock market", "nasdaq", "recession", "fed", "rate"],
}


def fetch(ticker: str, limit: int = 10) -> dict[str, Any]:
    try:
        return _fetch_live(ticker, limit)
    except Exception as exc:
        data = _load_fixture(ticker)
        data["provider_mode"] = "fixture_fallback"
        data["live_error"] = str(exc)
        return data


def _fetch_live(ticker: str, limit: int) -> dict[str, Any]:
    keywords = _TICKER_KEYWORDS.get(ticker.upper(), [ticker.lower()])
    with httpx.Client(timeout=12.0) as client:
        resp = client.get(
            f"{_KALSHI_BASE}/markets",
            params={"limit": 100, "status": "open"},
            headers={"accept": "application/json"},
        )
        resp.raise_for_status()
        raw = resp.json()

    all_markets = raw.get("markets", [])
    markets = []
    for m in all_markets:
        title = (m.get("title") or m.get("question") or "").lower()
        subtitle = (m.get("subtitle") or "").lower()
        if any(kw in title or kw in subtitle for kw in keywords):
            yes_price = m.get("yes_bid") or m.get("last_price") or 50
            markets.append({
                "venue": "Kalshi",
                "contract": m.get("title"),
                "ticker_kalshi": m.get("ticker"),
                "probability_yes": round(float(yes_price) / 100, 4),
                "volume": float(m.get("volume", 0) or 0),
                "close_time": m.get("close_time"),
            })
        if len(markets) >= limit:
            break

    return {"ticker": ticker.upper(), "provider_mode": "kalshi_live", "markets": markets}


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / "prediction_markets" / f"{ticker.upper()}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        # tag as Kalshi subset of fixture
        kalshi_markets = [m for m in data.get("markets", []) if m.get("venue") == "Kalshi"]
        return {"ticker": ticker.upper(), "markets": kalshi_markets}
    return {"ticker": ticker.upper(), "markets": []}
