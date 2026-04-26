"""TechnicalConfirmationEngine — VWAP + EMA technical setup analysis.

Uses Schwab intraday bars (5-min) to evaluate:
  - Price vs VWAP (above/reclaim = bullish, below/rejection = bearish)
  - Price vs 9 EMA and 21 EMA (alignment)
  - Higher lows / lower highs (trend quality)
  - Volume trend (confirmation)
  - RSI (not overbought/oversold extremes)

Output:
  - setup_status: "ready" | "watchlist" | "skip"
  - trigger_level: breakout entry level
  - invalidation_level: stop/skip level
  - technical_score: 0-100
  - direction: "bullish" | "bearish" | "neutral"

VWAP reclaim rule: only valid if time_of_reclaim before 11:30 AM ET.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]

_SCHWAB_DIR = str(_ROOT / "services" / "schwab-collector")
if _SCHWAB_DIR not in sys.path:
    sys.path.insert(0, _SCHWAB_DIR)


# ── Indicator Helpers ─────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return round(e, 4)


def _vwap(bars: list[dict]) -> float:
    cv = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars)
    vol = sum(b["volume"] for b in bars)
    return round(cv / vol, 4) if vol > 0 else (bars[-1]["close"] if bars else 0.0)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _higher_lows(bars: list[dict], lookback: int = 5) -> bool:
    lows = [b["low"] for b in bars[-lookback:]]
    return all(lows[i] >= lows[i - 1] for i in range(1, len(lows)))


def _lower_highs(bars: list[dict], lookback: int = 5) -> bool:
    highs = [b["high"] for b in bars[-lookback:]]
    return all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))


def _volume_increasing(bars: list[dict], lookback: int = 5) -> bool:
    if len(bars) < lookback * 2:
        return False
    recent_avg = sum(b["volume"] for b in bars[-lookback:]) / lookback
    prior_avg = sum(b["volume"] for b in bars[-lookback * 2: -lookback]) / lookback
    return recent_avg > prior_avg * 1.1


def _vwap_reclaim_time(bars: list[dict], vwap: float) -> str | None:
    """Return HH:MM of first VWAP reclaim, or None."""
    below = False
    for b in bars:
        if b["close"] < vwap:
            below = True
        elif below and b["close"] >= vwap:
            return b.get("dt", "")
    return None


def _parse_time_str(t: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 999


# ── TechnicalConfirmationEngine ───────────────────────────────────────────────

class TechnicalConfirmationEngine:
    """Evaluate intraday technical setup for a sympathy candidate."""

    def __init__(self) -> None:
        self._intraday_svc = None
        self._load_intraday()

    def _load_intraday(self) -> None:
        try:
            from schwab_intraday_service import SchwabIntradayService
            self._intraday_svc = SchwabIntradayService(frequency_minutes=5)
        except Exception as exc:
            _log.warning("[tech_confirmation] SchwabIntradayService unavailable: %s", exc)

    def analyze(
        self,
        ticker: str,
        current_price: float = 0.0,
        direction_bias: str = "neutral",  # "bullish" | "bearish" | "neutral"
    ) -> dict[str, Any]:
        """Return technical setup for ticker.

        direction_bias from upstream (options flow or expected catalyst direction).
        """
        ticker = ticker.upper()
        bars: list[dict] = []

        if self._intraday_svc:
            try:
                bars = self._intraday_svc.fetch_bars(ticker, days=1)
            except Exception as exc:
                _log.warning("[tech_confirmation] bars fetch failed for %s: %s", ticker, exc)

        if not bars or len(bars) < 5:
            return self._fallback_result(ticker, current_price, direction_bias)

        closes = [b["close"] for b in bars]
        current = closes[-1] if closes else current_price
        vwap = _vwap(bars)
        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        rsi = _rsi(closes)

        morning_high = max(b["high"] for b in bars[:6]) if len(bars) >= 6 else current
        morning_low = min(b["low"] for b in bars[:6]) if len(bars) >= 6 else current

        above_vwap = current >= vwap
        above_ema9 = current >= ema9
        above_ema21 = current >= ema21
        ema_bullish = ema9 >= ema21
        vol_increasing = _volume_increasing(bars)
        is_higher_lows = _higher_lows(bars)
        is_lower_highs = _lower_highs(bars)

        # VWAP reclaim
        reclaim_time = _vwap_reclaim_time(bars, vwap)
        valid_reclaim = False
        if reclaim_time:
            reclaim_mins = _parse_time_str(reclaim_time)
            valid_reclaim = reclaim_mins <= (11 * 60 + 30)  # before 11:30 AM

        # Score components
        if direction_bias == "bullish" or direction_bias == "neutral":
            score = self._bullish_score(
                above_vwap, valid_reclaim, above_ema9, above_ema21,
                ema_bullish, is_higher_lows, vol_increasing, rsi,
                current, morning_high, morning_low, vwap,
            )
            trigger = round(morning_high * 1.001, 2)
            invalidation = round(max(morning_low * 0.995, vwap * 0.99), 2)
            direction = "bullish"
        else:
            score = self._bearish_score(
                above_vwap, above_ema9, above_ema21,
                ema_bullish, is_lower_highs, vol_increasing, rsi,
                current, morning_high, morning_low, vwap,
            )
            trigger = round(morning_low * 0.999, 2)
            invalidation = round(min(morning_high * 1.005, vwap * 1.01), 2)
            direction = "bearish"

        # Setup status
        if score >= 70:
            setup_status = "ready"
        elif score >= 45:
            setup_status = "watchlist"
        else:
            setup_status = "skip"

        reasons = self._build_reasons(
            above_vwap, valid_reclaim, above_ema9, above_ema21,
            ema_bullish, is_higher_lows, vol_increasing, rsi, direction,
        )

        return {
            "ticker": ticker,
            "setup_status": setup_status,
            "direction": direction,
            "technical_score": score,
            "trigger_level": trigger,
            "invalidation_level": invalidation,
            "current_price": round(current, 2),
            "vwap": round(vwap, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "rsi": rsi,
            "morning_high": round(morning_high, 2),
            "morning_low": round(morning_low, 2),
            "above_vwap": above_vwap,
            "vwap_reclaim": valid_reclaim,
            "above_ema9": above_ema9,
            "above_ema21": above_ema21,
            "volume_increasing": vol_increasing,
            "reason": "; ".join(reasons) if reasons else "no strong signal",
            "bars_available": len(bars),
        }

    # ── Directional Scoring ────────────────────────────────────────────────────

    def _bullish_score(
        self, above_vwap, valid_reclaim, above_ema9, above_ema21,
        ema_bullish, higher_lows, vol_increasing, rsi,
        current, morning_high, morning_low, vwap,
    ) -> int:
        score = 30  # base
        if above_vwap:      score += 20
        if valid_reclaim:   score += 15  # VWAP reclaim before 11:30
        if above_ema9:      score += 10
        if above_ema21:     score += 8
        if ema_bullish:     score += 7
        if higher_lows:     score += 10
        if vol_increasing:  score += 8
        if 40 <= rsi <= 65: score += 5    # healthy RSI, not overbought
        if rsi > 70:        score -= 10   # overbought penalty
        if rsi < 30:        score -= 5    # oversold (might bounce, but risky)
        # Near key level
        if morning_high > 0 and abs(current - morning_high) / morning_high < 0.005:
            score += 10  # testing premarket high = breakout potential
        return min(100, max(0, score))

    def _bearish_score(
        self, above_vwap, above_ema9, above_ema21,
        ema_bullish, lower_highs, vol_increasing, rsi,
        current, morning_high, morning_low, vwap,
    ) -> int:
        score = 30
        if not above_vwap:  score += 20
        if not above_ema9:  score += 10
        if not above_ema21: score += 8
        if not ema_bullish: score += 7
        if lower_highs:     score += 10
        if vol_increasing:  score += 8
        if 35 <= rsi <= 60: score += 5
        if rsi < 30:        score -= 10  # oversold = too late for puts
        if rsi > 70:        score -= 5
        if morning_low > 0 and abs(current - morning_low) / morning_low < 0.005:
            score += 10  # testing morning low = breakdown potential
        return min(100, max(0, score))

    def _build_reasons(
        self, above_vwap, valid_reclaim, above_ema9, above_ema21,
        ema_bullish, higher_lows, vol_increasing, rsi, direction,
    ) -> list[str]:
        reasons = []
        if direction == "bullish":
            if above_vwap:      reasons.append("price above VWAP")
            if valid_reclaim:   reasons.append("VWAP reclaim before 11:30")
            if above_ema9 and above_ema21: reasons.append("above 9/21 EMA")
            if higher_lows:     reasons.append("higher lows pattern")
            if vol_increasing:  reasons.append("rising volume")
        else:
            if not above_vwap:  reasons.append("price below VWAP")
            if not above_ema9:  reasons.append("below 9 EMA")
            if not ema_bullish: reasons.append("9 EMA below 21 EMA")
            if vol_increasing:  reasons.append("rising volume on decline")
        if rsi > 70:            reasons.append("RSI overbought caution")
        if rsi < 30:            reasons.append("RSI oversold caution")
        return reasons

    def _fallback_result(
        self, ticker: str, current_price: float, direction_bias: str
    ) -> dict[str, Any]:
        """Return neutral watchlist result when no intraday data is available."""
        trigger = round(current_price * 1.005, 2) if current_price > 0 else 0.0
        invalidation = round(current_price * 0.985, 2) if current_price > 0 else 0.0
        return {
            "ticker": ticker,
            "setup_status": "watchlist",
            "direction": direction_bias if direction_bias != "neutral" else "bullish",
            "technical_score": 45,
            "trigger_level": trigger,
            "invalidation_level": invalidation,
            "current_price": round(current_price, 2),
            "vwap": 0.0,
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 50.0,
            "morning_high": 0.0,
            "morning_low": 0.0,
            "above_vwap": None,
            "vwap_reclaim": False,
            "above_ema9": None,
            "above_ema21": None,
            "volume_increasing": None,
            "reason": "no intraday data — default watchlist",
            "bars_available": 0,
        }
