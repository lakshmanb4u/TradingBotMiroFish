#!/usr/bin/env python3
"""MiroFish Multi-Ticker Alert Scanner — v2 (Regime-Aware + TimesFM + MASi)

Architecture (deterministic-first, LLM-optional):

  Layer 1 — Daily Regime (EMA + VWAP + RSI + TimesFM)
    Computed once at open, cached in state/daily_regime/{date}.json
    BULL  → long-only, 3/4 votes, normal stops
    BEAR  → short-only, 3/4 votes, normal stops
    CHOP  → both directions, 4/4 votes, stops tightened 30%, conf -10

  Layer 2 — Ensemble 4-agent vote (VWAP+Futures, EMA+RSI, Trendline, Volume)
    Gated by regime: wrong-direction signals suppressed
    All post-mortem fixes applied (ORF, EOD block, 60-min cooldown, UW gate)

  Layer 3 — TimesFM pre-filter
    Agrees → +conf boost
    Disagrees → -conf, warning in alert
    Unavailable → continues, marked in source_audit

  Layer 4 — MASi Agent confirmation/veto (Kimi K2 LLM)
    Only called when ensemble fires 3/4+
    CONFIRM / VETO / WARN
    DEGRADED → signal proceeds without LLM
    Times out → signal proceeds, masi_status=degraded

Delivery:
  - Terminal stdout always
  - openclaw notify for WhatsApp/Telegram

Post-mortem fixes (2026-04-26):
  - Opening range filter: no entries before 10:00 ET
  - EOD block: no new entries after 15:00 ET
  - 60-min cooldown per ticker
  - UW flow gate

Usage:
  python mirofish_alerts.py                       # all tickers, continuous
  python mirofish_alerts.py --watchlist mega      # Mag7 only
  python mirofish_alerts.py NVDA CLS CDNS         # custom tickers
  python mirofish_alerts.py --once                # single scan + exit (testing)
  python mirofish_alerts.py --no-notify           # terminal only
  python mirofish_alerts.py --no-masi             # skip MASi, deterministic only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for _sd in [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "agent-seeder",
    ROOT / "services" / "price-collector",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
_log = logging.getLogger(__name__)

from schwab_price_service import SchwabPriceService
from schwab_intraday_service import SchwabIntradayService
from uw_collector_service import UWCollectorService
from ensemble_scorer import ensemble_score

try:
    from masi_agent import _fetch_futures_bars, _fetch_futures_quote, _compute_vwap
    _FUTURES = True
except Exception:
    _FUTURES = False

try:
    from masi_confirmer import confirm as masi_confirm
    _MASI_AVAILABLE = True
except Exception:
    _MASI_AVAILABLE = False

try:
    from forecasting_service import TimesFMForecastingService
    _TIMESFM_AVAILABLE = True
except Exception:
    _TIMESFM_AVAILABLE = False

# ── Watchlists ─────────────────────────────────────────────────────────────────

WATCHLISTS: dict[str, list[str]] = {
    "mega":    ["AAPL","MSFT","GOOGL","META","AMZN","NVDA","TSLA"],
    "semis":   ["NVDA","AMD","QCOM","ARM","AVGO","INTC","ASML","AMAT","KLAC","LRCX"],
    "ai":      ["NVDA","META","MSFT","GOOGL","AMZN","CLS","CDNS","SNPS","FN","ANET","VRT","SMCI","ARM","PLTR"],
    "options": ["SPY","QQQ","NVDA","TSLA","META","AAPL","MSFT","GOOGL","AMD","ARM"],
    "all": [
        "AAPL","MSFT","GOOGL","META","AMZN","NVDA","TSLA",
        "AMD","QCOM","ARM","AVGO","INTC","ASML","AMAT","KLAC","LRCX",
        "CLS","CDNS","SNPS","FN","ANET","VRT","SMCI",
        "SPY","QQQ",
        "NFLX","UBER","SHOP","PLTR","COIN",
    ],
}


# ── Time helpers ───────────────────────────────────────────────────────────────

def et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def is_market_hours() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=25, second=0, microsecond=0)
    end   = now.replace(hour=16, minute=5,  second=0, microsecond=0)
    return start <= now <= end


def is_high_vol_window() -> bool:
    now  = et_now()
    mins = now.hour * 60 + now.minute
    return (9*60+30 <= mins <= 11*60+30) or (14*60 <= mins <= 16*60)


def minutes_to_open() -> int:
    now   = et_now()
    open_ = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= open_:
        return 0
    return int((open_ - now).total_seconds() / 60)


# ── Regime loader ─────────────────────────────────────────────────────────────

def _load_regime() -> dict:
    """Load today's cached regime or compute if missing."""
    try:
        from daily_regime import compute_regime
        return compute_regime()
    except Exception as e:
        _log.warning("[alerts] regime load failed: %s", e)
        # Safe default: CHOP (most conservative)
        return {
            "regime":   "CHOP",
            "confidence": 0,
            "reason":   f"regime unavailable: {e}",
            "trading_params": {
                "allowed_actions":    ["BUY","SELL/SHORT"],
                "min_ensemble_votes": 4,
                "stop_multiplier":    0.7,
                "confidence_boost":   -10,
                "description":        "Default CHOP (regime unavailable)",
            },
            "source_audit": {"regime": {"status": "error"}},
        }


