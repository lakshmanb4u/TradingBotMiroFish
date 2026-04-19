from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_FETCHER_DIR = str(_ROOT / "services" / "collector" / "fetchers")
if _FETCHER_DIR not in sys.path:
    sys.path.insert(0, _FETCHER_DIR)
import ohlcv as _ohlcv_fetcher

_AV_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")
_log = logging.getLogger(__name__)


class PriceCollectorService:
    def collect(self, ticker: str) -> dict[str, Any]:
        try:
            data = self._fetch_100_days(ticker)
        except Exception as exc:
            _log.warning("PriceCollectorService live fetch failed for %s: %s", ticker, exc)
            data = _ohlcv_fetcher.fetch_fixture(ticker)
            data.setdefault("live_error", str(exc))

        provider_mode: str = data.get("provider_mode", "fixture_fallback")
        series: list[dict] = data.get("series", [])

        close_prices = [float(r["close"]) for r in series]

        returns: list[float] = []
        for i in range(1, len(close_prices)):
            prev = close_prices[i - 1]
            curr = close_prices[i]
            returns.append(round((curr - prev) / prev, 6) if prev != 0 else 0.0)

        volatility = 0.0
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
            volatility = round(math.sqrt(variance) * math.sqrt(252), 4)

        avg_volume = 0.0
        if series:
            avg_volume = round(
                sum(float(r.get("volume", 0)) for r in series) / len(series), 2
            )

        price_trend = "flat"
        if len(close_prices) >= 20:
            last5_avg = sum(close_prices[-5:]) / 5
            last20_avg = sum(close_prices[-20:]) / 20
            if last5_avg > last20_avg * 1.001:
                price_trend = "up"
            elif last5_avg < last20_avg * 0.999:
                price_trend = "down"

        dates = [r.get("date", "") for r in series if r.get("date")]
        source_audit: dict[str, Any] = {
            "ohlcv": {
                "status": "live" if provider_mode == "alpha_vantage_live" else "fallback",
                "provider": "alpha_vantage" if provider_mode == "alpha_vantage_live" else "fixture",
                "record_count": len(series),
                "date_range": {
                    "from": dates[0] if dates else "",
                    "to": dates[-1] if dates else "",
                },
            }
        }

        return {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "series": series,
            "close_prices": close_prices,
            "returns": returns,
            "volatility": volatility,
            "avg_volume": avg_volume,
            "price_trend": price_trend,
            "source_audit": source_audit,
        }

    def features(self, ticker: str) -> dict[str, Any]:
        data = self.collect(ticker)
        return {
            "close_prices": data["close_prices"],
            "returns": data["returns"],
            "volatility": data["volatility"],
            "price_trend": data["price_trend"],
        }

    def _fetch_100_days(self, ticker: str) -> dict[str, Any]:
        if not _AV_KEY:
            raise ValueError("ALPHAVANTAGE_API_KEY not set")
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "TIME_SERIES_DAILY_ADJUSTED",
                    "symbol": ticker,
                    "outputsize": "full",
                    "apikey": _AV_KEY,
                },
            )
            r.raise_for_status()
        raw = r.json()
        series_raw = raw.get("Time Series (Daily)", {})
        if not series_raw:
            raise ValueError(f"No time series data from Alpha Vantage for {ticker}")
        items = list(series_raw.items())[:100]
        points = [
            {
                "date": date,
                "open": float(vals["1. open"]),
                "high": float(vals["2. high"]),
                "low": float(vals["3. low"]),
                "close": float(vals["4. close"]),
                "volume": int(vals["6. volume"]),
                "vwap": round(
                    (float(vals["2. high"]) + float(vals["3. low"]) + float(vals["4. close"])) / 3,
                    4,
                ),
            }
            for date, vals in items
        ]
        points.reverse()
        return {"ticker": ticker.upper(), "provider_mode": "alpha_vantage_live", "series": points}
