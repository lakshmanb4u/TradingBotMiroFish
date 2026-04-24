"""MiroFish Signal Scorer — decisive BUY / SELL / HOLD with entry, target, stop.

Replaces the old alignment-based strategy engine with a weighted scoring system
that always produces a clear directional signal.

Score interpretation:
  >= +4  → BUY  (high confidence)
  +2..+3 → BUY  (moderate confidence)
  -1..+1 → HOLD
  -2..-3 → SELL/SHORT (moderate confidence)
  <= -4  → SELL/SHORT (high confidence)
"""
from __future__ import annotations

from typing import Any


def score_ticker(
    price: dict[str, Any],
    intraday: dict[str, Any],
    uw: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    """Compute a decisive signal from all data layers.

    Args:
        price:    Output of SchwabPriceService.collect()
        intraday: Output of SchwabIntradayService.collect()
        uw:       Output of UWCollectorService.collect()
        forecast: Output of TimesFMForecastingService.forecast_from_prices()

    Returns:
        Signal dict with ACTION, entry, target, stop_loss, risk_reward, confidence, why.
    """
    last    = price["last_price"]
    rsi     = price["rsi_14"]
    mom     = price["momentum"]
    vol     = price["volatility"]

    itrend  = intraday["current"]["intraday_trend"]
    ivwap   = intraday["current"]["price_vs_vwap"]
    irsi    = intraday["current"]["rsi"]
    ireturn = intraday["current"]["intraday_return_pct"]

    uw_bias = uw["flow_bias"]
    fdir    = forecast["direction"]
    fconf   = float(forecast["confidence"])

    score = 0
    reasons: list[str] = []

    # ── Forecast (highest weight) ──────────────────────────────────────────────
    if fdir == "bullish":
        score += 3; reasons.append(f"forecast bullish {fconf:.0%}")
    elif fdir == "bearish":
        score -= 3; reasons.append(f"forecast bearish {fconf:.0%}")

    if fconf > 0.80:
        score += (1 if fdir == "bullish" else -1)

    # ── Unusual Whales flow ────────────────────────────────────────────────────
    if uw_bias == "bullish":
        score += 2; reasons.append("UW flow bullish")
    elif uw_bias == "bearish":
        score -= 2; reasons.append("UW flow bearish")

    # ── Momentum ───────────────────────────────────────────────────────────────
    if mom > 0.15:
        score += 2; reasons.append(f"strong momentum +{mom*100:.1f}%")
    elif mom > 0.05:
        score += 1; reasons.append(f"momentum +{mom*100:.1f}%")
    elif mom < -0.15:
        score -= 2; reasons.append(f"strong negative momentum {mom*100:.1f}%")
    elif mom < -0.05:
        score -= 1; reasons.append(f"momentum {mom*100:.1f}%")

    # ── Daily RSI ──────────────────────────────────────────────────────────────
    if rsi < 35:
        score += 2; reasons.append(f"RSI oversold {rsi:.0f}")
    elif rsi > 75:
        score -= 1; reasons.append(f"RSI overbought {rsi:.0f}")

    # ── Intraday alignment ─────────────────────────────────────────────────────
    if itrend == "up" and ivwap == "above":
        score += 1; reasons.append("intraday up, above VWAP")
    elif itrend == "down" and ivwap == "below":
        score -= 1; reasons.append("intraday down, below VWAP")

    if irsi < 30:
        score += 1; reasons.append(f"intraday RSI oversold {irsi:.0f}")
    elif irsi > 70:
        score -= 1; reasons.append(f"intraday RSI overbought {irsi:.0f}")

    # ── Intraday crash guard ───────────────────────────────────────────────────
    if ireturn < -10:
        score -= 2; reasons.append(f"intraday crash {ireturn:.1f}%")
    elif ireturn > 5:
        score += 1; reasons.append(f"intraday surge +{ireturn:.1f}%")

    # ── Decision ───────────────────────────────────────────────────────────────
    if score >= 4:
        action = "BUY"
        confidence = min(95, 50 + score * 7)
    elif score >= 2:
        action = "BUY"
        confidence = min(75, 50 + score * 7)
    elif score <= -4:
        action = "SELL/SHORT"
        confidence = min(95, 50 + abs(score) * 7)
    elif score <= -2:
        action = "SELL/SHORT"
        confidence = min(75, 50 + abs(score) * 7)
    else:
        action = "HOLD"
        confidence = 50

    # ── Entry / Target / Stop (ATR-based) ─────────────────────────────────────
    atr = vol * last / 16  # rough daily ATR proxy
    atr = max(atr, last * 0.005)  # floor at 0.5%

    if action == "BUY":
        entry  = round(last, 2)
        target = round(last + atr * 3, 2)
        stop   = round(last - atr * 1.5, 2)
    elif action == "SELL/SHORT":
        entry  = round(last, 2)
        target = round(last - atr * 3, 2)
        stop   = round(last + atr * 1.5, 2)
    else:
        entry = target = stop = round(last, 2)

    rr = round(abs(target - entry) / abs(stop - entry), 2) if stop != entry else 0

    return {
        "ticker":       price.get("ticker", ""),
        "price":        last,
        "score":        score,
        "ACTION":       action,
        "confidence":   f"{confidence}%",
        "entry":        entry,
        "target":       target,
        "stop_loss":    stop,
        "risk_reward":  f"1:{rr}",
        "reasons":      reasons,
        "why": {
            "forecast":  f"{fdir} {fconf:.0%}",
            "uw_flow":   uw_bias,
            "daily_rsi": rsi,
            "momentum":  f"{mom*100:.1f}%",
            "intraday":  f"{itrend} {ivwap} VWAP | {ireturn:+.1f}%",
        },
    }