# ── TimesFM pre-filter ────────────────────────────────────────────────────────

_timesfm_cache: dict[str, dict] = {}  # ticker -> forecast (reset each scan cycle)


def _get_timesfm(ticker: str, close_prices: list[float]) -> dict:
    """Run TimesFM for ticker, cached per scan cycle."""
    if ticker in _timesfm_cache:
        return _timesfm_cache[ticker]

    if not _TIMESFM_AVAILABLE or len(close_prices) < 20:
        result = {"available": False, "reason": "unavailable or insufficient history"}
        _timesfm_cache[ticker] = result
        return result

    try:
        svc = TimesFMForecastingService()
        out = svc.forecast_from_prices(ticker, close_prices, horizon=5)
        result = {
            "available":     True,
            "direction":     out.get("direction", "neutral"),
            "confidence":    out.get("confidence", 0.5),
            "predicted_return": out.get("predicted_return", 0.0),
            "provider_mode": out.get("provider_mode", "unknown"),
        }
    except Exception as e:
        result = {"available": False, "reason": str(e)[:80]}

    _timesfm_cache[ticker] = result
    return result


def _apply_timesfm_filter(ens: dict, timesfm: dict) -> dict:
    """Adjust ensemble confidence based on TimesFM agreement/disagreement."""
    result = {**ens}
    result["timesfm"] = timesfm
    result["timesfm_agreement"] = None

    if not timesfm.get("available"):
        result["timesfm_agreement"] = "unavailable"
        return result

    action    = ens.get("action", "HOLD")
    tf_dir    = timesfm.get("direction", "neutral")
    tf_conf   = timesfm.get("confidence", 0.5)

    agrees = (
        (action == "BUY"        and tf_dir == "bullish") or
        (action == "SELL/SHORT" and tf_dir == "bearish") or
        tf_dir == "neutral"
    )
    disagrees = (
        (action == "BUY"        and tf_dir == "bearish") or
        (action == "SELL/SHORT" and tf_dir == "bullish")
    )

    if agrees and tf_conf >= 0.55:
        result["timesfm_agreement"] = "agree"
        # Extract numeric confidence and boost
        conf_str = str(ens.get("confidence", "75%")).rstrip("%")
        try:
            result["confidence"] = f"{min(95, int(conf_str) + 5)}%"
        except Exception:
            pass
    elif disagrees and tf_conf >= 0.6:
        result["timesfm_agreement"] = "disagree"
        conf_str = str(ens.get("confidence", "75%")).rstrip("%")
        try:
            result["confidence"] = f"{max(40, int(conf_str) - 15)}%"
        except Exception:
            pass
    else:
        result["timesfm_agreement"] = "neutral"

    return result


# ── Signal fetch ───────────────────────────────────────────────────────────────

def fetch_signal(
    ticker: str,
    es_bars: list,
    nq_bars: list,
    es_price: float,
    nq_price: float,
) -> dict:
    price    = SchwabPriceService().collect(ticker)
    intraday = SchwabIntradayService().collect(ticker)
    uw       = UWCollectorService().collect(ticker, current_price=price["last_price"])

    price["uw_flow"]     = uw.get("flow_bias", "neutral")
    price["uw_net_puts"] = uw.get("net_put_sweep", False)

    ens = ensemble_score(
        price, intraday, es_bars, nq_bars,
        es_price, nq_price,
        require_hv_window=False,
    )
    ens["ticker"]     = ticker
    ens["price"]      = price["last_price"]
    ens["uw_bias"]    = uw.get("flow_bias", "neutral")
    ens["uw_signals"] = uw.get("signals", [])
    ens["intraday"]   = intraday.get("current", {})
    ens["time_et"]    = et_now().strftime("%H:%M ET")

    # Attach close prices for TimesFM
    ens["close_prices"] = price.get("close_prices", [])

    return ens


