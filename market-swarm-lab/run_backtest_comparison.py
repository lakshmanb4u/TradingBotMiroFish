#!/usr/bin/env python3
"""
Batch backtest comparison: normal vs loose profiles for 8 tickers.
Date range: 2026-04-01 to 2026-04-25, 5min timeframe.
Saves aggregated results to JSON.
"""

import sys
import json
import os
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent
BT_DIR = ROOT / "services" / "backtest"
if str(BT_DIR) not in sys.path:
    sys.path.insert(0, str(BT_DIR))

# Also add other service dirs
for sd in [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "agent-seeder",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "price-collector",
]:
    if str(sd) not in sys.path:
        sys.path.insert(0, str(sd))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from point_in_time_replay import run_backtest

TICKERS = ["SPY", "QQQ", "NVDA", "AMD", "TSLA", "MSFT", "META", "AAPL"]
PROFILES = ["normal", "loose"]
START = "2026-04-01"
END = "2026-04-25"
TIMEFRAME = "5min"

RESULTS = {}

for ticker in TICKERS:
    RESULTS[ticker] = {}
    for profile in PROFILES:
        print(f"\n{'='*60}")
        print(f"Running backtest: {ticker} | profile={profile}")
        print(f"{'='*60}")
        try:
            result = run_backtest(
                ticker=ticker,
                start=START,
                end=END,
                timeframe=TIMEFRAME,
                confirm_with=[],
                use_masi=False,
                use_timesfm=False,
                threshold_profile=profile,
                debug_votes=False,
            )
            summary = result.get("summary", {})
            calibration = result.get("calibration", {})
            
            # Extract metrics
            entry = {
                "ticker": ticker,
                "profile": profile,
                "signals": summary.get("total_signals", 0),
                "win_rate_pct": summary.get("win_rate_pct", 0),
                "avg_win_r": summary.get("avg_win_r", 0),
                "avg_loss_r": summary.get("avg_loss_r", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "total_r": summary.get("total_r", 0),
                "max_drawdown_r": summary.get("max_drawdown_r", 0),
                "calls_count": summary.get("calls_count", 0),
                "calls_win_rate": summary.get("calls_win_rate", 0),
                "puts_count": summary.get("puts_count", 0),
                "puts_win_rate": summary.get("puts_win_rate", 0),
                "bars_evaluated": calibration.get("total_bars_evaluated", 0),
                "bars_with_3plus_votes": calibration.get("bars_with_3plus_votes", 0),
                "output_dir": result.get("output_dir", ""),
                "error": None,
            }
            RESULTS[ticker][profile] = entry
            print(f"  Signals: {entry['signals']}, Win Rate: {entry['win_rate_pct']}%, "
                  f"PF: {entry['profit_factor']}, Total R: {entry['total_r']}, "
                  f"Max DD: {entry['max_drawdown_r']}")
        except Exception as e:
            print(f"  ERROR: {e}")
            RESULTS[ticker][profile] = {
                "ticker": ticker,
                "profile": profile,
                "error": str(e),
            }

# Save aggregated results
OUTPUT_PATH = ROOT / "state" / "backtests" / "comparison_results.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_PATH, "w") as f:
    json.dump(RESULTS, f, indent=2)

print(f"\n{'='*60}")
print("AGGREGATED RESULTS")
print(f"{'='*60}")
for ticker in TICKERS:
    print(f"\n{ticker}:")
    for profile in PROFILES:
        r = RESULTS[ticker][profile]
        if r.get("error"):
            print(f"  {profile:8s}: ERROR — {r['error']}")
        else:
            print(f"  {profile:8s}: signals={r['signals']:3d}  win={r['win_rate_pct']:5.1f}%  "
                  f"avgR={r['total_r']:+7.3f}  PF={r['profit_factor']!s:>6}  "
                  f"maxDD={r['max_drawdown_r']:+7.3f}")

print(f"\nResults saved to: {OUTPUT_PATH}")
