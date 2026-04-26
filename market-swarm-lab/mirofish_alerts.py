#!/usr/bin/env python3
"""MiroFish Multi-Ticker Alert Scanner

Scans a watchlist of popular symbols every 5 minutes during market hours.
Fires when 3/4 ensemble agents agree AND all post-mortem filters pass:
  - No entries before 10:00 ET (opening range filter)
  - No new entries after 15:00 ET (EOD block)
  - 60-min cooldown per ticker after a signal fires
  - UW flow gate: suppresses BUY on bearish flow + net put sweeps

Delivers alerts via:
  1. Terminal stdout (always)
  2. openclaw-notify CLI (WhatsApp/Telegram via OpenClaw gateway)

Usage:
    python3 mirofish_alerts.py                    # default watchlist
    python3 mirofish_alerts.py NVDA TSLA AMZN     # custom tickers

Watchlists (select with --watchlist):
    mega     — AAPL MSFT GOOGL META AMZN NVDA TSLA (Magnificent 7)
    semis    — NVDA AMD QCOM ARM AVGO INTC ASML AMAT KLAC
    ai       — NVDA META MSFT GOOGL AMZN CLS CDNS SNPS FN ANET
    options  — SPY QQQ SPX high-IV movers for options flow
    all      — full 30-ticker universe
"""
from __future__ import annotations

import sys
import time
import subprocess
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


# ── Watchlists ─────────────────────────────────────────────────────────────────

WATCHLISTS: dict[str, list[str]] = {
    "mega": [
        "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "TSLA",
    ],
    "semis": [
        "NVDA", "AMD", "QCOM", "ARM", "AVGO", "INTC", "ASML", "AMAT", "KLAC", "LRCX",
    ],
    "ai": [
        "NVDA", "META", "MSFT", "GOOGL", "AMZN", "CLS", "CDNS", "SNPS", "FN", "ANET",
        "VRT", "SMCI", "ARM", "PLTR",
    ],
    "options": [
        "SPY", "QQQ", "NVDA", "TSLA", "META", "AAPL", "MSFT", "GOOGL", "AMD", "ARM",
    ],
    "all": [
        # Magnificent 7
        "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "TSLA",
        # Semis
        "AMD", "QCOM", "ARM", "AVGO", "INTC", "ASML", "AMAT", "KLAC", "LRCX",
        # AI infra
        "CLS", "CDNS", "SNPS", "FN", "ANET", "VRT", "SMCI",
        # Indices
        "SPY", "QQQ",
        # Other high-volume
        "NFLX", "UBER", "SHOP", "PLTR", "COIN",
    ],
}

DEFAULT_WATCHLIST = "all"


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


# ── Notify via OpenClaw (WhatsApp/Telegram) ────────────────────────────────────

def send_alert(message: str) -> None:
    """Send alert via openclaw-notify to WhatsApp gateway."""
    try:
        subprocess.run(
            ["openclaw", "notify", message],
            capture_output=True, timeout=10
        )
    except Exception:
        pass  # Terminal output is always the fallback


# ── Signal fetch ───────────────────────────────────────────────────────────────

def fetch_signal(ticker: str, es_bars, nq_bars, es_price, nq_price) -> dict:
    price    = SchwabPriceService().collect(ticker)
    intraday = SchwabIntradayService().collect(ticker)
    uw       = UWCollectorService().collect(ticker, current_price=price["last_price"])

    # Enrich price dict with UW flow for the gate in ensemble_scorer
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
    return ens


# ── Format signal for terminal + WhatsApp ─────────────────────────────────────

def format_terminal(ens: dict, hv: bool) -> str:
    ticker  = ens["ticker"]
    action  = ens["action"]
    price   = ens["price"]
    conf    = ens.get("confidence", "?")
    bull    = ens.get("votes_bull", 0)
    bear    = ens.get("votes_bear", 0)
    now_str = et_now().strftime("%H:%M ET")
    hv_tag  = "🔥 HIGH-VOL" if hv else "⚡ normal"
    arrow   = "▲" if action == "BUY" else "▼"

    lines = [
        f"\n{'='*58}",
        f"  [{now_str}] {ticker}  ${price:.2f}   {arrow} {action}  ({conf})",
        f"  {hv_tag}  |  Bulls: {bull}/4  Bears: {bear}/4",
        f"{'='*58}",
        f"  Entry:    ${ens.get('entry', 0):.2f}",
        f"  T1 (70%): ${ens.get('target_1', 0):.2f}",
        f"  T2 (30%): ${ens.get('target_2', 0):.2f}",
        f"  Stop:     ${ens.get('stop_loss', 0):.2f}  R:R {ens.get('risk_reward', '?')}",
        f"\n  Agent votes:",
    ]
    for name, res in ens.get("agents", {}).items():
        icon = "✅" if res["vote"] == "bull" else ("❌" if res["vote"] == "bear" else "▪")
        lines.append(f"    {icon} {name:<22}  score={res['score']:+d}  → {res['vote'].upper()}")
    if ens.get("ema_rsi_persistent_bear"):
        lines.append("  ⚠️  EMA+RSI persistent bear — confidence reduced")
    lines.append(f"  UW flow: {ens.get('uw_bias', '?').upper()}")
    if ens.get("uw_bias", "").lower() == "bearish":
        lines.append("  ⚠️  UW BEARISH flow — verify no net put sweeps before entry")
    intra = ens.get("intraday", {})
    lines.append(f"  Intraday: {intra.get('intraday_trend','?')} | RSI {intra.get('rsi', 0):.0f} | {intra.get('price_vs_vwap','?')} VWAP")
    for sig in ens.get("uw_signals", [])[:2]:
        lines.append(f"  ⚡ {sig.get('reason', '')}")
    return "\n".join(lines)


