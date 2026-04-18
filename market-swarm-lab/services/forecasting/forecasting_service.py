from __future__ import annotations

import os
from statistics import mean
from typing import Any


class TimesFMForecastingService:
    def __init__(self) -> None:
        self.enable_timesfm = os.getenv("ENABLE_TIMESFM", "false").lower() == "true"

    def forecast(self, ticker: str, normalized_bundle: dict[str, Any]) -> dict[str, Any]:
        if self.enable_timesfm:
            try:
                import timesfm  # type: ignore  # noqa: F401
                provider_mode = "timesfm_adapter"
            except Exception:
                provider_mode = "timesfm_unavailable_fallback"
        else:
            provider_mode = "local_fallback"

        window = normalized_bundle["feature_window"]
        closes = [row["close"] for row in window]
        latest = window[-1]
        recent_mean = mean(closes[-5:]) if len(closes) >= 5 else mean(closes)
        short_trend = latest["close"] - recent_mean
        reddit_impulse = (latest["reddit_bullish_ratio"] - latest["reddit_bearish_ratio"]) * latest["close"] * 0.01
        sentiment_bias = latest["reddit_avg_sentiment"] * latest["close"] * 0.02
        rsi_drag = (latest["rsi"] - 50) * 0.03

        day1 = round(latest["close"] + short_trend * 0.4 + reddit_impulse + sentiment_bias - rsi_drag, 2)
        day5 = round(latest["close"] + short_trend * 0.8 + reddit_impulse * 1.5 + sentiment_bias * 1.2 - rsi_drag * 1.4, 2)
        delta = round(day5 - latest["close"], 2)
        confidence = round(min(0.87, 0.45 + abs(latest["reddit_avg_sentiment"]) * 0.2 + abs(short_trend) / max(latest["close"], 1)), 2)
        direction = "up" if delta >= 0 else "down"

        return {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "direction": direction,
            "confidence": confidence,
            "latest_close": latest["close"],
            "forecast_close_1d": day1,
            "forecast_close_5d": day5,
            "delta_5d": delta,
            "drivers": {
                "short_trend": round(short_trend, 2),
                "reddit_impulse": round(reddit_impulse, 2),
                "reddit_avg_sentiment": latest["reddit_avg_sentiment"],
                "reddit_mentions": latest["reddit_mentions"],
                "rsi_drag": round(rsi_drag, 2),
            },
            "timesfm_inputs_used": normalized_bundle["timesfm_inputs"],
        }