# ── Regime gate ───────────────────────────────────────────────────────────────

def _regime_allows(action: str, regime: dict) -> bool:
    """Return True if regime allows this action direction."""
    params = regime.get("trading_params", {})
    allowed = params.get("allowed_actions", ["BUY","SELL/SHORT"])
    return action in allowed


def _regime_min_votes(regime: dict, hv: bool) -> int:
    params = regime.get("trading_params", {})
    min_v  = params.get("min_ensemble_votes", 3)
    # Outside high-vol window, always require 4/4 regardless of regime
    return max(min_v, 4 if not hv else min_v)


def _apply_regime_to_stops(ens: dict, regime: dict) -> dict:
    params = regime.get("trading_params", {})
    mult   = params.get("stop_multiplier", 1.0)
    if mult != 1.0:
        result = {**ens}
        entry = ens.get("entry", 0)
        stop  = ens.get("stop_loss", 0)
        if entry and stop:
            new_stop = entry - (entry - stop) * mult
            result["stop_loss"] = round(new_stop, 2)
        return result
    return ens


# ── Output formatters ─────────────────────────────────────────────────────────

def _format_terminal(ens: dict, regime: dict, masi: dict | None, hv: bool) -> str:
    ticker = ens["ticker"]
    action = ens["action"]
    price  = ens["price"]
    conf   = ens.get("confidence", "?")
    bull   = ens.get("votes_bull", 0)
    bear   = ens.get("votes_bear", 0)
    arrow  = "▲" if action == "BUY" else "▼"
    hv_tag = "🔥 HIGH-VOL" if hv else "⚡ normal"
    reg    = regime.get("regime", "?")
    reg_em = "🟢" if reg=="BULL" else ("🔴" if reg=="BEAR" else "🟡")

    tf     = ens.get("timesfm", {})
    tf_agr = ens.get("timesfm_agreement", "unavailable")
    tf_icon = {"agree":"✅","disagree":"⚠️ ","neutral":"→","unavailable":"—"}.get(tf_agr,"—")

    lines = [
        f"\n{'='*60}",
        f"  [{ens.get('time_et','?')}] {ticker}  ${price:.2f}  {arrow} {action}  ({conf})",
        f"  {hv_tag}  |  Bulls: {bull}/4  Bears: {bear}/4",
        f"  {reg_em} Regime: {reg} ({regime.get('confidence','?')}% conf)",
        f"{'='*60}",
        f"  Entry:    ${ens.get('entry',0):.2f}",
        f"  T1 (70%): ${ens.get('target_1',0):.2f}",
        f"  T2 (30%): ${ens.get('target_2',0):.2f}",
        f"  Stop:     ${ens.get('stop_loss',0):.2f}  R:R {ens.get('risk_reward','?')}",
        f"\n  Agent votes:",
    ]
    for name, res in ens.get("agents", {}).items():
        icon = "✅" if res["vote"]=="bull" else ("❌" if res["vote"]=="bear" else "▪")
        lines.append(f"    {icon} {name:<22}  score={res['score']:+d}  → {res['vote'].upper()}")
    if ens.get("ema_rsi_persistent_bear"):
        lines.append("  ⚠️  EMA+RSI persistent bear — confidence reduced")
    lines.append(f"  UW flow: {ens.get('uw_bias','?').upper()}")
    if ens.get("uw_bias","").lower() == "bearish":
        lines.append("  ⚠️  UW BEARISH flow — verify no net put sweeps before entry")

    intra = ens.get("intraday", {})
    lines.append(f"  Intraday: {intra.get('intraday_trend','?')} | RSI {intra.get('rsi',0):.0f} | {intra.get('price_vs_vwap','?')} VWAP")

    # TimesFM
    if tf.get("available"):
        lines.append(f"  TimesFM: {tf_icon} {tf.get('direction','?')} ({tf.get('confidence',0):.0%} conf) [{tf.get('provider_mode','?')}]")
        if tf_agr == "disagree":
            lines.append(f"  ⚠️  TimesFM DISAGREES — confidence reduced")
    else:
        lines.append(f"  TimesFM: — unavailable ({tf.get('reason','')})")

    # MASi
    if masi:
        v = masi.get("verdict","?")
        v_icon = {"CONFIRM":"✅","VETO":"🚫","WARN":"⚠️ ","DEGRADED":"—"}.get(v,"?")
        lines.append(f"  MASi: {v_icon} {v}  ({masi.get('latency_ms','?')}ms)  {masi.get('reason','')[:80]}")
        for risk in masi.get("risks",[])[:2]:
            lines.append(f"    ↳ risk: {risk}")
    else:
        lines.append(f"  MASi: — not called")

    for sig in ens.get("uw_signals",[])[:2]:
        lines.append(f"  ⚡ {sig.get('reason','')}")

    return "\n".join(lines)