def format_whatsapp(ens: dict, hv: bool) -> str:
    """Compact single-message format for WhatsApp."""
    ticker  = ens["ticker"]
    action  = ens["action"]
    price   = ens["price"]
    conf    = ens.get("confidence", "?")
    bull    = ens.get("votes_bull", 0)
    bear    = ens.get("votes_bear", 0)
    entry   = ens.get("entry", 0)
    t1      = ens.get("target_1", 0)
    t2      = ens.get("target_2", 0)
    stop    = ens.get("stop_loss", 0)
    rr      = ens.get("risk_reward", "?")
    now_str = et_now().strftime("%H:%M ET")
    arrow   = "▲" if action == "BUY" else "▼"
    hv_tag  = "HIGH-VOL" if hv else "normal"
    uw      = ens.get("uw_bias", "neutral").upper()

    flags = []
    if ens.get("ema_rsi_persistent_bear"):
        flags.append("⚠️ EMA bear outlier")
    if uw == "BEARISH":
        flags.append("⚠️ UW bearish flow")
    flag_str = "  " + " | ".join(flags) if flags else ""

    msg = (
        f"🐟 MiroFish Signal [{now_str}]\n"
        f"{arrow} *{ticker}* ${price:.2f} — {action} ({conf}) [{hv_tag}]\n"
        f"Bulls: {bull}/4  Bears: {bear}/4  UW: {uw}\n"
        f"Entry: ${entry:.2f}  Stop: ${stop:.2f}  R:R {rr}\n"
        f"T1 (70%): ${t1:.2f}  T2 (30%): ${t2:.2f}"
    )
    if flag_str:
        msg += f"\n{flag_str}"
    # UW sweep alerts
    sweeps = [s.get("reason", "") for s in ens.get("uw_signals", [])[:1] if s.get("reason")]
    if sweeps:
        msg += f"\n⚡ {sweeps[0]}"
    return msg


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MiroFish Multi-Ticker Alert Scanner")
    parser.add_argument("tickers", nargs="*", help="Tickers to scan (overrides --watchlist)")
    parser.add_argument("--watchlist", "-w", default=DEFAULT_WATCHLIST,
                        choices=list(WATCHLISTS.keys()),
                        help=f"Named watchlist (default: {DEFAULT_WATCHLIST})")
    parser.add_argument("--interval", "-i", type=int, default=300,
                        help="Scan interval in seconds (default: 300 = 5 min)")
    parser.add_argument("--no-notify", action="store_true",
                        help="Terminal only, skip openclaw notify")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else WATCHLISTS[args.watchlist]
    interval = args.interval
    notify   = not args.no_notify

    print(f"\n{'='*58}")
    print(f"  MiroFish Alert Scanner")
    print(f"  Watchlist: {args.watchlist if not args.tickers else 'custom'}")
    print(f"  Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"  Interval: {interval//60} min  |  Notify: {'on' if notify else 'off'}")
    print(f"  Filters: ORF 10:00 ET | EOD 15:00 ET | 60-min cooldown")
    print(f"  Threshold: 3/4 agents agree")
    print(f"{'='*58}\n")

    last_signals: dict[str, dict] = {}  # ticker -> {action, time}

    while True:
        now_et  = et_now()
        now_str = now_et.strftime("%A %H:%M ET")

        if not is_market_hours():
            mins = minutes_to_open()
            if mins > 0:
                print(f"[{now_str}] Market closed. Opens in {mins} min.")
            else:
                print(f"[{now_str}] Market closed.")
            time.sleep(interval)
            continue

        hv      = is_high_vol_window()
        hv_str  = "🔥 HIGH-VOL" if hv else "normal"
        batch   = len(tickers)
        print(f"\n[{now_str}] {hv_str} | Scanning {batch} tickers...")

        # Fetch futures once per cycle
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

        fired_count = 0

        for ticker in tickers:
            try:
                ens    = fetch_signal(ticker, es_bars, nq_bars, es_price, nq_price)
                action = ens.get("action", "HOLD")
                bull   = ens.get("votes_bull", 0)
                bear   = ens.get("votes_bear", 0)

                # Threshold: 3/4 in HV window, 4/4 outside
                threshold   = 3 if hv else 4
                should_fire = (action == "BUY"        and bull >= threshold) or \
                              (action == "SELL/SHORT"  and bear >= threshold)

                if should_fire:
                    last    = last_signals.get(ticker, {})
                    elapsed = (now_et - last.get("time", now_et - timedelta(hours=2))).total_seconds()
                    changed = last.get("action") != action
                    ready   = elapsed > 3600  # 60-min cooldown

                    if changed or ready:
                        # Print to terminal
                        print(format_terminal(ens, hv))
                        last_signals[ticker] = {"action": action, "time": now_et}
                        fired_count += 1

                        # Send WhatsApp alert
                        if notify:
                            send_alert(format_whatsapp(ens, hv))
                    else:
                        mins_left = int((3600 - elapsed) / 60)
                        print(f"  {ticker:6s} cooldown ({mins_left}m remaining)")
                else:
                    # Quiet on HOLD — only print if score is interesting
                    score = ens.get("score", 0)
                    if abs(score) >= 4 and action == "HOLD":
                        print(f"  {ticker:6s} ${ens.get('price',0):.2f}  near-signal "
                              f"(bulls={bull} bears={bear} score={score:+d})")

            except Exception as e:
                print(f"  {ticker}: error — {e}")

        if fired_count == 0:
            print(f"  No signals fired this cycle ({batch} tickers scanned).")

        next_run = now_et + timedelta(seconds=interval)
        print(f"  Next scan: {next_run.strftime('%H:%M ET')}")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
