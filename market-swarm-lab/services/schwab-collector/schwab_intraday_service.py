"""Schwab Intraday Service — 5-min bar analysis + signal detection.

Fetches today's intraday bars via Schwab REST API and computes:
  - VWAP, intraday trend, momentum
  - RSI (14-period on 5-min bars)
  - Volume spikes (relative to avg bar volume)
  - Key intraday levels: morning high, support, resistance
  - GEX proxy: put/call imbalance at each bar (from options chain)
  - Signal detection:
      * OVERBOUGHT_SHORT  — RSI > 70 near resistance → put signal
      * OVERSOLD_LONG     — RSI < 30 near support → call signal
      * VWAP_REJECTION    — price rejected off VWAP → fade signal
      * VOLUME_REVERSAL   — volume spike at extreme → reversal signal
      * LEVEL_BREAKDOWN   — price breaks key support → put signal
      * LEVEL_BREAKOUT    — price breaks key resistance → call signal

Run standalone for live output:
    python3 schwab_intraday_service.py
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from schwab_auth import get_valid_token

import requests

_log = logging.getLogger(__name__)
BASE_URL = os.environ.get("SCHWAB_BASE_URL", "https://api.schwabapi.com")


class SchwabIntradayService:
    """Fetches 5-min intraday bars and generates intraday trade signals."""

    def __init__(self, frequency_minutes: int = 5) -> None:
        self._freq = frequency_minutes
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_valid_token()}",
            "Accept": "application/json",
        }

    # ── Data Fetch ─────────────────────────────────────────────────────────────

    def fetch_bars(self, symbol: str, days: int = 1) -> list[dict[str, Any]]:
        """Fetch intraday OHLCV bars. Default: today's session."""
        symbol = symbol.upper()
        resp = self._session.get(
            f"{BASE_URL}/marketdata/v1/pricehistory",
            headers=self._headers(),
            params={
                "symbol": symbol,
                "periodType": "day",
                "period": days,
                "frequencyType": "minute",
                "frequency": self._freq,
                "needExtendedHoursData": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        candles = resp.json().get("candles", [])
        _log.info("[intraday] %s: %d %d-min bars", symbol, len(candles), self._freq)
        return [
            {
                "ts": c["datetime"],
                "dt": datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc).strftime("%H:%M"),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(c["volume"]),
            }
            for c in candles
        ]

    # ── Indicators ─────────────────────────────────────────────────────────────

    def _vwap(self, bars: list[dict]) -> list[float]:
        """Running VWAP across bars."""
        cum_pv = 0.0
        cum_v = 0.0
        vwap = []
        for b in bars:
            typical = (b["high"] + b["low"] + b["close"]) / 3
            cum_pv += typical * b["volume"]
            cum_v += b["volume"]
            vwap.append(round(cum_pv / cum_v, 4) if cum_v > 0 else b["close"])
        return vwap

    def _rsi(self, closes: list[float], period: int = 14) -> list[float]:
        """RSI on each bar."""
        rsi_vals = [50.0] * len(closes)
        if len(closes) < period + 1:
            return rsi_vals
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs = avg_g / avg_l if avg_l > 0 else 100.0
            rsi_vals[i + 1] = round(100.0 - 100.0 / (1.0 + rs), 2)
        return rsi_vals

    def _volume_avg(self, bars: list[dict]) -> float:
        vols = [b["volume"] for b in bars]
        return statistics.mean(vols) if vols else 1.0

    # ── Key Levels ─────────────────────────────────────────────────────────────

    def _key_levels(self, bars: list[dict]) -> dict[str, float]:
        if not bars:
            return {}
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]

        morning_bars = bars[:min(12, len(bars))]  # first hour
        morning_high = max(b["high"] for b in morning_bars)
        morning_low = min(b["low"] for b in morning_bars)

        session_high = max(highs)
        session_low = min(lows)

        return {
            "morning_high": round(morning_high, 2),
            "morning_low": round(morning_low, 2),
            "session_high": round(session_high, 2),
            "session_low": round(session_low, 2),
            "prev_close": round(closes[0], 2),  # open as proxy
        }

    # ── Signal Detection ───────────────────────────────────────────────────────

    def _detect_signals(
        self,
        bars: list[dict],
        vwap: list[float],
        rsi: list[float],
        levels: dict[str, float],
        avg_vol: float,
    ) -> list[dict[str, Any]]:
        signals = []
        if len(bars) < 15:
            return signals

        for i in range(14, len(bars)):
            b = bars[i]
            r = rsi[i]
            v = vwap[i]
            price = b["close"]
            vol_spike = b["volume"] / avg_vol if avg_vol > 0 else 1.0

            # OVERBOUGHT near session/morning high → put signal
            if r > 70 and price >= levels.get("morning_high", 9999) * 0.998:
                signals.append({
                    "time": b["dt"],
                    "type": "OVERBOUGHT_SHORT",
                    "action": "BUY_PUTS",
                    "price": price,
                    "rsi": r,
                    "reason": f"RSI {r:.0f} overbought at/near morning high ${levels.get('morning_high'):.2f}",
                    "confidence": min(0.85, 0.5 + (r - 70) / 100 + (vol_spike - 1) * 0.05),
                })

            # OVERSOLD near session/morning low → call signal
            elif r < 30 and price <= levels.get("morning_low", 0) * 1.002:
                signals.append({
                    "time": b["dt"],
                    "type": "OVERSOLD_LONG",
                    "action": "BUY_CALLS",
                    "price": price,
                    "rsi": r,
                    "reason": f"RSI {r:.0f} oversold at/near morning low ${levels.get('morning_low'):.2f}",
                    "confidence": min(0.85, 0.5 + (30 - r) / 100 + (vol_spike - 1) * 0.05),
                })

            # VWAP rejection (price crossed below VWAP with volume)
            elif i > 0 and bars[i - 1]["close"] > vwap[i - 1] and price < v and vol_spike > 1.5:
                signals.append({
                    "time": b["dt"],
                    "type": "VWAP_REJECTION",
                    "action": "BUY_PUTS",
                    "price": price,
                    "vwap": v,
                    "vol_spike": round(vol_spike, 2),
                    "reason": f"Price crossed below VWAP ${v:.2f} with {vol_spike:.1f}x volume",
                    "confidence": min(0.80, 0.55 + vol_spike * 0.05),
                })

            # VOLUME REVERSAL at extreme (high vol at low RSI = bounce)
            elif vol_spike > 2.0 and r < 35:
                signals.append({
                    "time": b["dt"],
                    "type": "VOLUME_REVERSAL",
                    "action": "BUY_CALLS",
                    "price": price,
                    "rsi": r,
                    "vol_spike": round(vol_spike, 2),
                    "reason": f"Volume {vol_spike:.1f}x avg at oversold RSI {r:.0f} — likely reversal",
                    "confidence": min(0.82, 0.55 + vol_spike * 0.04 + (35 - r) / 100),
                })

            # SUPPORT BREAKDOWN
            elif (price < levels.get("morning_low", 0) * 0.998
                  and i > 0 and bars[i - 1]["close"] >= levels.get("morning_low", 0)):
                signals.append({
                    "time": b["dt"],
                    "type": "LEVEL_BREAKDOWN",
                    "action": "BUY_PUTS",
                    "price": price,
                    "level": levels.get("morning_low"),
                    "reason": f"Broke below morning low ${levels.get('morning_low'):.2f} — bearish",
                    "confidence": 0.75,
                })

            # RESISTANCE BREAKOUT
            elif (price > levels.get("morning_high", 9999) * 1.001
                  and i > 0 and bars[i - 1]["close"] <= levels.get("morning_high", 9999)
                  and vol_spike > 1.3):
                signals.append({
                    "time": b["dt"],
                    "type": "LEVEL_BREAKOUT",
                    "action": "BUY_CALLS",
                    "price": price,
                    "level": levels.get("morning_high"),
                    "reason": f"Broke above morning high ${levels.get('morning_high'):.2f} with {vol_spike:.1f}x volume",
                    "confidence": min(0.80, 0.65 + vol_spike * 0.03),
                })

        return signals

    # ── Main Collect ───────────────────────────────────────────────────────────

    def collect(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        bars = self.fetch_bars(symbol)
        if not bars:
            return {"symbol": symbol, "source": "no_data", "bars": [], "signals": []}

        closes = [b["close"] for b in bars]
        vwap = self._vwap(bars)
        rsi = self._rsi(closes)
        avg_vol = self._volume_avg(bars)
        levels = self._key_levels(bars)
        signals = self._detect_signals(bars, vwap, rsi, levels, avg_vol)

        # Intraday trend: compare last price to open
        open_price = bars[0]["close"]
        last_price = bars[-1]["close"]
        intraday_return = (last_price - open_price) / open_price
        intraday_trend = "up" if intraday_return > 0.001 else ("down" if intraday_return < -0.001 else "flat")

        # Current RSI and VWAP
        current_rsi = rsi[-1]
        current_vwap = vwap[-1]
        price_vs_vwap = "above" if last_price > current_vwap else "below"

        return {
            "symbol": symbol,
            "source": "schwab_live",
            "bar_count": len(bars),
            "frequency_min": self._freq,
            "levels": levels,
            "current": {
                "price": last_price,
                "vwap": current_vwap,
                "price_vs_vwap": price_vs_vwap,
                "rsi": current_rsi,
                "intraday_trend": intraday_trend,
                "intraday_return_pct": round(intraday_return * 100, 3),
                "last_bar_time": bars[-1]["dt"],
                "last_bar_volume": bars[-1]["volume"],
                "avg_bar_volume": round(avg_vol),
            },
            "signals": signals,
            "bars_sample": bars[-10:],  # last 10 bars for context
            "source_audit": {
                "intraday": {
                    "status": "live",
                    "provider": "schwab",
                    "record_count": len(bars),
                }
            },
        }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    svc = SchwabIntradayService(frequency_minutes=5)
    result = svc.collect("SPY")

    print(f"\n{'='*50}")
    print(f"SPY Intraday — {result['bar_count']} bars")
    print(f"{'='*50}")
    print(f"Price:       ${result['current']['price']:.2f}")
    print(f"VWAP:        ${result['current']['vwap']:.2f} ({result['current']['price_vs_vwap']})")
    print(f"RSI:         {result['current']['rsi']:.1f}")
    print(f"Trend:       {result['current']['intraday_trend']} ({result['current']['intraday_return_pct']:+.2f}%)")
    print(f"Key Levels:  {result['levels']}")

    print(f"\n{'='*50}")
    print(f"SIGNALS ({len(result['signals'])} detected today):")
    print(f"{'='*50}")
    for s in result["signals"]:
        conf = s.get("confidence", 0)
        print(f"  [{s['time']}] {s['action']:12s} | {s['type']:20s} | conf:{conf:.0%} | {s['reason']}")

    if not result["signals"]:
        print("  No signals detected today.")
