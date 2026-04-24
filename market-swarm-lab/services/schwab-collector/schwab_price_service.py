"""SchwabPriceService — drop-in replacement for PriceService / PriceCollectorService.

Matches the output schema expected by the normalizer, agent-seeder, and
strategy-engine so it can replace Alpha Vantage / Massive.com with zero
changes to downstream services.

Output schema (same as PriceService.collect()):
{
    "ticker": str,
    "provider_mode": "schwab_live" | "schwab_fixture",
    "series": [{"timestamp", "open", "high", "low", "close", "volume"}, ...],
    "close_prices": [...],
    "returns": [...],
    "daily_returns": [...],
    "volatility": float,
    "avg_volume": float,
    "price_trend": "up"|"down"|"flat",
    "rsi_14": float,
    "vwap": float,
    "momentum": float,
    "rolling_volatility_5d": float,
    "rolling_volatility_10d": float,
    "quote": { lastPrice, bidPrice, askPrice, ... },
    "options_features": { iv_rank, call_put_ratio, atm_iv, flow_bias, ... },
    "source_audit": { "ohlcv": {...} }
}
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

# Auto-load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from schwab_collector_service import SchwabCollectorService

_log = logging.getLogger(__name__)


class SchwabPriceService:
    """Schwab-backed price service. Drop-in for PriceService/PriceCollectorService."""

    def __init__(self) -> None:
        self._svc = SchwabCollectorService()

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        data = self._svc.collect(ticker)

        price_rich = data.get("price_rich", {})
        quote = data.get("quote", {})
        q = quote.get("quote") or quote.get("extended") or quote
        options_features = data.get("options_features", {})

        close_prices = price_rich.get("close_prices", [])
        returns = price_rich.get("daily_returns", [])
        avg_volume = 0.0

        # Build series in standard format
        series = [
            {"timestamp": str(i), "open": c, "high": c, "low": c, "close": c, "volume": 0}
            for i, c in enumerate(close_prices)
        ]

        last_price = float(q.get("lastPrice") or q.get("mark") or
                          (close_prices[-1] if close_prices else 0))

        source = data.get("source", "schwab_fixture")

        return {
            "ticker": ticker,
            "provider_mode": source,
            "series": series,
            "close_prices": close_prices,
            "returns": returns,
            "daily_returns": returns,
            "volatility": price_rich.get("volatility", 0.0),
            "avg_volume": avg_volume,
            "price_trend": price_rich.get("price_trend", "flat"),
            "rsi_14": price_rich.get("rsi_14", 50.0),
            "vwap": price_rich.get("vwap", 0.0),
            "momentum": price_rich.get("momentum", 0.0),
            "rolling_volatility_5d": price_rich.get("rolling_volatility_5d", 0.0),
            "rolling_volatility_10d": price_rich.get("rolling_volatility_10d", 0.0),
            "last_price": last_price,
            "quote": quote,
            "options_features": options_features,
            "source_audit": {
                "ohlcv": {
                    "status": "live" if source == "schwab_live" else "fallback",
                    "provider": "schwab",
                    "record_count": len(close_prices),
                    "date_range": {
                        "from": series[0]["timestamp"] if series else "",
                        "to": series[-1]["timestamp"] if series else "",
                    },
                }
            },
        }

    # Alias for compatibility with PriceService.features()
    def features(self, ticker: str) -> dict[str, Any]:
        return self.collect(ticker)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    svc = SchwabPriceService()
    result = svc.collect("SPY")
    print(json.dumps({
        "provider_mode": result["provider_mode"],
        "last_price": result["last_price"],
        "price_trend": result["price_trend"],
        "rsi_14": result["rsi_14"],
        "momentum": result["momentum"],
        "candles": len(result["close_prices"]),
        "options_features": result["options_features"],
    }, indent=2))