def _format_whatsapp(ens: dict, regime: dict, masi: dict | None, hv: bool) -> str:
    ticker = ens["ticker"]
    action = ens["action"]
    price  = ens["price"]
    conf   = ens.get("confidence","?")
    bull   = ens.get("votes_bull",0)
    bear   = ens.get("votes_bear",0)
    entry  = ens.get("entry",0)
    t1     = ens.get("target_1",0)
    t2     = ens.get("target_2",0)
    stop   = ens.get("stop_loss",0)
    rr     = ens.get("risk_reward","?")
    arrow  = "▲" if action=="BUY" else "▼"
    hv_tag = "HIGH-VOL" if hv else "normal"
    uw     = ens.get("uw_bias","neutral").upper()
    reg    = regime.get("regime","?")
    reg_em = "🟢" if reg=="BULL" else ("🔴" if reg=="BEAR" else "🟡")
    time_s = ens.get("time_et","?")

    tf     = ens.get("timesfm",{})
    tf_agr = ens.get("timesfm_agreement","unavailable")
    tf_line = ""
    if tf.get("available"):
        tf_line = f"\nTimesFM: {tf.get('direction','?')} {tf.get('confidence',0):.0%}"
        if tf_agr == "disagree":
            tf_line += " ⚠️ DISAGREES"
    
    masi_line = ""
    if masi:
        v = masi.get("verdict","?")
        v_icon = {"CONFIRM":"✅","VETO":"🚫","WARN":"⚠️","DEGRADED":"—"}.get(v,"?")
        masi_line = f"\nMASi: {v_icon} {v} — {masi.get('reason','')[:60]}"

    flags = []
    if ens.get("ema_rsi_persistent_bear"):
        flags.append("⚠️ EMA bear outlier")
    if uw == "BEARISH":
        flags.append("⚠️ UW bearish flow")
    flags_line = "\n" + " | ".join(flags) if flags else ""

    sweeps = [s.get("reason","") for s in ens.get("uw_signals",[])[:1] if s.get("reason")]
    sweep_line = f"\n⚡ {sweeps[0]}" if sweeps else ""

    msg = (
        f"🐟 MiroFish [{time_s}]\n"
        f"{arrow} *{ticker}* ${price:.2f} — {action} ({conf}) [{hv_tag}]\n"
        f"{reg_em} Regime: {reg} | Ensemble: {bull}/4 | UW: {uw}\n"
        f"Entry: ${entry:.2f}  Stop: ${stop:.2f}  R:R {rr}\n"
        f"T1: ${t1:.2f}  T2: ${t2:.2f}"
        f"{tf_line}"
        f"{masi_line}"
        f"{flags_line}"
        f"{sweep_line}"
    )
    return msg


# ── Delivery ──────────────────────────────────────────────────────────────────

def send_alert(message: str) -> None:
    try:
        subprocess.run(["openclaw","notify", message], capture_output=True, timeout=10)
    except Exception:
        pass


# ── Main scanner ──────────────────────────────────────────────────────────────

