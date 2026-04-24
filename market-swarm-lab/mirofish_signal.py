#!/usr/bin/env python3
"""MiroFish Signal CLI — run a decisive BUY/SELL/HOLD signal for any ticker.

Usage:
    python3 mirofish_signal.py SPY
    python3 mirofish_signal.py ARM CAR NVDA TSLA

Output: clear signal per ticker with entry, target, stop, R:R, confidence.
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SERVICE_DIRS = [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "price-collector",
]
for sd in SERVICE_DIRS:
    sp = str(sd)
    if sp not in sys.path:
        sys.path.insert(0, sp)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING)

from schwab_price_service import SchwabPriceService
from schwab_intraday_service import SchwabIntradayService
from uw_collector_service import UWCollectorService
from forecasting_service import TimesFMForecastingService
from signal_scorer import score_ticker


def run(ticker: str) -> dict:
    ticker = ticker.upper()
    print(f"  Fetching {ticker}...", end=" ", flush=True)

    price    = SchwabPriceService().collect(ticker)
    intraday = SchwabIntradayService().collect(ticker)
    uw       = UWCollectorService().collect(ticker, current_price=price["last_price"])
    forecast = TimesFMForecastingService().forecast_from_prices(ticker, price["close_prices"])

    result = score_ticker(price, intraday, uw, forecast)
    result["ticker"] = ticker
    print("done")
    return result


def print_signal(r: dict) -> None:
    action = r["ACTION"]
    color  = ""  # plain text for WhatsApp/terminal compatibility

    arrow = "▲" if action == "BUY" else ("▼" if action == "SELL/SHORT" else "—")

    print(f"\n{'='*52}")
    print(f"  {r['ticker']:6s}  ${r['price']:.2f}   {arrow} {action}  ({r['confidence']})")
    print(f"{'='*52}")

    if action != "HOLD":
        print(f"  Entry:      ${r['entry']:.2f}")
        print(f"  Target:     ${r['target']:.2f}")
        print(f"  Stop Loss:  ${r['stop_loss']:.2f}")
        print(f"  Risk/Reward {r['risk_reward']}")
    else:
        print(f"  No trade — signals conflicting or insufficient edge.")

    print(f"\n  Score: {r['score']:+d}  |  Reasons:")
    for reason in r["reasons"]:
        print(f"    • {reason}")

    w = r["why"]
    print(f"\n  Forecast:  {w['forecast']}")
    print(f"  UW Flow:   {w['uw_flow']}")
    print(f"  RSI:       {w['daily_rsi']:.1f}")
    print(f"  Momentum:  {w['momentum']}")
    print(f"  Intraday:  {w['intraday']}")

    # Masi strategies fired
    masi = [s for s in r.get("masi_strategies", []) if s.get("name") != "HIGH_VOLUME_WINDOW"]
    if masi:
        print(f"\n  Masi Strategies ({len(masi)} fired):")
        for s in masi:
            print(f"    + {s['name']}: {s['reason'][:75]}")

    for warn in r.get("masi_warnings", []):
        print(f"  !! {warn}")

    # Masi exit plan
    ep = r.get("masi_exit_plan", {})
    if ep and r["ACTION"] != "HOLD":
        print(f"\n  Masi Exit Plan:")
        print(f"    T1 (70% out): ${ep.get('target_1',0):.2f}  R:R {ep.get('risk_reward_t1','?')}")
        print(f"    T2 (30% runner): ${ep.get('target_2',0):.2f}  R:R {ep.get('risk_reward_t2','?')}")
        print(f"    Stop Loss:    ${ep.get('stop_loss',0):.2f}")


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["SPY"]

    print(f"\nMiroFish Signal Engine")
    print(f"Tickers: {', '.join(t.upper() for t in tickers)}\n")

    for ticker in tickers:
        try:
            result = run(ticker)
            print_signal(result)
        except Exception as exc:
            print(f"\n  {ticker.upper()}: ERROR — {exc}")

    print()
