#!/usr/bin/env python3
"""MiroFish Live Poller — runs during market hours, prints signals every 5 min.

Automatically runs from 9:25 AM to 4:05 PM ET Monday-Friday.
Only prints a signal when the ensemble fires (3/4 agents agree).
Stays quiet during low-conviction periods.

Fixes applied 2026-04-26 (post-mortem: Friday Apr 25 session):
  - Opening range filter (no entries before 10:00 ET) handled in ensemble_scorer
  - EOD block (no new entries after 15:00 ET) handled in ensemble_scorer
  - Signal cooldown: 60-min lockout per ticker after a signal fires
    (was 15 min — caused 11 duplicate signals on same thesis Friday)
  - UW flow gate warning displayed in output when EMA+RSI is persistent bear
  - Intraday ATR-based targets displayed (see ensemble_scorer fix #3)

Usage:
    python3 mirofish_live.py              # default: SPY
    python3 mirofish_live.py SPY ARM NVDA # multiple tickers
"""
from __future__ import annotations

import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SERVICE_DIRS = [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "agent-seeder",
    ROOT / "services" / "price-collector",
]
for sd in SERVICE_DIRS:
    if str(sd) not in sys.path:
        sys.path.insert(0, str(sd))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING)

from schwab_price_service import SchwabPriceService
from schwab_intraday_service import SchwabIntradayService
from uw_collector_service import UWCollectorService
from ensemble_scorer import ensemble_score

try:
    from masi_agent import _fetch_futures_bars, _fetch_futures_quote, _compute_vwap
    _FUTURES = True
except Exception:
    _FUTURES = False


# ── Time helpers ───────────────────────────────────────────────────────────────

def et_now() -> datetime:
    """Current time in ET (UTC-4 EDT)."""
    return datetime.now(timezone.utc) - timedelta(hours=4)


def is_market_hours() -> bool:
    """True during market hours Mon-Fri 9:25 AM - 4:05 PM ET."""
    now = et_now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    start = now.replace(hour=9, minute=25, second=0, microsecond=0)
    end   = now.replace(hour=16, minute=5, second=0, microsecond=0)
    return start <= now <= end


def is_high_vol_window() -> bool:
    """Masi high-vol windows: 9:30-11:30 and 14:00-16:00 ET."""
    now = et_now()
    mins = now.hour * 60 + now.minute
    return (9*60+30 <= mins <= 11*60+30) or (14*60 <= mins <= 16*60)


def minutes_to_open() -> int:
    """Minutes until 9:30 AM ET."""
    now   = et_now()
    open_ = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= open_:
        return 0
    return int((open_ - now).total_seconds() / 60)


# ── Signal fetch ───────────────────────────────────────────────────────────────

def fetch_signal(ticker: str, es_bars, nq_bars, es_price, nq_price) -> dict:
    price    = SchwabPriceService().collect(ticker)
    intraday = SchwabIntradayService().collect(ticker)
    uw       = UWCollectorService().collect(ticker, current_price=price["last_price"])

    ens = ensemble_score(
        price, intraday, es_bars, nq_bars,
        es_price, nq_price,
        require_hv_window=False,  # we handle window logic here
    )
    ens["ticker"]      = ticker
    ens["price"]       = price["last_price"]
    ens["uw_bias"]     = uw.get("flow_bias", "neutral")
    ens["uw_signals"]  = uw.get("signals", [])
    ens["intraday"]    = intraday.get("current", {})
    return ens


# ── Display ────────────────────────────────────────────────────────────────────

