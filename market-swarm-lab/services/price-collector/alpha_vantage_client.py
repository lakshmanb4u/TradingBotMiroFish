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
        """Fetch daily OHLCV from Alpha Vantage.

        Fix 2026-04-26: Use TIME_SERIES_DAILY (unadjusted) instead of
        TIME_SERIES_DAILY_ADJUSTED — the free AV API tier does not support
        adjusted data and returns an empty series silently.

        Also: if 'full' outputsize returns empty (rate-limit on free tier),
        automatically retry with 'compact' (last 100 trading days). This
        fixes the backtest_replay.py zero-bars bug where underlying_price=$0
        caused the entire synthetic chain generation to abort.
        """
        for attempt_size in ([outputsize] if outputsize != "full" else ["full", "compact"]):
            with httpx.Client(timeout=20.0) as client:
                r = client.get(
                    _BASE,
                    params={
                        "function": "TIME_SERIES_DAILY",
                        "symbol": ticker,
                        "outputsize": attempt_size,
                        "apikey": self._key,
                    },
                )
                r.raise_for_status()
            raw = r.json()
            if "Error Message" in raw:
                raise ValueError(f"Alpha Vantage error for {ticker}: {raw['Error Message']}")
            # AV uses both 'Note' and 'Information' keys for rate-limit messages
            rate_limited = "Note" in raw or (
                "Information" in raw and
                ("premium" in raw["Information"].lower() or "rate" in raw["Information"].lower() or "spread" in raw["Information"].lower())
            )
            if rate_limited and attempt_size == "compact":
                # Both sizes exhausted — propagate the message
                msg = raw.get("Note") or raw.get("Information", "rate limit")
                raise ValueError(f"Alpha Vantage rate limit for {ticker}: {msg[:120]}")
            if rate_limited:
                # full hit rate-limit — fall through to compact retry
                continue
            series_raw = raw.get("Time Series (Daily)", {})
            if not series_raw and attempt_size == "compact":
                raise ValueError(f"No time series data from Alpha Vantage for {ticker}")
            if not series_raw:
                # full returned empty — fall through to compact
                continue
            items = sorted(series_raw.items())  # ascending chronological
            records = [
                {
                    "date": dt,
                    "open":   float(vals["1. open"]),
                    "high":   float(vals["2. high"]),
                    "low":    float(vals["3. low"]),
                    "close":  float(vals["4. close"]),
                    "volume": int(vals["5. volume"]),
                }
                for dt, vals in items
            ]
            return records
        raise ValueError(f"No time series data from Alpha Vantage for {ticker} (both full and compact failed)")

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
