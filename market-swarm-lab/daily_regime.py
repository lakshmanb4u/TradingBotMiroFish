#!/usr/bin/env python3
"""Daily Regime Classifier — runs once per trading day at ~9:00 AM ET.

Determines the intraday trading regime: BULL | BEAR | CHOP
Caches result in state/daily_regime/{date}.json

Inputs (deterministic-first, LLM-optional):
  1. SPY/QQQ close prices + EMA structure (from Schwab/yfinance)
  2. VWAP relationship (20-day rolling proxy)
  3. RSI(14)
  4. /ES and /NQ futures trend vs VWAP
  5. TimesFM 5-day direction/confidence (optional — falls back gracefully)

Regime rules:
  BULL  — EMA10 > EMA20, price > VWAP, RSI > 50, TimesFM direction = up/neutral
  BEAR  — EMA10 < EMA20, price < VWAP, RSI < 50, TimesFM direction = down/neutral
  CHOP  — Mixed signals (EMA/VWAP/RSI disagree)

Usage:
  python daily_regime.py           # print + cache today's regime
  python daily_regime.py --debug   # verbose output
  python daily_regime.py --date 2026-04-25  # replay historical date
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for _sd in [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "price-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "agent-seeder",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

_log = logging.getLogger(__name__)

REGIME_DIR = ROOT / "state" / "daily_regime"
REGIME_DIR.mkdir(parents=True, exist_ok=True)


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _ema(prices: list[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2 / (period + 1)
    e = prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return round(e, 4)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _vwap_rolling(bars: list[dict], period: int = 20) -> float:
    if not bars:
        return 0.0
    recent = bars[-period:]
    cv  = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in recent)
    vol = sum(b["volume"] for b in recent)
    return round(cv / vol, 4) if vol > 0 else 0.0


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_spy_bars(lookback_days: int = 30) -> tuple[list[dict], str]:
    """Fetch SPY daily bars. Returns (bars, source)."""
    # Try yfinance first (fast, free, reliable)
    try:
        import yfinance as yf
        import warnings
        warnings.filterwarnings("ignore")
        t = yf.Ticker("SPY")
        hist = t.history(period="60d", interval="1d")
        if not hist.empty:
            bars = [
                {
                    "date":   idx.strftime("%Y-%m-%d"),
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
                for idx, row in hist.iterrows()
            ]
            bars.sort(key=lambda b: b["date"])
            return bars[-lookback_days:], "yfinance"
    except Exception as e:
        _log.warning("[regime] yfinance failed: %s", e)

    # Try Alpha Vantage
    try:
        from alpha_vantage_client import AlphaVantageClient
        client = AlphaVantageClient()
        bars = client.fetch_daily("SPY", outputsize="compact")
        return bars[-lookback_days:], "alpha_vantage"
    except Exception as e:
        _log.warning("[regime] AV failed: %s", e)

    return [], "unavailable"


def _fetch_futures_data() -> dict:
    """Fetch /ES and /NQ current quote + recent bars. Returns dict with data or empty."""
    try:
        from masi_agent import _fetch_futures_bars, _fetch_futures_quote, _compute_vwap
        es_bars  = _fetch_futures_bars("/ES", 5)
        nq_bars  = _fetch_futures_bars("/NQ", 5)
        es_quote = _fetch_futures_quote("/ES")
        nq_quote = _fetch_futures_quote("/NQ")
        es_price = es_quote.get("last", 0)
        nq_price = nq_quote.get("last", 0)
        es_vwap  = _compute_vwap(es_bars[-40:]) if es_bars else 0
        nq_vwap  = _compute_vwap(nq_bars[-40:]) if nq_bars else 0
        return {
            "available": True,
            "es_price": es_price,
            "nq_price": nq_price,
            "es_vwap":  es_vwap,
            "nq_vwap":  nq_vwap,
            "es_above_vwap": es_price > es_vwap if es_vwap else None,
            "nq_above_vwap": nq_price > nq_vwap if nq_vwap else None,
        }
    except Exception as e:
        _log.warning("[regime] futures fetch failed: %s", e)
        return {"available": False}


def _fetch_timesfm(closes: list[float]) -> dict:
    """Run TimesFM 5-day forecast. Returns direction/confidence or unavailable."""
    try:
        from forecasting_service import TimesFMForecastingService
        svc = TimesFMForecastingService()
        result = svc.forecast_from_prices("SPY", closes, horizon=5)
        return {
            "available": True,
            "direction":    result.get("direction", "neutral"),
            "confidence":   result.get("confidence", 0.5),
            "predicted_return": result.get("predicted_return", 0.0),
            "provider_mode": result.get("provider_mode", "unknown"),
        }
    except Exception as e:
        _log.warning("[regime] TimesFM failed: %s", e)
        return {"available": False, "reason": str(e)[:120]}


# ── Regime scorer ─────────────────────────────────────────────────────────────

def _score_regime(
    bars: list[dict],
    futures: dict,
    timesfm: dict,
) -> dict:
    """Deterministic regime scoring. Returns full regime dict."""

    if not bars:
        return {
            "regime": "CHOP",
            "confidence": 0,
            "reason": "No price data available",
            "signals": {},
        }

    closes = [b["close"] for b in bars]
    last   = closes[-1]

    # EMA structure
    ema10 = _ema(closes[-20:], 10)
    ema20 = _ema(closes[-20:], 20)
    ema50 = _ema(closes[-50:], 50) if len(closes) >= 50 else _ema(closes, len(closes))

    # VWAP
    vwap20 = _vwap_rolling(bars, 20)

    # RSI
    rsi14 = _rsi(closes[-20:], 14)

    # Signals (each +1 = bullish, -1 = bearish, 0 = neutral)
    signals = {}

    # EMA10 vs EMA20
    if ema10 > ema20 * 1.001:
        signals["ema_cross"] = +1
    elif ema10 < ema20 * 0.999:
        signals["ema_cross"] = -1
    else:
        signals["ema_cross"] = 0

    # Price vs VWAP
    if last > vwap20 * 1.002:
        signals["price_vs_vwap"] = +1
    elif last < vwap20 * 0.998:
        signals["price_vs_vwap"] = -1
    else:
        signals["price_vs_vwap"] = 0

    # RSI
    if rsi14 > 55:
        signals["rsi"] = +1
    elif rsi14 < 45:
        signals["rsi"] = -1
    else:
        signals["rsi"] = 0

    # Price vs EMA50 (macro trend)
    if last > ema50 * 1.005:
        signals["ema50_trend"] = +1
    elif last < ema50 * 0.995:
        signals["ema50_trend"] = -1
    else:
        signals["ema50_trend"] = 0

    # Futures alignment (optional)
    if futures.get("available"):
        es_bull = futures.get("es_above_vwap")
        nq_bull = futures.get("nq_above_vwap")
        if es_bull is True and nq_bull is True:
            signals["futures"] = +1
        elif es_bull is False and nq_bull is False:
            signals["futures"] = -1
        else:
            signals["futures"] = 0
    else:
        signals["futures"] = 0  # neutral if unavailable

    # TimesFM (optional, weighted less than deterministic)
    timesfm_weight = 0
    if timesfm.get("available"):
        tf_dir  = timesfm.get("direction", "neutral")
        tf_conf = timesfm.get("confidence", 0.5)
        if tf_dir == "bullish" and tf_conf >= 0.55:
            timesfm_weight = +1
        elif tf_dir == "bearish" and tf_conf >= 0.55:
            timesfm_weight = -1
        signals["timesfm"] = timesfm_weight
    else:
        signals["timesfm"] = 0

    # Tally deterministic signals (exclude timesfm for regime decision)
    det_signals = {k: v for k, v in signals.items() if k != "timesfm"}
    bull_count = sum(1 for v in det_signals.values() if v == +1)
    bear_count = sum(1 for v in det_signals.values() if v == -1)
    total_det  = len(det_signals)

    # Regime decision
    if bull_count >= 3 and bear_count == 0:
        regime = "BULL"
        confidence = int(bull_count / total_det * 100)
    elif bear_count >= 3 and bull_count == 0:
        regime = "BEAR"
        confidence = int(bear_count / total_det * 100)
    elif bull_count >= 3 and bear_count == 1:
        regime = "BULL"
        confidence = int((bull_count - 0.5) / total_det * 100)
    elif bear_count >= 3 and bull_count == 1:
        regime = "BEAR"
        confidence = int((bear_count - 0.5) / total_det * 100)
    else:
        regime = "CHOP"
        confidence = max(0, 100 - abs(bull_count - bear_count) * 20)

    # TimesFM can flip BULL↔CHOP or BEAR↔CHOP but not BULL↔BEAR
    timesfm_disagreement = False
    if timesfm.get("available") and signals["timesfm"] != 0:
        if regime == "BULL" and signals["timesfm"] == -1:
            timesfm_disagreement = True
            regime = "CHOP"
            confidence = max(confidence - 20, 30)
        elif regime == "BEAR" and signals["timesfm"] == +1:
            timesfm_disagreement = True
            regime = "CHOP"
            confidence = max(confidence - 20, 30)

    # Build reason string
    parts = []
    for name, val in signals.items():
        sym = "↑" if val == 1 else ("↓" if val == -1 else "→")
        parts.append(f"{name}={sym}")
    reason = f"bull={bull_count} bear={bear_count} ({', '.join(parts)})"
    if timesfm_disagreement:
        reason += f" | TimesFM override: {timesfm.get('direction')} {timesfm.get('confidence',0):.0%}"

    return {
        "regime":    regime,
        "confidence": confidence,
        "reason":     reason,
        "signals":    signals,
        "indicators": {
            "last_close":  round(last, 2),
            "ema10":       round(ema10, 2),
            "ema20":       round(ema20, 2),
            "ema50":       round(ema50, 2),
            "vwap20":      round(vwap20, 2),
            "rsi14":       round(rsi14, 1),
        },
        "futures":  futures,
        "timesfm":  timesfm,
        "timesfm_disagreement": timesfm_disagreement,
    }


# ── Cache + load ──────────────────────────────────────────────────────────────

def _cache_path(for_date: date) -> Path:
    return REGIME_DIR / f"{for_date.isoformat()}.json"


def load_cached_regime(for_date: date | None = None) -> dict | None:
    """Load cached regime for today (or specific date). Returns None if missing."""
    d = for_date or date.today()
    path = _cache_path(d)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def save_regime(regime_result: dict, for_date: date | None = None) -> Path:
    d = for_date or date.today()
    path = _cache_path(d)
    path.write_text(json.dumps(regime_result, indent=2, default=str))
    return path


# ── Main compute ──────────────────────────────────────────────────────────────

def compute_regime(for_date: date | None = None, force: bool = False) -> dict:
    """Compute (or load cached) daily regime.

    Args:
        for_date: Date to compute for (default: today)
        force:    Recompute even if cached

    Returns:
        Full regime dict including source_audit
    """
    d = for_date or date.today()

    if not force:
        cached = load_cached_regime(d)
        if cached:
            cached["from_cache"] = True
            return cached

    source_audit = {}

    # 1. SPY daily bars
    bars, bars_source = _fetch_spy_bars(lookback_days=60)
    source_audit["ohlcv"] = {
        "provider": bars_source,
        "status":   "live" if bars else "unavailable",
        "records":  len(bars),
    }

    # 2. Futures
    futures = _fetch_futures_data()
    source_audit["futures"] = {
        "provider": "schwab" if futures.get("available") else "none",
        "status":   "live" if futures.get("available") else "unavailable",
    }

    # 3. TimesFM (optional)
    closes = [b["close"] for b in bars] if bars else []
    timesfm = _fetch_timesfm(closes) if len(closes) >= 20 else {"available": False, "reason": "insufficient history"}
    source_audit["timesfm"] = {
        "provider":   timesfm.get("provider_mode", "none"),
        "status":     "live" if timesfm.get("available") else "unavailable",
        "direction":  timesfm.get("direction"),
        "confidence": timesfm.get("confidence"),
    }

    # 4. Score regime
    scored = _score_regime(bars, futures, timesfm)

    result = {
        "date":          d.isoformat(),
        "computed_at":   datetime.now(timezone.utc).isoformat(),
        "regime":        scored["regime"],
        "confidence":    scored["confidence"],
        "reason":        scored["reason"],
        "signals":       scored["signals"],
        "indicators":    scored["indicators"],
        "timesfm_disagreement": scored.get("timesfm_disagreement", False),
        "source_audit":  source_audit,
        "from_cache":    False,
        # Trading parameters derived from regime
        "trading_params": _regime_trading_params(scored["regime"], scored["confidence"]),
    }

    save_regime(result, d)
    return result


def _regime_trading_params(regime: str, confidence: int) -> dict:
    """Return trading thresholds/adjustments for each regime."""
    if regime == "BULL":
        return {
            "allowed_actions":    ["BUY"],
            "min_ensemble_votes": 3,
            "stop_multiplier":    1.0,   # normal stops
            "confidence_boost":   +5,    # add to ensemble confidence
            "description":        "Long-only. 3/4 votes required.",
        }
    elif regime == "BEAR":
        return {
            "allowed_actions":    ["SELL/SHORT"],
            "min_ensemble_votes": 3,
            "stop_multiplier":    1.0,
            "confidence_boost":   +5,
            "description":        "Short-only. 3/4 votes required.",
        }
    else:  # CHOP
        return {
            "allowed_actions":    ["BUY", "SELL/SHORT"],
            "min_ensemble_votes": 4,   # require unanimous
            "stop_multiplier":    0.7,  # tighten stops 30%
            "confidence_boost":   -10,  # reduce confidence
            "description":        "Both directions allowed. 4/4 required. Stops tightened 30%.",
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_regime(r: dict) -> None:
    regime = r["regime"]
    emoji  = "🟢" if regime == "BULL" else ("🔴" if regime == "BEAR" else "🟡")
    cached = " (cached)" if r.get("from_cache") else ""
    print(f"\n{'='*60}")
    print(f"  Daily Regime — {r['date']}{cached}")
    print(f"{'='*60}")
    print(f"  {emoji}  REGIME: {regime}  ({r['confidence']}% confidence)")
    print(f"  Reason:  {r['reason']}")

    ind = r.get("indicators", {})
    print(f"\n  Indicators:")
    print(f"    SPY close: ${ind.get('last_close','?')}  VWAP20: ${ind.get('vwap20','?')}")
    print(f"    EMA10: ${ind.get('ema10','?')}  EMA20: ${ind.get('ema20','?')}  EMA50: ${ind.get('ema50','?')}")
    print(f"    RSI14: {ind.get('rsi14','?')}")

    fut = r.get("source_audit", {}).get("futures", {})
    tf  = r.get("source_audit", {}).get("timesfm", {})
    print(f"\n  Futures:  {fut.get('status','?')}")
    print(f"  TimesFM:  {tf.get('status','?')}  direction={tf.get('direction','?')}  conf={tf.get('confidence','?')}")
    if r.get("timesfm_disagreement"):
        print(f"  ⚠️  TimesFM disagreed — regime softened to CHOP")

    tp = r.get("trading_params", {})
    print(f"\n  Trading params:")
    print(f"    Allowed: {tp.get('allowed_actions')}  Min votes: {tp.get('min_ensemble_votes')}")
    print(f"    Stop multiplier: {tp.get('stop_multiplier')}x  Conf adj: {tp.get('confidence_boost'):+d}")
    print(f"    {tp.get('description','')}")

    audit = r.get("source_audit", {})
    print(f"\n  Source audit:")
    for src, v in audit.items():
        icon = "✅" if v.get("status") in ("live","config") else "⚠️ "
        print(f"    {icon} {src}: {v.get('status','?')} via {v.get('provider','?')}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Daily Regime Classifier")
    parser.add_argument("--date",  help="Date to compute (YYYY-MM-DD, default: today)")
    parser.add_argument("--force", action="store_true", help="Recompute even if cached")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    from datetime import date as _date
    for_date = None
    if args.date:
        from datetime import datetime as _dt
        for_date = _dt.strptime(args.date, "%Y-%m-%d").date()

    regime = compute_regime(for_date=for_date, force=args.force)
    _print_regime(regime)


if __name__ == "__main__":
    main()
