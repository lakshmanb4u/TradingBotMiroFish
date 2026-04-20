"""Intraday price service using Massive.com (Polygon.io) free tier."""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from massive_client import MassiveClient


def _prev_weekdays(ref: date, n: int) -> date:
    d = ref
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


class IntradayService:
    def __init__(self) -> None:
        self._no_key = False
        try:
            self._client: MassiveClient | None = MassiveClient()
        except ValueError:
            self._no_key = True
            self._client = None

    # ------------------------------------------------------------------ public

    def collect_intraday(self, ticker: str, days_back: int = 5) -> dict:
        today = date.today()
        from_date = _prev_weekdays(today, days_back).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        bars: list[dict] = []
        provider_mode = "massive_live"
        status = "live"

        if not self._no_key and self._client is not None:
            try:
                raw = self._client.get_hourly_bars(ticker, from_date, to_date)
                bars = [self._normalize_bar(b) for b in raw]
            except Exception:
                bars = []

        if not bars:
            bars = self._fixture_fallback(ticker)
            provider_mode = "fixture_fallback"
            status = "fallback"

        features = self._compute_features(bars)
        self._persist(ticker, today, bars, "intraday")

        return {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "bars": bars,
            **features,
            "latest_close": bars[-1]["close"] if bars else 0.0,
            "latest_vwap": bars[-1]["vwap"] if bars else 0.0,
            "source_audit": {
                "intraday": {
                    "status": status,
                    "provider": "massive" if status == "live" else "fixture",
                    "record_count": len(bars),
                    "date_range": {"from": from_date, "to": to_date},
                }
            },
        }

    def collect_today_minutes(self, ticker: str) -> dict:
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")

        bars: list[dict] = []
        provider_mode = "massive_live"
        status = "live"

        if not self._no_key and self._client is not None:
            try:
                raw = self._client.get_minute_bars(ticker, today_str, today_str, limit=390)
                bars = [self._normalize_bar(b) for b in raw]
            except Exception:
                bars = []

        if not bars:
            bars = self._fixture_fallback(ticker)
            provider_mode = "fixture_fallback"
            status = "fallback"

        features = self._compute_features(bars)

        last_15 = bars[-15:] if len(bars) >= 15 else bars
        last_15_closes = [b["close"] for b in last_15]
        if len(last_15_closes) >= 2:
            delta = last_15_closes[-1] - last_15_closes[0]
            last_15min_trend = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        else:
            last_15min_trend = "flat"

        self._persist(ticker, today, bars, "intraday_minutes")

        return {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "bars": bars,
            **features,
            "last_15min_trend": last_15min_trend,
            "latest_close": bars[-1]["close"] if bars else 0.0,
            "latest_vwap": bars[-1]["vwap"] if bars else 0.0,
            "source_audit": {
                "intraday": {
                    "status": status,
                    "provider": "massive" if status == "live" else "fixture",
                    "record_count": len(bars),
                    "date_range": {"from": today_str, "to": today_str},
                }
            },
        }

    # ------------------------------------------------------------------ helpers

    def _normalize_bar(self, b: dict) -> dict:
        ts_ms = b.get("t", 0)
        dt = datetime.utcfromtimestamp(ts_ms / 1000)
        return {
            "timestamp": dt.isoformat(),
            "open": float(b.get("o", 0.0)),
            "high": float(b.get("h", 0.0)),
            "low": float(b.get("l", 0.0)),
            "close": float(b.get("c", 0.0)),
            "volume": int(b.get("v", 0)),
            "vwap": float(b.get("vw", 0.0)),
        }

    def _compute_features(self, bars: list[dict]) -> dict:
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        volumes = [b["volume"] for b in bars]

        if len(closes) < 2:
            return {
                "intraday_trend": "flat",
                "intraday_volatility": 0.0,
                "intraday_momentum": 0.0,
                "avg_hourly_volume": float(volumes[0]) if volumes else 0.0,
                "price_range_pct": 0.0,
            }

        delta = closes[-1] - closes[0]
        intraday_trend = "up" if delta > 0 else ("down" if delta < 0 else "flat")

        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1] != 0
        ]
        intraday_volatility = (
            round(statistics.stdev(returns) * math.sqrt(252 * 6.5), 6)
            if len(returns) >= 2
            else 0.0
        )

        if len(closes) >= 4 and closes[-4] != 0:
            intraday_momentum = round((closes[-1] - closes[-4]) / closes[-4], 6)
        else:
            intraday_momentum = 0.0

        avg_hourly_volume = round(statistics.mean(volumes), 2) if volumes else 0.0

        price_range_pct = 0.0
        if closes[-1] != 0 and highs and lows:
            price_range_pct = round((max(highs) - min(lows)) / closes[-1], 6)

        return {
            "intraday_trend": intraday_trend,
            "intraday_volatility": intraday_volatility,
            "intraday_momentum": intraday_momentum,
            "avg_hourly_volume": avg_hourly_volume,
            "price_range_pct": price_range_pct,
        }

    def _fixture_fallback(self, ticker: str) -> list[dict]:
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "infra" / "fixtures" / "market_data" / f"{ticker.upper()}.json"
        )
        if not fixture_path.exists():
            return []
        try:
            data = json.loads(fixture_path.read_text())
            series = data.get("series", [])[-5:]
            return [
                {
                    "timestamp": f"{day['date']}T14:30:00",
                    "open": float(day.get("open", 0)),
                    "high": float(day.get("high", 0)),
                    "low": float(day.get("low", 0)),
                    "close": float(day.get("close", 0)),
                    "volume": int(day.get("volume", 0)),
                    "vwap": float(day.get("close", 0)),
                }
                for day in series
            ]
        except Exception:
            return []

    def _persist(self, ticker: str, day: date, bars: list[dict], suffix: str) -> None:
        out_dir = Path(__file__).resolve().parents[2] / "state" / "raw" / "intraday"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{ticker.upper()}_{day.strftime('%Y%m%d')}_{suffix}.json"
        (out_dir / fname).write_text(
            json.dumps({"ticker": ticker.upper(), "date": str(day), "bars": bars}, indent=2)
        )