def run_scanner(
    tickers: list[str],
    interval: int = 300,
    notify: bool = True,
    use_masi: bool = True,
    once: bool = False,
) -> None:

    print(f"\n{'='*60}")
    print(f"  MiroFish Alert Scanner v2")
    print(f"  Tickers ({len(tickers)}): {', '.join(tickers[:10])}{'...' if len(tickers)>10 else ''}")
    print(f"  Interval: {interval//60} min  |  Notify: {'on' if notify else 'off'}")
    print(f"  MASi: {'on' if use_masi and _MASI_AVAILABLE else 'off'}")
    print(f"  TimesFM: {'on' if _TIMESFM_AVAILABLE else 'off'}")
    print(f"  Filters: ORF 10:00 ET | EOD 15:00 ET | 60-min cooldown | UW gate")
    print(f"{'='*60}\n")

    last_signals: dict[str, dict] = {}
    regime: dict = {}
    regime_date: str = ""

    while True:
        now_et  = et_now()
        now_str = now_et.strftime("%A %H:%M ET")

        if not once and not is_market_hours():
            mins = minutes_to_open()
            msg = f"[{now_str}] Market closed. Opens in {mins} min." if mins > 0 else f"[{now_str}] Market closed."
            print(msg)
            time.sleep(interval)
            continue

        # Refresh regime once per trading day
        today_str = now_et.strftime("%Y-%m-%d")
        if regime_date != today_str or not regime:
            global _timesfm_cache
            _timesfm_cache = {}  # clear TimesFM cache on new day
            print(f"[{now_str}] Loading daily regime...")
            regime = _load_regime()
            regime_date = today_str
            reg = regime.get("regime","?")
            reg_conf = regime.get("confidence","?")
            params = regime.get("trading_params",{})
            print(f"  Regime: {reg} ({reg_conf}% conf)  "
                  f"min_votes={params.get('min_ensemble_votes',3)}  "
                  f"stop_mult={params.get('stop_multiplier',1.0)}x\n")

        hv      = is_high_vol_window()
        hv_str  = "🔥 HIGH-VOL" if hv else "normal"
        print(f"\n[{now_str}] {hv_str} | Scanning {len(tickers)} tickers... regime={regime.get('regime','?')}")

        # Fetch futures once per cycle
        es_bars = nq_bars = []
        es_price = nq_price = 0
        futures_data: dict = {"available": False}
        if _FUTURES:
            try:
                es_bars   = _fetch_futures_bars("/ES", 5)
                nq_bars   = _fetch_futures_bars("/NQ", 5)
                es_q      = _fetch_futures_quote("/ES")
                nq_q      = _fetch_futures_quote("/NQ")
                es_price  = es_q.get("last", 0)
                nq_price  = nq_q.get("last", 0)
                es_vwap   = _compute_vwap(es_bars[-40:]) if es_bars else 0
                nq_vwap   = _compute_vwap(nq_bars[-40:]) if nq_bars else 0
                futures_data = {
                    "available":     True,
                    "es_price":      es_price,
                    "nq_price":      nq_price,
                    "es_vwap":       es_vwap,
                    "nq_vwap":       nq_vwap,
                    "es_above_vwap": es_price > es_vwap if es_vwap else None,
                    "nq_above_vwap": nq_price > nq_vwap if nq_vwap else None,
                }
                es_pos  = "↑" if es_price > es_vwap else "↓"
                nq_pos  = "↑" if nq_price > nq_vwap else "↓"
                print(f"  /ES {es_price} {es_pos} VWAP {es_vwap:.0f}  |  /NQ {nq_price} {nq_pos} VWAP {nq_vwap:.0f}")
            except Exception as e:
                print(f"  Futures fetch failed: {e}")

        fired_count = 0

        for ticker in tickers:
            try:
                ens    = fetch_signal(ticker, es_bars, nq_bars, es_price, nq_price)
                action = ens.get("action", "HOLD")
                bull   = ens.get("votes_bull", 0)
                bear   = ens.get("votes_bear", 0)
                score  = ens.get("score", 0)

                # ── Regime gate: filter wrong-direction signals ───────────────
                if action in ("BUY","SELL/SHORT") and not _regime_allows(action, regime):
                    print(f"  {ticker:6s} regime-blocked ({action} in {regime.get('regime','?')} day)")
                    continue

                # ── Vote threshold (regime-adjusted) ────────────────────────
                min_votes = _regime_min_votes(regime, hv)
                should_fire = (action == "BUY"       and bull >= min_votes) or \
                              (action == "SELL/SHORT" and bear >= min_votes)

                # Near-signal display (informational)
                if not should_fire and abs(score) >= 4 and action == "HOLD":
                    print(f"  {ticker:6s} ${ens.get('price',0):.2f}  near-signal "
                          f"(bulls={bull} bears={bear} score={score:+d})")
                    continue

                if not should_fire:
                    continue

                # ── 60-min cooldown ──────────────────────────────────────────
                last = last_signals.get(ticker, {})
                elapsed = (now_et - last.get("time", now_et - timedelta(hours=2))).total_seconds()
                if last.get("action") == action and elapsed < 3600:
                    mins_left = int((3600 - elapsed) / 60)
                    print(f"  {ticker:6s} cooldown ({mins_left}m)")
                    continue

                # ── Apply regime stop adjustments ────────────────────────────
                ens = _apply_regime_to_stops(ens, regime)

                # ── TimesFM pre-filter ───────────────────────────────────────
                close_prices = ens.get("close_prices", [])
                timesfm = _get_timesfm(ticker, close_prices)
                ens = _apply_timesfm_filter(ens, timesfm)

                # ── MASi confirmation (only on fired signals) ────────────────
                masi_result: dict | None = None
                if use_masi and _MASI_AVAILABLE:
                    masi_result = masi_confirm(
                        signal=ens,
                        regime=regime,
                        futures=futures_data,
                    )
                    if masi_result.get("verdict") == "VETO":
                        print(f"  {ticker:6s} 🚫 MASi VETO: {masi_result.get('reason','')[:60]}")
                        continue

                # ── Fire ──────────────────────────────────────────────────────
                print(_format_terminal(ens, regime, masi_result, hv))
                last_signals[ticker] = {"action": action, "time": now_et}
                fired_count += 1

                if notify:
                    send_alert(_format_whatsapp(ens, regime, masi_result, hv))

            except Exception as e:
                print(f"  {ticker}: error — {e}")

        if fired_count == 0:
            print(f"  No signals fired ({len(tickers)} tickers, regime={regime.get('regime','?')}).")

        if once:
            print(f"\n  [--once mode] scan complete.")
            break

        next_run = now_et + timedelta(seconds=interval)
        print(f"  Next scan: {next_run.strftime('%H:%M ET')}")
        time.sleep(interval)


