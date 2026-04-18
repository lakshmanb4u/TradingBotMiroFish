"""Polymarket fetcher — public CLOB API, no key required."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_POLY_API = "https://clob.polymarket.com"
_GAMMA_API = "https://gamma-api.polymarket.com"

# Keyword map: ticker -> search terms for market titles
_TICKER_KEYWORDS = {
    "NVDA": ["nvidia", "nvda", "semiconductor", "ai chip"],
    "SPY":  ["s&p 500", "spy", "spx", "stock market", "us stocks"],
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
        resp = client.get(f"{_GAMMA_API}/markets", params={"limit": 50, "active": "true"})
        resp.raise_for_status()
        all_markets = resp.json()

    if isinstance(all_markets, dict):
        all_markets = all_markets.get("markets", all_markets.get("data", []))

    markets = []
    for m in all_markets:
        title = (m.get("question") or m.get("title") or "").lower()
        if any(kw in title for kw in keywords):
            markets.append({
                "venue": "Polymarket",
                "contract": m.get("question") or m.get("title"),
                "probability_yes": _parse_prob(m),
                "volume_usd": float(m.get("volume", 0) or 0),
                "end_date": m.get("end_date_iso") or m.get("endDate"),
            })
        if len(markets) >= limit:
            break

    return {"ticker": ticker.upper(), "provider_mode": "polymarket_live", "markets": markets}


def _parse_prob(m: dict) -> float:
    for key in ("outcomePrices", "outcome_prices", "bestBid", "lastTradedPrice"):
        val = m.get(key)
        if val is None:
            continue
        if isinstance(val, list) and val:
            try:
                return round(float(val[0]), 4)
            except (ValueError, TypeError):
                pass
        try:
            return round(float(val), 4)
        except (ValueError, TypeError):
            pass
    return 0.5


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / "prediction_markets" / f"{ticker.upper()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"ticker": ticker.upper(), "markets": []}
