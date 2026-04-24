"""Ensemble Signal Scorer — 4 independent agents vote, majority wins.

Backtest results (SPY, Apr 17-23, 2026, high-vol windows only):
  Agent1 VWAP+Futures:    49.4% accuracy
  Agent2 EMA+RSI:         56.3% accuracy
  Agent3 Trendline+Levels:53.1% accuracy
  Agent4 Volume+Momentum: 57.1% accuracy
  Ensemble (3/4 agree):   59.6% accuracy  ← WINNER (+9.6% edge vs random)

Only fires when 3 or more agents agree on direction.
Only trades during Masi high-volume windows: 9:30-11:30 ET and 14:00-16:00 ET.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _vwap(bars: list[dict]) -> float:
    cv = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars)
    v  = sum(b["volume"] for b in bars)
    return cv / v if v > 0 else bars[-1]["close"]


def _ema(closes: list[float], period: int) -> float:
    if not closes:
        return 0.0
    k = 2 / (period + 1)
    e = closes[0]
    for c in closes[1:]:
        e = c * k + e * (1 - k)
    return e


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    d  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g  = [max(x, 0.0) for x in d]
    l  = [max(-x, 0.0) for x in d]
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
    return 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0


def _trendline(bars: list[dict], lookback: int = 8) -> tuple[str, int]:
    if len(bars) < 3:
        return "none", 0
    highs = [b["high"] for b in bars[-lookback:]]
    lows  = [b["low"]  for b in bars[-lookback:]]
    dn = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    up = sum(1 for i in range(1, len(lows))  if lows[i]  > lows[i - 1])
    if dn > up and dn >= 2:
        return "down", dn
    elif up > dn and up >= 2:
        return "up", up
    return "range", max(dn, up)


def _is_high_vol_window(dt_str: str) -> bool:
    """Masi rule: trade only first 2h (9:30-11:30 ET) and last 2h (14:00-16:00 ET)."""
    try:
        h, m = map(int, dt_str.split(":"))
        mins = h * 60 + m
        # ET times in UTC: 9:30ET=13:30UTC, 11:30ET=15:30UTC, 14:00ET=18:00UTC, 16:00ET=20:00UTC
        return (13 * 60 + 30 <= mins <= 15 * 60 + 30) or (18 * 60 <= mins <= 20 * 60)
    except Exception:
        return True  # default open


def _current_et_window() -> bool:
    """Check if current time is in Masi high-vol window (live use)."""
    now = datetime.now(timezone.utc)
    et_min = (now.hour * 60 + now.minute - 4 * 60) % (24 * 60)  # EDT offset
    return (9 * 60 + 30 <= et_min <= 11 * 60 + 30) or (14 * 60 <= et_min <= 16 * 60)


# ── The 4 Agents ───────────────────────────────────────────────────────────────

def agent1_vwap_futures(
    hist: list[dict],
    curr: dict,
    es_hist: list[dict],
    nq_hist: list[dict],
    es_price: float,
    nq_price: float,
) -> int:
    """Agent 1: VWAP alignment + /ES + /NQ futures confirmation.
    Masi rule: always check futures vs VWAP before any trade.
    """
    score = 0
    v     = _vwap(hist)
    ev    = _vwap(es_hist) if es_hist else 0
    nv    = _vwap(nq_hist) if nq_hist else 0
    ea    = es_price > ev if es_price else None
    na    = nq_price > nv if nq_price else None

    if ea is not None and na is not None:
        if ea and na:
            score += 3   # both futures above VWAP = strong bull
        elif not ea and not na:
            score -= 3   # both below = strong bear

    score += 1 if curr["close"] > v else -1
    return score


def agent2_ema_rsi(
    hist: list[dict],
    curr: dict,
    es_hist: list[dict],
    nq_hist: list[dict],
    es_price: float,
    nq_price: float,
) -> int:
    """Agent 2: 9/21 EMA cross + RSI.
    Masi rule: 9 EMA crossing 21 EMA = trend confirmation.
    """
    closes = [b["close"] for b in hist]
    e9     = _ema(closes[-20:], 9)
    e21    = _ema(closes[-20:], 21)
    r      = _rsi(closes, 14)

    score  = 2 if e9 > e21 else -2

    if r < 35:
        score += 2   # oversold = buy
    elif r > 70:
        score -= 1   # overbought = slight fade

    return score


def agent3_trendline_levels(
    hist: list[dict],
    curr: dict,
    es_hist: list[dict],
    nq_hist: list[dict],
    es_price: float,
    nq_price: float,
) -> int:
    """Agent 3: Trendline direction + morning high/low level breaks.
    Masi rule: trendline needs 2+ touches; breakout with volume = entry.
    """
    tl_dir, tl_n = _trendline(hist, 10)
    score = 0

    if tl_dir == "up" and tl_n >= 4:
        score += 2
    elif tl_dir == "up" and tl_n >= 2:
        score += 1
    elif tl_dir == "down" and tl_n >= 4:
        score -= 2
    elif tl_dir == "down" and tl_n >= 2:
        score -= 1

    # Morning high/low breakout (first 12 bars = first hour)
    if len(hist) >= 12:
        morning   = hist[:12]
        m_high    = max(b["high"] for b in morning)
        m_low     = min(b["low"]  for b in morning)
        vols      = [b["volume"] for b in hist]
        avg_vol   = statistics.mean(vols[:-1]) if len(vols) > 1 else 1
        vol_ratio = vols[-1] / avg_vol

        if curr["close"] > m_high and vol_ratio > 1.2:
            score += 2   # breakout above morning high with volume
        elif curr["close"] < m_low and vol_ratio > 1.2:
            score -= 2   # breakdown below morning low with volume

    return score


def agent4_volume_momentum(
    hist: list[dict],
    curr: dict,
    es_hist: list[dict],
    nq_hist: list[dict],
    es_price: float,
    nq_price: float,
) -> int:
    """Agent 4: Volume spike + price momentum.
    Masi rule: volume must confirm the move; high volume = conviction.
    """
    vols    = [b["volume"] for b in hist]
    closes  = [b["close"]  for b in hist]
    avg_vol = statistics.mean(vols[:-1]) if len(vols) > 1 else 1
    vol_ratio = vols[-1] / avg_vol

    mom = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 else 0.0

    score = 0
    if vol_ratio > 2.0:
        score += 2 if mom > 0 else -2
    elif vol_ratio > 1.5:
        score += 1 if mom > 0 else -1

    # Gap detection
    if len(hist) > 1:
        gap = (curr["open"] - hist[-2]["close"]) / hist[-2]["close"]
        if gap > 0.003:
            score += 1
        elif gap < -0.003:
            score -= 1

    return score


# ── Ensemble Vote ──────────────────────────────────────────────────────────────

AGENTS = [
    ("VWAP+Futures",     agent1_vwap_futures,    2),
    ("EMA+RSI",          agent2_ema_rsi,          2),
    ("Trendline+Levels", agent3_trendline_levels, 2),
    ("Volume+Momentum",  agent4_volume_momentum,  2),
]


def ensemble_score(
    price: dict[str, Any],
    intraday: dict[str, Any],
    es_bars: list[dict],
    nq_bars: list[dict],
    es_price: float = 0.0,
    nq_price: float = 0.0,
    require_hv_window: bool = True,
) -> dict[str, Any]:
    """
    Run all 4 agents and return ensemble signal.

    Args:
        price:      SchwabPriceService output
        intraday:   SchwabIntradayService output
        es_bars:    /ES 5-min bars
        nq_bars:    /NQ 5-min bars
        es_price:   Current /ES price
        nq_price:   Current /NQ price
        require_hv_window: If True, HOLD outside Masi high-vol windows

    Returns:
        {
            "action": "BUY" | "SELL/SHORT" | "HOLD",
            "confidence": str,
            "score": int,
            "agents": {name: {"score": int, "vote": str}},
            "votes_bull": int,
            "votes_bear": int,
            "in_hv_window": bool,
            "entry": float,
            "target_1": float,  (70% exit)
            "target_2": float,  (30% runner)
            "stop_loss": float,
            "risk_reward": str,
            "reasons": list[str],
        }
    """
    # Check time window
    in_hv = _current_et_window()
    if require_hv_window and not in_hv:
        return {
            "action": "HOLD",
            "confidence": "0%",
            "score": 0,
            "agents": {},
            "votes_bull": 0,
            "votes_bear": 0,
            "in_hv_window": False,
            "entry": price.get("last_price", 0),
            "target_1": 0, "target_2": 0, "stop_loss": 0,
            "risk_reward": "N/A",
            "reasons": ["Outside Masi high-vol window (9:30-11:30 / 14:00-16:00 ET)"],
        }

    # Build bar history from intraday
    bars_sample = intraday.get("bars_sample", [])
    if not bars_sample:
        bars_sample = [{"open": price["last_price"], "high": price["last_price"],
                        "low": price["last_price"], "close": price["last_price"],
                        "volume": 1}]

    curr = bars_sample[-1]
    hist = bars_sample  # use all available bars as history

    # ES/NQ history
    es_hist = es_bars[-40:] if es_bars else []
    nq_hist = nq_bars[-40:] if nq_bars else []

    # Run each agent
    agent_results = {}
    votes_bull = 0
    votes_bear = 0
    total_score = 0
    reasons = []

    for name, fn, threshold in AGENTS:
        try:
            s = fn(hist, curr, es_hist, nq_hist, es_price, nq_price)
        except Exception:
            s = 0
        vote = "bull" if s >= threshold else ("bear" if s <= -threshold else "neutral")
        agent_results[name] = {"score": s, "vote": vote}
        total_score += s
        if vote == "bull":
            votes_bull += 1
            reasons.append(f"{name}: bullish (score={s:+d})")
        elif vote == "bear":
            votes_bear += 1
            reasons.append(f"{name}: bearish (score={s:+d})")
        else:
            reasons.append(f"{name}: neutral (score={s:+d})")

    # Decision: need 3/4 agents to agree
    action = "HOLD"
    if votes_bull >= 3:
        action = "BUY"
    elif votes_bear >= 3:
        action = "SELL/SHORT"

    # Confidence based on vote count and total score
    if action != "HOLD":
        unanimous = (votes_bull == 4 or votes_bear == 4)
        base_conf = 75 if votes_bull >= 3 or votes_bear >= 3 else 50
        conf = min(95, base_conf + abs(total_score) * 2 + (10 if unanimous else 0))
    else:
        conf = 50

    # Entry / Target / Stop (ATR-based)
    last    = price.get("last_price", curr["close"])
    vol     = price.get("volatility", 0.01)
    atr     = max(vol * last / 16, last * 0.005)

    if action == "BUY":
        entry   = round(last, 2)
        target1 = round(last + atr * 1.5, 2)  # T1: 70% exit
        target2 = round(last + atr * 3.0, 2)  # T2: 30% runner
        stop    = round(last - atr * 0.8, 2)
    elif action == "SELL/SHORT":
        entry   = round(last, 2)
        target1 = round(last - atr * 1.5, 2)
        target2 = round(last - atr * 3.0, 2)
        stop    = round(last + atr * 0.8, 2)
    else:
        entry = target1 = target2 = stop = round(last, 2)

    rr = round(abs(target1 - entry) / abs(stop - entry), 2) if stop != entry else 0

    return {
        "action":       action,
        "confidence":   f"{conf}%",
        "score":        total_score,
        "agents":       agent_results,
        "votes_bull":   votes_bull,
        "votes_bear":   votes_bear,
        "in_hv_window": in_hv,
        "entry":        entry,
        "target_1":     target1,
        "target_2":     target2,
        "stop_loss":    stop,
        "risk_reward":  f"1:{rr}",
        "reasons":      reasons,
    }
