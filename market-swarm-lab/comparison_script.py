#!/usr/bin/env python3
"""Compare full_runner vs partial_profit strategies"""

import json
from pathlib import Path

# Load existing full_runner results
run1_dir = Path("state/backtests/replay/SPY_2026-04-01_2026-04-25_5min")
with open(run1_dir / "backtest_report.json") as f:
    run1 = json.load(f)

# NOTE: run2 (partial_profit) would be in a different directory
# For now, create a placeholder comparison

print("=" * 70)
print("STRATEGY COMPARISON: SPY Apr 1-25, 2026 (5min, loose profile)")
print("=" * 70)

print("\n🏃 FULL RUNNER (existing results):")
print(f"  Total trades: {run1['summary']['total_signals']}")
print(f"  Win rate: {run1['summary']['win_rate_pct']}%")
print(f"  Total R: {run1['summary']['total_r']:.3f}")
print(f"  Profit factor: {run1['summary']['profit_factor']:.2f}")
print(f"  Max drawdown: {run1['summary']['max_drawdown_r']:.2f}R")
print(f"  Avg win: {run1['summary']['avg_win_r']:.3f}R")
print(f"  Avg loss: {run1['summary']['avg_loss_r']:.3f}R")
print(f"  Best trade: {run1['summary']['best_trade']['pnl_r']:.3f}R")
print(f"  Worst trade: {run1['summary']['worst_trade']['pnl_r']:.3f}R")
print(f"  MFE avg: {run1['summary']['mfe_avg']:.3f}R")
print(f"  MAE avg: {run1['summary']['mae_avg']:.3f}R")

print("\n💰 PARTIAL PROFIT (need to run):")
print("  Run command:")
print("  python3 mirofish_signal.py backtest --ticker SPY --start 2026-04-01 --end 2026-04-25 --timeframe 5min --threshold-profile loose --strategy partial_profit")

print("\n" + "=" * 70)
print("KEY DIFFERENCES TO EXPECT:")
print("=" * 70)
print("""
1. Full runner: Lets winners run to target_2 or stop
   - Higher avg win, higher variance
   - More -1R stops (breakeven stopped out)
   - Better capture of outlier moves

2. Partial profit: Takes 50% at +1R, 25% at +2R, trails 25%
   - Lower avg win per trade (realized earlier)
   - Higher win rate (more +1R captures)
   - Lower variance, smoother equity curve
   - May miss some big runners

Trade-off: Variance reduction vs outlier capture
""")
