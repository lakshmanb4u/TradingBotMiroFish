"""OHLCV fetcher — Alpha Vantage live or CSV upload, fixture fallback.

Accepts either:
  - CSV text (comma-separated: date,open,high,low,close,volume)
  - Alpha Vantage API pull when ALPHAVANTAGE_API_KEY is set
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any

import httpx

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_AV_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")


def fetch_live(ticker: str, outputsize: str = "compact") -> dict[str, Any]:
    """Pull from Alpha Vantage. Raises on failure."""
    if not _AV_KEY:
        raise ValueError("ALPHAVANTAGE_API_KEY not set")
    with httpx.Client(timeout=15.0) as client:
        r = client.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": ticker,
                "outputsize": outputsize,
                "apikey": _AV_KEY,
            },
        )
        r.raise_for_status()
    raw = r.json()
    series = raw.get("Time Series (Daily)", {})
    points = [
        {
            "date": date,
            "open": float(vals["1. open"]),
            "high": float(vals["2. high"]),
            "low": float(vals["3. low"]),
            "close": float(vals["4. close"]),
            "volume": int(vals["6. volume"]),
            "vwap": round((float(vals["2. high"]) + float(vals["3. low"]) + float(vals["4. close"])) / 3, 4),
        }
        for date, vals in list(series.items())[:30]
    ]
    points.reverse()
    return {"ticker": ticker.upper(), "provider_mode": "alpha_vantage_live", "series": points}


def parse_csv(ticker: str, csv_text: str) -> dict[str, Any]:
    """Parse uploaded CSV text.

    Expected header (optional): date,open,high,low,close,volume
    """
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    rows = []
    for row in reader:
        keys = [k.lower().strip() for k in row.keys()]
        vals = list(row.values())
        record = dict(zip(keys, vals))
        close = float(record.get("close", record.get("adj close", 0)))
        high = float(record.get("high", close))
        low = float(record.get("low", close))
        rows.append({
            "date": record.get("date", record.get("timestamp", "")),
            "open": float(record.get("open", close)),
            "high": high,
            "low": low,
            "close": close,
            "volume": int(float(record.get("volume", 0))),
            "vwap": round((high + low + close) / 3, 4),
        })
    return {"ticker": ticker.upper(), "provider_mode": "csv_upload", "series": rows}


def fetch_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / "market_data" / f"{ticker.upper()}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["provider_mode"] = "fixture_fallback"
        return data
    return {"ticker": ticker.upper(), "provider_mode": "fixture_fallback", "series": []}


def fetch(ticker: str) -> dict[str, Any]:
    try:
        return fetch_live(ticker)
    except Exception as exc:
        data = fetch_fixture(ticker)
        data["live_error"] = str(exc)
        return data
