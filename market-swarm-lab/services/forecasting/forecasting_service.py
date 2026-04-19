"""
Forecasting service with TimesFM 2.5 (PyTorch) and a local fallback.

TimesFM 2.5 is loaded lazily on first call.
If the model or its dependencies are unavailable, the service falls back to
a deterministic local forecaster that still returns the full output schema.

Quantile mapping from TimesFM 2.5 output:
  quantile_forecast shape = (n_series, horizon, 10)
  columns:  [mean, q10, q20, q30, q40, q50, q60, q70, q80, q90]
"""
from __future__ import annotations

import json
import math
import os
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Any

_timesfm_error: str | None = None
_timesfm_model = None
_timesfm_config_cls = None

ENABLE_TIMESFM = os.getenv("ENABLE_TIMESFM", "false").lower() == "true"
_ROOT = Path(__file__).resolve().parents[2]


def _load_timesfm():
    global _timesfm_model, _timesfm_config_cls, _timesfm_error
    if _timesfm_model is not None:
        return True
    if _timesfm_error is not None:
        return False
    try:
        import torch  # noqa: F401
        import numpy as np  # noqa: F401
        import timesfm  # noqa: F401

        torch.set_float32_matmul_precision("high")
        _timesfm_config_cls = timesfm.ForecastConfig
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch"
        )
        model.compile(
            timesfm.ForecastConfig(
                max_context=1024,
                max_horizon=256,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
        _timesfm_model = model
        return True
    except Exception as exc:
        _timesfm_error = str(exc)
        return False


class TimesFMForecastingService:
    def forecast(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """Called from the workflow pipeline."""
        window = normalized_bundle["feature_window"]
        closes = [row["close"] for row in window]
        series = {
            "close": closes,
            "volume": [row["volume"] for row in window],
            "vwap": [row["vwap"] for row in window],
            "rsi": [row["rsi"] for row in window],
        }
        latest = window[-1]
        result = self._run_forecast(ticker=ticker, series=series, horizon=5)
        # Augment with pipeline-specific keys the reporting/simulation layers expect
        result["latest_close"] = latest["close"]
        result["delta_5d"] = round(result["forecast"][-1] - latest["close"], 2)
        result["drivers"] = {
            "short_trend": round(latest["close"] - mean(closes[-5:]) if len(closes) >= 5 else 0, 2),
            "reddit_impulse": round(
                (latest["reddit_bullish_ratio"] - latest["reddit_bearish_ratio"])
                * latest["close"]
                * 0.01,
                2,
            ),
            "reddit_avg_sentiment": latest["reddit_avg_sentiment"],
            "reddit_mentions": latest["reddit_mentions"],
            "rsi_drag": round((latest["rsi"] - 50) * 0.03, 2),
        }
        result["forecast_close_1d"] = round(result["forecast"][0], 2)
        result["forecast_close_5d"] = round(result["forecast"][-1], 2)
        result["timesfm_inputs_used"] = normalized_bundle["timesfm_inputs"]
        return result

    def forecast_from_prices(
        self,
        ticker: str,
        close_prices: list[float],
        horizon: int = 5,
    ) -> dict[str, Any]:
        """Forecast directly from raw close prices, bypassing feature_window."""
        series = {"close": close_prices}
        result = self._run_forecast(ticker=ticker, series=series, horizon=horizon)

        _dir_map = {"up": "bullish", "down": "bearish", "sideways": "neutral"}
        direction = _dir_map.get(result.get("direction", "sideways"), "neutral")

        forecast_pts = result.get("forecast", [])
        last_close = close_prices[-1] if close_prices else 0.0
        predicted_return = 0.0
        if last_close and forecast_pts:
            predicted_return = round((forecast_pts[-1] - last_close) / last_close, 6)

        confidence = result.get("confidence", 0.5)
        trend_strength = round(abs(predicted_return) * confidence, 4)

        forecast_deviation = 0.0
        if len(forecast_pts) > 1:
            forecast_deviation = round(stdev(forecast_pts), 4)

        output: dict[str, Any] = {
            "ticker": result["ticker"],
            "provider_mode": result["provider_mode"],
            "direction": direction,
            "predicted_return": predicted_return,
            "confidence": confidence,
            "forecast": forecast_pts,
            "quantiles": result.get("quantiles", {"p10": [], "p50": [], "p90": []}),
            "trend_strength": trend_strength,
            "forecast_deviation": forecast_deviation,
        }

        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "ohlcv"
        raw_dir.mkdir(parents=True, exist_ok=True)

        try:
            with open(raw_dir / f"{ticker.upper()}_timesfm_input_{today_str}.json", "w") as f:
                json.dump(
                    {"ticker": ticker, "close_prices": close_prices, "horizon": horizon, "date": today_str},
                    f, indent=2,
                )
        except Exception:
            pass

        try:
            with open(raw_dir / f"{ticker.upper()}_timesfm_output_{today_str}.json", "w") as f:
                json.dump(output, f, indent=2)
        except Exception:
            pass

        return output

    def run_api(
        self,
        ticker: str,
        series: dict[str, list[float]],
        horizon: int,
    ) -> dict[str, Any]:
        """Called directly from POST /forecast."""
        return self._run_forecast(ticker=ticker, series=series, horizon=horizon)

    def _run_forecast(
        self,
        ticker: str,
        series: dict[str, list[float]],
        horizon: int,
    ) -> dict[str, Any]:
        closes = series.get("close", [])
        if ENABLE_TIMESFM and _load_timesfm():
            try:
                return self._timesfm_forecast(ticker, series, horizon, closes)
            except Exception as exc:
                return self._fallback_forecast(
                    ticker, closes, horizon,
                    provider_mode="timesfm_runtime_error_fallback",
                    error=str(exc),
                )
        return self._fallback_forecast(ticker, closes, horizon)

    # -------------------------------------------------------- TimesFM path

    def _timesfm_forecast(
        self,
        ticker: str,
        series: dict[str, list[float]],
        horizon: int,
        closes: list[float],
    ) -> dict[str, Any]:
        import numpy as np

        inputs = [np.array(closes, dtype=np.float32)]
        point_forecast, quantile_forecast = _timesfm_model.forecast(
            horizon=horizon,
            inputs=inputs,
        )
        # point_forecast: (1, horizon)
        # quantile_forecast: (1, horizon, 10) -> [mean, q10..q90]
        pts = [round(float(v), 4) for v in point_forecast[0]]
        q10 = [round(float(v), 4) for v in quantile_forecast[0, :, 1]]
        q50 = [round(float(v), 4) for v in quantile_forecast[0, :, 5]]
        q90 = [round(float(v), 4) for v in quantile_forecast[0, :, 9]]
        direction, confidence = _derive_direction(closes[-1] if closes else 0.0, pts)
        return {
            "ticker": ticker.upper(),
            "provider_mode": "timesfm_2p5_pytorch",
            "horizon": horizon,
            "forecast": pts,
            "quantiles": {"p10": q10, "p50": q50, "p90": q90},
            "direction": direction,
            "confidence": confidence,
        }

    # -------------------------------------------------------- fallback path

    def _fallback_forecast(
        self,
        ticker: str,
        closes: list[float],
        horizon: int,
        provider_mode: str = "local_fallback",
        error: str | None = None,
    ) -> dict[str, Any]:
        if len(closes) < 2:
            closes = closes + [100.0] * max(0, 2 - len(closes))

        last = closes[-1]
        recent = closes[-min(5, len(closes)):]
        trend_per_step = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
        vol = stdev(closes[-min(10, len(closes)):]) if len(closes) > 1 else last * 0.01

        pts = [round(last + trend_per_step * (i + 1), 2) for i in range(horizon)]
        q10 = [round(p - 1.28 * vol, 2) for p in pts]
        q50 = pts
        q90 = [round(p + 1.28 * vol, 2) for p in pts]
        direction, confidence = _derive_direction(last, pts)

        result: dict[str, Any] = {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "horizon": horizon,
            "forecast": pts,
            "quantiles": {"p10": q10, "p50": q50, "p90": q90},
            "direction": direction,
            "confidence": confidence,
        }
        if error:
            result["fallback_reason"] = error
        return result


# ------------------------------------------------------------------ helpers

def _derive_direction(
    last_close: float,
    forecast: list[float],
) -> tuple[str, float]:
    if not forecast:
        return "sideways", 0.5
    final = forecast[-1]
    delta_pct = (final - last_close) / max(abs(last_close), 1e-9)
    if delta_pct > 0.005:
        direction = "up"
    elif delta_pct < -0.005:
        direction = "down"
    else:
        direction = "sideways"
    # Confidence scales with magnitude, capped at 0.95
    confidence = round(min(0.95, 0.5 + abs(delta_pct) * 8), 3)
    return direction, confidence
