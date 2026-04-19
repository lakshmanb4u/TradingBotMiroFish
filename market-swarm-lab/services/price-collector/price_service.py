"""Price service: fetch, normalize, derive features, persist."""
from __future__ import annotations

import json
import logging
import math
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

_SERVICE_DIR = Path(__file__).resolve().parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from alpha_vantage_client import AlphaVantageClient

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)


class PriceService:
    def __init__(self) -> None:
        self._no_key = False
        self._client: AlphaVantageClient | None = None
        try:
            self._client = AlphaVantageClient()
        except ValueError:
            self._no_key = True

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        provider_mode = "alpha_vantage_live"
        raw_records: list[dict[str, Any]] = []

        # 1. Fetch 100 days
        if self._no_key or self._client is None:
            raw_records, provider_mode = self._load_fixture(ticker)
        else:
            try:
                raw_records = self._client.fetch_daily(ticker)
                provider_mode = "alpha_vantage_live"
            except Exception as exc:
                _log.warning("AlphaVantageClient.fetch_daily failed for %s: %s", ticker, exc)
                raw_records, provider_mode = self._load_fixture(ticker)

        # 2. Normalize
        series: list[dict[str, Any]] = [
            {
                "timestamp": r.get("date", r.get("timestamp", "")),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "volume": int(r.get("volume", 0)),
            }
            for r in raw_records
        ]

        # 3. Derive features
        close_prices = [r["close"] for r in series]

        daily_returns: list[float] = [
            round((close_prices[i] - close_prices[i - 1]) / close_prices[i - 1], 6)
            if close_prices[i - 1] != 0 else 0.0
            for i in range(1, len(close_prices))
        ]

        rolling_volatility_5d = 0.0
        if len(daily_returns) >= 5:
            last5 = daily_returns[-5:]
            if len(last5) > 1:
                rolling_volatility_5d = round(statistics.stdev(last5) * math.sqrt(252), 6)

        rolling_volatility_10d = 0.0
        if len(daily_returns) >= 10:
            last10 = daily_returns[-10:]
            if len(last10) > 1:
                rolling_volatility_10d = round(statistics.stdev(last10) * math.sqrt(252), 6)

        momentum = 0.0
        if len(close_prices) >= 10 and close_prices[-10] != 0:
            momentum = round((close_prices[-1] - close_prices[-10]) / close_prices[-10], 4)

        price_trend = "flat"
        if len(close_prices) >= 20:
            last5_avg = sum(close_prices[-5:]) / 5
            last20_avg = sum(close_prices[-20:]) / 20
            if last5_avg > last20_avg * 1.001:
                price_trend = "up"
            elif last5_avg < last20_avg * 0.999:
                price_trend = "down"

        vwap = 0.0
        if series:
            typical_prices = [(r["high"] + r["low"] + r["close"]) / 3 for r in series]
            vwap = round(sum(typical_prices) / len(typical_prices), 4)

        rsi_14 = self._compute_rsi(close_prices)

        avg_volume = 0.0
        if series:
            avg_volume = round(sum(r["volume"] for r in series) / len(series), 2)

        # 4. Persist raw JSON
        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "ohlcv"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{ticker}_{today_str}.json"
        try:
            with open(raw_path, "w") as f:
                json.dump(
                    {"ticker": ticker, "provider_mode": provider_mode, "records": raw_records},
                    f,
                    indent=2,
                )
        except Exception as exc:
            _log.warning("Raw JSON persist failed: %s", exc)

        # 5. Persist Parquet
        parquet_path: str | None = None
        try:
            import pandas as pd  # noqa: PLC0415
            parquet_dir = _ROOT / "data" / "market_data" / "ohlcv"
            parquet_dir.mkdir(parents=True, exist_ok=True)
            pq_file = parquet_dir / f"{ticker}.parquet"
            df = pd.DataFrame(series)
            df.to_parquet(str(pq_file), index=False)
            parquet_path = str(pq_file)
        except ImportError:
            _log.warning("pandas/pyarrow not available; skipping Parquet persist")
        except Exception as exc:
            _log.warning("Parquet persist failed: %s", exc)

        # 6. Return
        return {
            "ticker": ticker,
            "provider_mode": provider_mode,
            "series": series,
            "close_prices": close_prices,
            "daily_returns": daily_returns,
            "rolling_volatility_5d": rolling_volatility_5d,
            "rolling_volatility_10d": rolling_volatility_10d,
            "momentum": momentum,
            "price_trend": price_trend,
            "vwap": vwap,
            "rsi_14": rsi_14,
            "avg_volume": avg_volume,
            "raw_artifact_path": str(raw_path),
            "parquet_path": parquet_path,
            "source_audit": {
                "ohlcv": {
                    "status": "live" if provider_mode == "alpha_vantage_live" else "fallback",
                    "provider": "alphavantage" if provider_mode == "alpha_vantage_live" else "fixture",
                    "record_count": len(series),
                }
            },
        }

    def features(self, ticker: str) -> dict[str, Any]:
        data = self.collect(ticker)
        return {
            "close_prices": data["close_prices"],
            "daily_returns": data["daily_returns"],
            "rolling_volatility_5d": data["rolling_volatility_5d"],
            "rolling_volatility_10d": data["rolling_volatility_10d"],
            "momentum": data["momentum"],
            "price_trend": data["price_trend"],
            "vwap": data["vwap"],
            "rsi_14": data["rsi_14"],
        }

    def _load_fixture(self, ticker: str) -> tuple[list[dict[str, Any]], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "market_data" / f"{ticker}.json"
        if not fixture_path.exists():
            _log.warning("No fixture found for %s at %s", ticker, fixture_path)
            return [], "fixture_fallback"
        try:
            with open(fixture_path) as f:
                data = json.load(f)
        except Exception as exc:
            _log.warning("Failed to load fixture for %s: %s", ticker, exc)
            return [], "fixture_fallback"
        series = data.get("series", [])
        normalized = [
            {
                "date": r.get("date", r.get("timestamp", "")),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "volume": int(r.get("volume", 0)),
            }
            for r in series
        ]
        return normalized, "fixture_fallback"

    def _compute_rsi(self, close_prices: list[float], period: int = 14) -> float:
        if len(close_prices) < period + 1:
            return 50.0
        changes = [close_prices[i] - close_prices[i - 1] for i in range(1, len(close_prices))]
        recent = changes[-period:]
        gains = [c for c in recent if c > 0]
        losses = [abs(c) for c in recent if c < 0]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)
