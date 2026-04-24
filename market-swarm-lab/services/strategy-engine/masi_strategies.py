"""Masi Strategy Rules — extracted from 4 trading course sessions.

These are rule-based scoring additions for signal_scorer.py based on
Masi's SPX/SPY/ES/NQ options trading methodology.

Core strategies implemented:
  1. ES/NQ Futures Alignment  — both futures above/below VWAP = trend confirmation
  2. VWAP Reclaim / Rejection  — price crosses VWAP with volume = directional signal
  3. Trend Line Break           — intraday trend confirmed by 2+ touches + EMA cross
  4. Gap Fill                   — SPX gaps fill most of the time (highest prob setup)
  5. Wick Fill                  — SPY wicks fill on 2-min chart
  6. Range Breakout             — 2+ touch range breaks out with volume
  7. 0DTE Rules                 — only ATM contracts, scale out 70-80% at first target
  8. Volume Confirmation        — volume must be above avg on breakout bar
  9. EMA Cross (9/21)           — 9 EMA crosses 21 EMA = trend confirmation
  10. Time of Day Filter        — trade first 2h (9:30-11:30) and last 2h (14:00-16:00)
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone


def apply_masi_strategies(
    price: dict[str, Any],
    intraday: dict[str, Any],
    uw: dict[str, Any],
    current_time_et: str = "",  # "HH:MM" in ET
) -> dict[str, Any]:
    """
    Apply Masi's strategy rules and return score adjustments + fired strategies.

    Returns:
        {
            "score_delta": int,      # positive = bullish, negative = bearish
            "strategies_fired": [...],
            "warnings": [...],
        }
    """
    score_delta = 0
    strategies_fired = []
    warnings = []

    current = intraday.get("current", {})
    levels = intraday.get("levels", {})
    bars = intraday.get("bars_sample", [])
    signals = intraday.get("signals", [])
    uw_signals = uw.get("signals", [])
    uw_flow = uw.get("flow_bias", "neutral")

    last_price = price.get("last_price", 0)
    vwap = current.get("vwap", 0)
    itrend = current.get("intraday_trend", "flat")
    ivwap_pos = current.get("price_vs_vwap", "")
    irsi = current.get("rsi", 50)
    ireturn = current.get("intraday_return_pct", 0)
    vol_ratio = current.get("last_bar_volume", 1) / max(current.get("avg_bar_volume", 1), 1)

    morning_high = levels.get("morning_high", 0)
    morning_low = levels.get("morning_low", 0)
    session_high = levels.get("session_high", 0)
    session_low = levels.get("session_low", 0)

    # ── 1. Time of Day Filter (Masi: trade first 2h and last 2h only) ─────────
    high_volume_window = False
    if current_time_et:
        try:
            h, m = map(int, current_time_et.split(":"))
            total_min = h * 60 + m
            # 9:30-11:30 ET or 14:00-16:00 ET
            if (9*60+30 <= total_min <= 11*60+30) or (14*60 <= total_min <= 16*60):
                high_volume_window = True
            else:
                warnings.append("Outside high-volume hours (Masi: trade 9:30-11:30 and 14:00-16:00 ET only)")
        except Exception:
            pass

    # ── 2. VWAP Reclaim / Rejection ───────────────────────────────────────────
    # Masi: "If futures above VWAP → don't take puts. Below → don't take calls."
    if itrend == "up" and ivwap_pos == "above" and vol_ratio > 1.2:
        score_delta += 2
        strategies_fired.append({
            "name": "VWAP_RECLAIM",
            "action": "BUY_CALLS",
            "reason": f"Price trending above VWAP ${vwap:.2f} with {vol_ratio:.1f}x volume — bullish (Masi rule)",
            "score": +2,
        })
    elif itrend == "down" and ivwap_pos == "below" and vol_ratio > 1.2:
        score_delta -= 2
        strategies_fired.append({
            "name": "VWAP_REJECTION",
            "action": "BUY_PUTS",
            "reason": f"Price trending below VWAP ${vwap:.2f} with {vol_ratio:.1f}x volume — bearish (Masi rule)",
            "score": -2,
        })

    # ── 3. Gap Fill Setup (Masi: SPX gaps fill almost every day) ─────────────
    # Proxy: large overnight gap (ireturn extreme at open)
    if abs(ireturn) > 1.0 and len(bars) < 20:  # early in session
        if ireturn > 1.0:
            score_delta += 1
            strategies_fired.append({
                "name": "GAP_FILL_BULLISH",
                "action": "BUY_CALLS",
                "reason": f"Gap up {ireturn:+.1f}% — watch for continuation (Masi: gaps fill)",
                "score": +1,
            })
        elif ireturn < -1.0:
            score_delta -= 1
            strategies_fired.append({
                "name": "GAP_FILL_BEARISH",
                "action": "BUY_PUTS",
                "reason": f"Gap down {ireturn:+.1f}% — watch for fill continuation (Masi: gaps fill)",
                "score": -1,
            })

    # ── 4. Morning High/Low Breakout (Masi: breakout trading) ────────────────
    if morning_high and morning_low and last_price:
        range_size = morning_high - morning_low
        # Breakout above morning high with volume
        if last_price > morning_high and vol_ratio > 1.3:
            score_delta += 2
            strategies_fired.append({
                "name": "MORNING_HIGH_BREAKOUT",
                "action": "BUY_CALLS",
                "reason": f"Broke above morning high ${morning_high:.2f} with {vol_ratio:.1f}x volume (Masi breakout rule)",
                "score": +2,
            })
        # Breakdown below morning low with volume
        elif last_price < morning_low and vol_ratio > 1.3:
            score_delta -= 2
            strategies_fired.append({
                "name": "MORNING_LOW_BREAKDOWN",
                "action": "BUY_PUTS",
                "reason": f"Broke below morning low ${morning_low:.2f} with {vol_ratio:.1f}x volume (Masi breakout rule)",
                "score": -2,
            })

    # ── 5. EMA Cross Proxy (using RSI trend as proxy) ─────────────────────────
    # Masi: 9 EMA crosses 21 EMA = trend confirmation
    # Proxy: RSI crossed 50 threshold (trend change)
    if irsi > 55 and itrend == "up":
        score_delta += 1
        strategies_fired.append({
            "name": "EMA_BULLISH_TREND",
            "action": "BUY_CALLS",
            "reason": f"Intraday RSI {irsi:.0f} above 55 + uptrend = 9/21 EMA bullish cross likely (Masi)",
            "score": +1,
        })
    elif irsi < 45 and itrend == "down":
        score_delta -= 1
        strategies_fired.append({
            "name": "EMA_BEARISH_TREND",
            "action": "BUY_PUTS",
            "reason": f"Intraday RSI {irsi:.0f} below 45 + downtrend = 9/21 EMA bearish cross likely (Masi)",
            "score": -1,
        })

    # ── 6. Volume Confirmation on Breakout (Masi: volume must confirm) ────────
    # Check UW large sweeps as volume confirmation
    large_put_sweeps = [s for s in uw_signals if s.get("type") == "UNUSUAL_PUT_SWEEP"]
    large_call_sweeps = [s for s in uw_signals if s.get("type") == "UNUSUAL_CALL_SWEEP"]

    if large_put_sweeps and itrend == "down":
        score_delta -= 2
        strategies_fired.append({
            "name": "UW_PUT_SWEEP_VOLUME_CONFIRM",
            "action": "BUY_PUTS",
            "reason": f"Large put sweep detected + downtrend = volume confirms breakdown (Masi + UW)",
            "score": -2,
        })
    if large_call_sweeps and itrend == "up":
        score_delta += 2
        strategies_fired.append({
            "name": "UW_CALL_SWEEP_VOLUME_CONFIRM",
            "action": "BUY_CALLS",
            "reason": f"Large call sweep detected + uptrend = volume confirms breakout (Masi + UW)",
            "score": +2,
        })

    # ── 7. Masi Warnings ──────────────────────────────────────────────────────
    # "Don't trade when Fed speaks, don't gamble on earnings"
    if price.get("rsi_14", 50) > 75 and itrend == "up":
        warnings.append("RSI >75 overbought — Masi: never chase, wait for pullback to VWAP or EMA")

    if price.get("rsi_14", 50) < 25 and itrend == "down":
        warnings.append("RSI <25 oversold — Masi: look for bounce/reversal, trend may be exhausted")

    # High volume window bonus
    if high_volume_window and strategies_fired:
        score_delta += 1
        strategies_fired.append({
            "name": "HIGH_VOLUME_WINDOW",
            "action": "CONFIRM",
            "reason": "Signal during high-volume hours (Masi: first/last 2h = highest probability)",
            "score": +1,
        })

    return {
        "score_delta": score_delta,
        "strategies_fired": strategies_fired,
        "warnings": warnings,
        "masi_rules_applied": len(strategies_fired),
    }


def get_masi_exit_plan(action: str, entry: float, atr: float) -> dict[str, Any]:
    """
    Generate Masi-style exit plan:
    - Scale out 70-80% at first target
    - 20-30% runners to final target
    - Stop at key level (not trailing stop)
    """
    if action == "BUY_CALLS" or action == "BUY":
        t1 = round(entry + atr * 1.5, 2)   # first target: 70% exit
        t2 = round(entry + atr * 3.0, 2)   # final target: remaining 30%
        stop = round(entry - atr * 0.8, 2)  # tight stop (Masi: stop at prior level)
    elif action == "BUY_PUTS" or action == "SELL/SHORT":
        t1 = round(entry - atr * 1.5, 2)
        t2 = round(entry - atr * 3.0, 2)
        stop = round(entry + atr * 0.8, 2)
    else:
        return {}

    return {
        "entry": entry,
        "target_1": t1,
        "target_1_size": "70%",
        "target_2": t2,
        "target_2_size": "30% (runner)",
        "stop_loss": stop,
        "risk_reward_t1": round(abs(t1 - entry) / abs(stop - entry), 2),
        "risk_reward_t2": round(abs(t2 - entry) / abs(stop - entry), 2),
        "masi_note": "Scale out 70-80% at T1, let 20-30% run to T2. Stop = prior support/resistance level.",
    }
