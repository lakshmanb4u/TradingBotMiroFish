"""Massive.com (Polygon.io) REST client for intraday OHLCV data."""
from __future__ import annotations

import os
import time

import httpx


class MassiveClient:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("MASSIVE_API_KEY", "")
        if not self._api_key:
            raise ValueError("MASSIVE_API_KEY is required")

    def _get(self, path: str, params: dict) -> dict:
        time.sleep(0.2)
        params = {**params, "apiKey": self._api_key}
        resp = httpx.get(f"{self.BASE_URL}{path}", params=params, timeout=15.0)
        resp.raise_for_status()
        return resp.json()

    def get_minute_bars(
        self, ticker: str, from_date: str, to_date: str, limit: int = 390
    ) -> list[dict]:
        data = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/minute/{from_date}/{to_date}",
            {"adjusted": "true", "sort": "asc", "limit": limit},
        )
        return data.get("results") or []

    def get_hourly_bars(
        self, ticker: str, from_date: str, to_date: str, limit: int = 50
    ) -> list[dict]:
        data = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/hour/{from_date}/{to_date}",
            {"adjusted": "true", "sort": "asc", "limit": limit},
        )
        return data.get("results") or []

    def get_daily_bars(
        self, ticker: str, from_date: str, to_date: str, limit: int = 100
    ) -> list[dict]:
        data = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
            {"adjusted": "true", "sort": "asc", "limit": limit},
        )
        return data.get("results") or []

    def get_snapshot(self, ticker: str) -> dict:
        try:
            data = self._get(
                f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                {},
            )
            return data.get("ticker") or {}
        except Exception:
            return {}

    def get_technical_indicator(
        self, ticker: str, indicator: str = "rsi", window: int = 14
    ) -> dict:
        return self._get(
            f"/v1/indicators/{indicator}/{ticker}",
            {
                "timespan": "day",
                "adjusted": "true",
                "window": window,
                "series_type": "close",
                "limit": 10,
            },
        )
