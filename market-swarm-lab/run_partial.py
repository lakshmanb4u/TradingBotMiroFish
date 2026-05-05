#!/usr/bin/env python3
import sys
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/agent-seeder')
from point_in_time_replay import run_backtest

result = run_backtest(
    ticker="SPY", start="2026-04-01", end="2026-04-25",
    timeframe="5min", threshold_profile="loose", strategy="partial_profit"
)

s = result['summary']
print(f"\n{'='*60}")
print("PARTIAL PROFIT RESULTS")
print(f"{'='*60}")
print(f"Total trades: {s['total_signals']}")
print(f"Win rate: {s['win_rate_pct']}%")
print(f"Total R: {s['total_r']:.3f}")
print(f"Profit factor: {s['profit_factor']:.2f}")
print(f"Max drawdown: {s['max_drawdown_r']:.2f}R")
print(f"Expectancy: {s['expectancy']:.3f}R")
print(f"Median R: {s['median_r']:.3f}R")
print(f"Skew: {s['skew']:.2f}")
print(f"Top 3 contribution: {s['top3_contribution_pct']:.1f}%")
print(f"Best trade: {s['best_trade']['pnl_r']:.3f}R")
print(f"Worst trade: {s['worst_trade']['pnl_r']:.3f}R")
print(f"Avg win: {s['avg_win_r']:.3f}R")
print(f"Avg loss: {s['avg_loss_r']:.3f}R")

pm = s.get('partial_metrics', {})
print(f"\nSTAGE HITS:")
print(f"  Stage 1 (+1R): {pm.get('stage1_hit_pct', 0):.1f}%")
print(f"  Stage 2 (+2R): {pm.get('stage2_hit_pct', 0):.1f}%")
print(f"  Stage 3 (+3R): {pm.get('stage3_hit_pct', 0):.1f}%")
print(f"  Avg runner contribution: {pm.get('avg_runner_contribution_r', 0):.3f}R")
