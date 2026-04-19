"""Alpha Vantage REST client for OHLCV and related data."""
from __future__ import annotations

import os
from typing import Any

import httpx

_BASE = "https://www.alphavantage.co/query"


class AlphaVantageClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.getenv("ALPHAVANTAGE_API_KEY", "")
        if not self._key:
            raise ValueError("ALPHAVANTAGE_API_KEY not set")

    def fetch_daily(self, ticker: str, outputsize: str = "full") -> list[dict[str, Any]]:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                _BASE,
                params={
                    "function": "TIME_SERIES_DAILY_ADJUSTED",
                    "symbol": ticker,
                    "outputsize": outputsize,
                    "apikey": self._key,
                },
            )
            r.raise_for_status()
        raw = r.json()
        if "Error Message" in raw:
            raise ValueError(f"Alpha Vantage error for {ticker}: {raw['Error Message']}")
        if "Note" in raw:
            raise ValueError(f"Alpha Vantage rate limit for {ticker}: {raw['Note']}")
        series_raw = raw.get("Time Series (Daily)", {})
        if not series_raw:
            raise ValueError(f"No time series data from Alpha Vantage for {ticker}")
        items = sorted(series_raw.items())  # ascending chronological
        records = [
            {
                "date": date,
                "open": float(vals["1. open"]),
                "high": float(vals["2. high"]),
                "low": float(vals["3. low"]),
                "close": float(vals["4. close"]),
                "adjusted_close": float(vals["5. adjusted close"]),
                "volume": int(vals["6. volume"]),
                "dividend": float(vals["7. dividend amount"]),
                "split_coeff": float(vals["8. split coefficient"]),
            }
            for date, vals in items
        ]
        return records[-100:]

    def fetch_quote(self, ticker: str) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                _BASE,
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": ticker,
                    "apikey": self._key,
                },
            )
            r.raise_for_status()
        raw = r.json()
        q = raw.get("Global Quote", {})
        if not q:
            raise ValueError(f"No quote data from Alpha Vantage for {ticker}")
        change_pct_str = q.get("10. change percent", "0%").rstrip("%")
        return {
            "price": float(q.get("05. price", 0)),
            "change": float(q.get("09. change", 0)),
            "change_pct": float(change_pct_str),
            "volume": int(q.get("06. volume", 0)),
            "latest_trading_day": q.get("07. latest trading day", ""),
        }