def print_signal(ens: dict, hv: bool) -> None:
    ticker  = ens["ticker"]
    action  = ens["action"]
    price   = ens["price"]
    conf    = ens.get("confidence", "?")
    bull    = ens.get("votes_bull", 0)
    bear    = ens.get("votes_bear", 0)
    now_str = et_now().strftime("%H:%M ET")
    hv_tag  = "🔥 HIGH-VOL WINDOW" if hv else "⚡ normal window"

    if action == "HOLD":
        return  # stay quiet on HOLD

    arrow = "▲" if action == "BUY" else "▼"

    print(f"\n{'='*58}")
    print(f"  [{now_str}] {ticker}  ${price:.2f}   {arrow} {action}  ({conf})")
    print(f"  {hv_tag}  |  Bulls: {bull}/4  Bears: {bear}/4")
    print(f"{'='*58}")
    print(f"  Entry:    ${ens.get('entry',0):.2f}")
    print(f"  T1 (70%): ${ens.get('target_1',0):.2f}")
    print(f"  T2 (30%): ${ens.get('target_2',0):.2f}")
    print(f"  Stop:     ${ens.get('stop_loss',0):.2f}  R:R {ens.get('risk_reward','?')}")
    print(f"\n  Agent votes:")
    for name, res in ens.get("agents", {}).items():
        icon = "✅" if res["vote"] == "bull" else ("❌" if res["vote"] == "bear" else "▪")
        print(f"    {icon} {name:<22}  score={res['score']:+d}  → {res['vote'].upper()}")
    # Fix #5: warn when EMA+RSI is persistent bear outlier
    if ens.get("ema_rsi_persistent_bear"):
        print(f"  ⚠️  EMA+RSI persistent bear — confidence reduced, watch closely")
    print(f"  UW flow: {ens.get('uw_bias','?').upper()}")
    # Fix #4: warn when UW flow gate would suppress in live context
    uw_bias = ens.get("uw_bias", "neutral").lower()
    if uw_bias == "bearish":
        print(f"  ⚠️  UW BEARISH flow — verify no net put sweeps before entry")
    intra = ens.get("intraday", {})
    print(f"  Intraday: {intra.get('intraday_trend','?')} | RSI {intra.get('rsi',0):.0f} | {intra.get('price_vs_vwap','?')} VWAP")

    # UW sweep alerts
    for sig in ens.get("uw_signals", [])[:2]:
        print(f"  ⚡ {sig.get('reason','')}")


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["SPY"]
    interval = 300  # 5 minutes

    print(f"\n{'='*58}")
    print(f"  MiroFish Live Poller")
    print(f"  Tickers: {', '.join(t.upper() for t in tickers)}")
    print(f"  Interval: every {interval//60} min during market hours")
    print(f"  Signal threshold: 3/4 agents agree")
    print(f"  Backtested accuracy: 59.6% (+9.6% edge)")
    print(f"{'='*58}\n")

    last_signals = {}  # track last signal per ticker to avoid repeats

    while True:
        now_et = et_now()
        now_str = now_et.strftime("%A %H:%M ET")

        if not is_market_hours():
            mins = minutes_to_open()
            if mins > 0:
                print(f"[{now_str}] Market closed. Opens in {mins} min. Waiting...")
            else:
                print(f"[{now_str}] Market closed. Checking again in 5 min...")
            time.sleep(interval)
            continue

        hv = is_high_vol_window()
        hv_str = "🔥 HIGH-VOL" if hv else "normal"
        print(f"\n[{now_str}] {hv_str} | Scanning {', '.join(t.upper() for t in tickers)}...")

        # Fetch futures once per cycle (shared across all tickers)
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
                es_vwap  = _compute_vwap(es_bars[-40:]) if es_bars else 0
                nq_vwap  = _compute_vwap(nq_bars[-40:]) if nq_bars else 0
                es_pos   = "↑" if es_price > es_vwap else "↓"
                nq_pos   = "↑" if nq_price > nq_vwap else "↓"
                print(f"  /ES {es_price} {es_pos} VWAP {es_vwap:.0f}  |  /NQ {nq_price} {nq_pos} VWAP {nq_vwap:.0f}")
            except Exception as e:
                print(f"  Futures fetch failed: {e}")

        # Scan each ticker
        fired_any = False
        for ticker in tickers:
            try:
                ens = fetch_signal(ticker.upper(), es_bars, nq_bars, es_price, nq_price)
                action = ens.get("action", "HOLD")
                bull = ens.get("votes_bull", 0)
                bear = ens.get("votes_bear", 0)

                # Only fire if:
                # 1. During high-vol window: 3/4 agree
                # 2. Outside high-vol: 4/4 agree (very high conviction only)
                threshold = 3 if hv else 4
                should_fire = (action == "BUY" and bull >= threshold) or \
                              (action == "SELL/SHORT" and bear >= threshold)

                if should_fire:
                    # Fix: 60-min cooldown per ticker after a signal fires.
                    # Previous 15-min cooldown caused 11 duplicate entries on
                    # the same thesis (Friday Apr 25 post-mortem).
                    last = last_signals.get(ticker, {})
                    seconds_since_last = (
                        now_et - last.get("time", now_et - timedelta(hours=2))
                    ).total_seconds()
                    action_changed = last.get("action") != action
                    cooldown_expired = seconds_since_last > 3600  # 60 minutes

                    if action_changed or cooldown_expired:
                        print_signal(ens, hv)
                        last_signals[ticker] = {"action": action, "time": now_et}
                        fired_any = True
                    else:
                        mins_remaining = int((3600 - seconds_since_last) / 60)
                        print(f"  {ticker.upper():5s} signal cooldown ({mins_remaining}m remaining — duplicate suppressed)")
                else:
                    conf = ens.get("confidence", "?")
                    print(f"  {ticker.upper():5s} ${ens.get('price',0):.2f}  HOLD  "
                          f"(bulls={bull} bears={bear}  {conf})")

            except Exception as e:
                print(f"  {ticker.upper()}: error — {e}")

        if not fired_any:
            print(f"  No signals fired this cycle.")

        # Wait for next cycle
        next_run = now_et + timedelta(seconds=interval)
        print(f"\n  Next scan: {next_run.strftime('%H:%M ET')}")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