# ── Debug helpers ─────────────────────────────────────────────────────────────

def debug_live_signal(ticker: str) -> dict:
    """For GET /debug/live-signal endpoint — return full signal payload."""
    es_bars = nq_bars = []
    es_price = nq_price = 0
    if _FUTURES:
        try:
            es_bars  = _fetch_futures_bars("/ES", 5)
            nq_bars  = _fetch_futures_bars("/NQ", 5)
            es_q     = _fetch_futures_quote("/ES")
            nq_q     = _fetch_futures_quote("/NQ")
            es_price = es_q.get("last", 0)
            nq_price = nq_q.get("last", 0)
        except Exception:
            pass

    ens     = fetch_signal(ticker.upper(), es_bars, nq_bars, es_price, nq_price)
    regime  = _load_regime()
    close_p = ens.get("close_prices", [])
    tf      = _get_timesfm(ticker.upper(), close_p)
    ens     = _apply_timesfm_filter(ens, tf)
    ens     = _apply_regime_to_stops(ens, regime)

    masi: dict | None = None
    if _MASI_AVAILABLE:
        masi = masi_confirm(ens, regime, None)

    return {
        "ticker":        ticker.upper(),
        "ensemble":      ens,
        "regime":        regime,
        "timesfm":       tf,
        "masi":          masi,
        "source_audit": {
            "ensemble":  "live_schwab",
            "regime":    regime.get("source_audit", {}),
            "timesfm":   {"available": tf.get("available"), "provider": tf.get("provider_mode","?")},
            "masi":      {"available": _MASI_AVAILABLE, "verdict": masi.get("verdict") if masi else None},
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MiroFish Alert Scanner v2")
    parser.add_argument("tickers", nargs="*", help="Custom ticker list")
    parser.add_argument("--watchlist","-w", default="all", choices=list(WATCHLISTS.keys()))
    parser.add_argument("--interval","-i", type=int, default=300)
    parser.add_argument("--no-notify",  action="store_true")
    parser.add_argument("--no-masi",    action="store_true", help="Skip MASi LLM confirmation")
    parser.add_argument("--once",       action="store_true", help="Single scan then exit (testing)")
    parser.add_argument("--debug",      action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    tickers = [t.upper() for t in args.tickers] if args.tickers else WATCHLISTS[args.watchlist]

    run_scanner(
        tickers  = tickers,
        interval = args.interval,
        notify   = not args.no_notify,
        use_masi = not args.no_masi,
        once     = args.once,
    )


if __name__ == "__main__":
    main()
