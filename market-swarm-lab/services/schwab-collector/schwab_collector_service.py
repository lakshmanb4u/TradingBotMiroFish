"""Schwab Collector Service.

Drop-in replacement for price-collector. Produces the same normalized
price bundle that the normalizer/forecasting services expect, plus
an additional 'options_chain' key with raw chain data for flow analysis.

Output bundle schema:
{
    "source": "schwab_live" | "schwab_fixture",
    "symbol": "SPY",
    "quote": { lastPrice, bidPrice, askPrice, totalVolume, ... },
    "price_rich": {
        "close_prices": [...],
        "daily_returns": [...],
        "volatility": float,
        "price_trend": "up"|"down"|"flat",
        "rsi_14": float,
        "vwap": float,
        "momentum": float,
        "rolling_volatility_5d": float,
        "rolling_volatility_10d": float,
    },
    "options_chain": { callExpDateMap, putExpDateMap, ... },
    "options_features": {
        "iv_rank": float,          # 0-100
        "call_put_ratio": float,   # >1 = more calls (bullish lean)
        "atm_iv": float,           # ATM implied volatility
        "flow_bias": "bullish"|"bearish"|"neutral",
    }
}
"""
from __future__ import annotations

import logging
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow running standalone
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from schwab_client import SchwabClient

_log = logging.getLogger(__name__)


class SchwabCollectorService:
    def __init__(self) -> None:
        self._client = SchwabClient()

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        source = "schwab_live"

        # 1. Real-time quote
        quote = self._client.get_quote(ticker)
        if quote.get("source") == "fixture_missing":
            source = "schwab_fixture"

        # 2. Price history (1 year daily)
        candles = self._client.get_price_history(ticker)
        if not candles:
            source = "schwab_fixture"

        # 3. Options chain
        chain = self._client.get_options_chain(ticker)

        # 4. Compute price features
        price_rich = self._compute_price_features(candles)

        # 5. Compute options features
        options_features = self._compute_options_features(chain, quote)

        return {
            "source": source,
            "symbol": ticker,
            "quote": quote,
            "price_rich": price_rich,
            "options_chain": chain,
            "options_features": options_features,
        }

    # ── Price Feature Computation ──────────────────────────────────────────────

    def _compute_price_features(self, candles: list[dict]) -> dict[str, Any]:
        if not candles:
            return {
                "close_prices": [],
                "daily_returns": [],
                "volatility": 0.0,
                "price_trend": "flat",
                "rsi_14": 50.0,
                "vwap": 0.0,
                "momentum": 0.0,
                "rolling_volatility_5d": 0.0,
                "rolling_volatility_10d": 0.0,
            }

        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]

        # Daily returns
        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
        ]

        # Volatility (annualized)
        vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0

        # Rolling volatility
        rv5 = statistics.stdev(returns[-5:]) * math.sqrt(252) if len(returns) >= 5 else vol
        rv10 = statistics.stdev(returns[-10:]) * math.sqrt(252) if len(returns) >= 10 else vol

        # Price trend (last 10 days)
        trend = "flat"
        if len(closes) >= 10:
            slope = closes[-1] - closes[-10]
            if slope > closes[-10] * 0.005:
                trend = "up"
            elif slope < -closes[-10] * 0.005:
                trend = "down"

        # RSI-14
        rsi = self._compute_rsi(closes, 14)

        # VWAP (volume-weighted average price, all history)
        total_vol = sum(volumes)
        vwap = (
            sum(c["close"] * c["volume"] for c in candles) / total_vol
            if total_vol > 0
            else closes[-1]
        )

        # Momentum: 20-day rate of change
        momentum = 0.0
        if len(closes) >= 21:
            momentum = (closes[-1] - closes[-21]) / closes[-21]

        return {
            "close_prices": closes,
            "daily_returns": returns,
            "volatility": round(vol, 6),
            "price_trend": trend,
            "rsi_14": round(rsi, 2),
            "vwap": round(vwap, 4),
            "momentum": round(momentum, 6),
            "rolling_volatility_5d": round(rv5, 6),
            "rolling_volatility_10d": round(rv10, 6),
        }

    def _compute_rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── Options Feature Computation ────────────────────────────────────────────

    def _compute_options_features(
        self, chain: dict[str, Any], quote: dict[str, Any]
    ) -> dict[str, Any]:
        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        total_call_vol = 0
        total_put_vol = 0
        iv_values: list[float] = []
        atm_iv = 0.0
        q = quote.get("quote") or quote.get("extended") or quote
        last_price = float(q.get("lastPrice", 0) or q.get("mark", 0) or 0)

        for exp_key, strikes in call_map.items():
            for strike_str, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    total_call_vol += int(c.get("totalVolume", 0) or 0)
                    iv = float(c.get("volatility", 0) or 0)
                    if iv > 0:
                        iv_values.append(iv)
                    # ATM detection
                    if last_price and abs(float(strike_str) - last_price) < 1.0:
                        atm_iv = iv

        for exp_key, strikes in put_map.items():
            for strike_str, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    total_put_vol += int(c.get("totalVolume", 0) or 0)
                    iv = float(c.get("volatility", 0) or 0)
                    if iv > 0:
                        iv_values.append(iv)

        total_vol = total_call_vol + total_put_vol
        call_put_ratio = (total_call_vol / total_put_vol) if total_put_vol > 0 else 1.0
        avg_iv = statistics.mean(iv_values) if iv_values else 0.0

        # IV Rank: simplified (0-100 relative to chain average)
        iv_rank = min(100.0, max(0.0, avg_iv * 100)) if avg_iv < 1 else min(100.0, avg_iv)

        # Flow bias
        if call_put_ratio > 1.2:
            flow_bias = "bullish"
        elif call_put_ratio < 0.8:
            flow_bias = "bearish"
        else:
            flow_bias = "neutral"

        return {
            "iv_rank": round(iv_rank, 2),
            "call_put_ratio": round(call_put_ratio, 4),
            "atm_iv": round(atm_iv, 4),
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "avg_iv": round(avg_iv, 4),
            "flow_bias": flow_bias,
        }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    # Load .env manually for standalone run
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    svc = SchwabCollectorService()
    result = svc.collect("SPY")
    print(json.dumps({
        "source": result["source"],
        "symbol": result["symbol"],
        "last_price": result["quote"].get("lastPrice"),
        "price_trend": result["price_rich"]["price_trend"],
        "rsi_14": result["price_rich"]["rsi_14"],
        "momentum": result["price_rich"]["momentum"],
        "options_features": result["options_features"],
        "candle_count": len(result["price_rich"]["close_prices"]),
    }, indent=2))
