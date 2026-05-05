#!/usr/bin/env python3
"""Compare full_runner vs partial_profit strategies"""
import csv
import statistics
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')

def load_trades(path):
    trades = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                'pnl_r': float(row['pnl_r']),
                'realized_pnl_r': float(row.get('realized_pnl_r', row['pnl_r'])),
                'mfe': float(row['mfe']),
                'mae': float(row['mae']),
                'exit_reason': row['exit_reason'],
                'partial_exits': row.get('partial_exits', ''),
                'max_stage': row.get('max_stage_reached', ''),
            })
    return trades

def analyze(trades, name):
    pnls = [t['pnl_r'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss else 999
    
    equity = [0]
    for p in pnls:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
    
    expectancy = statistics.mean(pnls) if pnls else 0
    median_r = statistics.median(pnls) if pnls else 0
    
    if len(pnls) > 2:
        mean = statistics.mean(pnls)
        std = statistics.stdev(pnls) if len(pnls) > 1 else 0.001
        skew = sum(((x - mean) / std) ** 3 for x in pnls) / len(pnls) if std else 0
    else:
        skew = 0
    
    sorted_pnls = sorted(pnls, reverse=True)
    top3 = sum(sorted_pnls[:3]) if len(sorted_pnls) >= 3 else sum(sorted_pnls)
    top3_pct = (top3 / total * 100) if total != 0 else 0
    
    stage1_hits = sum(1 for t in trades if 'stage1' in t['partial_exits'])
    stage2_hits = sum(1 for t in trades if 'stage2' in t['partial_exits'])
    stage3_hits = sum(1 for t in trades if 'stage3' in t['partial_exits'])
    
    runner_contrib = []
    for t in trades:
        if t['partial_exits'] and 'stage1' in t['partial_exits']:
            runner_contrib.append(t['realized_pnl_r'])
    
    return {
        'name': name,
        'total_trades': len(trades),
        'win_rate': win_rate,
        'total_r': total,
        'profit_factor': profit_factor,
        'max_dd': max_dd,
        'expectancy': expectancy,
        'median_r': median_r,
        'skew': skew,
        'top3_pct': top3_pct,
        'best': max(pnls) if pnls else 0,
        'worst': min(pnls) if pnls else 0,
        'avg_win': statistics.mean(wins) if wins else 0,
        'avg_loss': statistics.mean(losses) if losses else 0,
        'stage1_pct': stage1_hits / len(trades) * 100 if trades else 0,
        'stage2_pct': stage2_hits / len(trades) * 100 if trades else 0,
        'stage3_pct': stage3_hits / len(trades) * 100 if trades else 0,
        'avg_runner': statistics.mean(runner_contrib) if runner_contrib else 0,
    }

fr_trades = load_trades('state/backtests/replay/SPY_2026-04-01_2026-04-25_5min/trades.csv')
pp_trades = load_trades('state/backtests/replay/SPY_2026-04-01_2026-04-25_5min_partial_profit/trades.csv')

fr = analyze(fr_trades, 'FULL RUNNER')
pp = analyze(pp_trades, 'PARTIAL PROFIT')

print("=" * 70)
print("STRATEGY COMPARISON: SPY Apr 1-25, 2026 (5min, loose profile)")
print("=" * 70)

print(f"\n{'Metric':<25} {'Full Runner':>15} {'Partial Profit':>15} {'Delta':>10}")
print("-" * 70)

print(f"{'Total Trades':<25} {fr['total_trades']:>15.0f} {pp['total_trades']:>15.0f} {pp['total_trades']-fr['total_trades']:>+10.0f}")
print(f"{'Win Rate %':<25} {fr['win_rate']:>15.1f} {pp['win_rate']:>15.1f} {pp['win_rate']-fr['win_rate']:>+10.1f}")
print(f"{'Total R':<25} {fr['total_r']:>15.3f} {pp['total_r']:>15.3f} {pp['total_r']-fr['total_r']:>+10.3f}")
print(f"{'Profit Factor':<25} {fr['profit_factor']:>15.2f} {pp['profit_factor']:>15.2f} {pp['profit_factor']-fr['profit_factor']:>+10.2f}")
print(f"{'Max Drawdown R':<25} {fr['max_dd']:>15.2f} {pp['max_dd']:>15.2f} {pp['max_dd']-fr['max_dd']:>+10.2f}")
print(f"{'Expectancy R':<25} {fr['expectancy']:>15.3f} {pp['expectancy']:>15.3f} {pp['expectancy']-fr['expectancy']:>+10.3f}")
print(f"{'Median R':<25} {fr['median_r']:>15.3f} {pp['median_r']:>15.3f} {pp['median_r']-fr['median_r']:>+10.3f}")
print(f"{'Skew':<25} {fr['skew']:>15.2f} {pp['skew']:>15.2f} {pp['skew']-fr['skew']:>+10.2f}")
print(f"{'Top 3 Contribution %':<25} {fr['top3_pct']:>15.1f} {pp['top3_pct']:>15.1f} {pp['top3_pct']-fr['top3_pct']:>+10.1f}")
print(f"{'Best Trade R':<25} {fr['best']:>15.3f} {pp['best']:>15.3f} {pp['best']-fr['best']:>+10.3f}")
print(f"{'Worst Trade R':<25} {fr['worst']:>15.3f} {pp['worst']:>15.3f} {pp['worst']-fr['worst']:>+10.3f}")
print(f"{'Avg Win R':<25} {fr['avg_win']:>15.3f} {pp['avg_win']:>15.3f} {pp['avg_win']-fr['avg_win']:>+10.3f}")
print(f"{'Avg Loss R':<25} {fr['avg_loss']:>15.3f} {pp['avg_loss']:>15.3f} {pp['avg_loss']-fr['avg_loss']:>+10.3f}")

print("\n" + "=" * 70)
print("PARTIAL PROFIT STAGE METRICS")
print("=" * 70)
print(f"Stage 1 (+1R) hit rate:  {pp['stage1_pct']:.1f}%")
print(f"Stage 2 (+2R) hit rate:  {pp['stage2_pct']:.1f}%")
print(f"Stage 3 (+3R) hit rate:  {pp['stage3_pct']:.1f}%")
print(f"Avg runner contribution: {pp['avg_runner']:.3f}R")

print("\n" + "=" * 70)
print("ANALYSIS")
print("=" * 70)

if pp['total_r'] > fr['total_r']:
    print(f"✅ Partial profit WINS on total return: +{pp['total_r']:.2f}R vs +{fr['total_r']:.2f}R")
elif pp['total_r'] < fr['total_r']:
    print(f"⚠️  Full runner wins on total return: +{fr['total_r']:.2f}R vs +{pp['total_r']:.2f}R")
    print(f"   Sacrifice: {fr['total_r'] - pp['total_r']:.2f}R ({(fr['total_r'] - pp['total_r'])/fr['total_r']*100:.1f}%)")
else:
    print("🤝 Equal total return")

if pp['max_dd'] < fr['max_dd']:
    print(f"✅ Partial profit REDUCES drawdown: {pp['max_dd']:.2f}R vs {fr['max_dd']:.2f}R")
else:
    print(f"⚠️  Drawdown similar or worse: {pp['max_dd']:.2f}R vs {fr['max_dd']:.2f}R")

if pp['win_rate'] > fr['win_rate']:
    print(f"✅ Higher win rate: {pp['win_rate']:.1f}% vs {fr['win_rate']:.1f}%")

if abs(pp['skew']) < abs(fr['skew']):
    print(f"✅ Less skewed distribution: {pp['skew']:.2f} vs {fr['skew']:.2f}")
    print("   → More consistent, less outlier-dependent")

if pp['top3_pct'] < fr['top3_pct']:
    print(f"✅ Less concentrated in top 3: {pp['top3_pct']:.1f}% vs {fr['top3_pct']:.1f}%")
    print("   → More trades contributing to profit")

print("\n" + "=" * 70)
print("VERDICT")
print("=" * 70)

improvements = 0
if pp['max_dd'] < fr['max_dd']: improvements += 1
if pp['win_rate'] > fr['win_rate']: improvements += 1
if abs(pp['skew']) < abs(fr['skew']): improvements += 1
if pp['top3_pct'] < fr['top3_pct']: improvements += 1

if improvements >= 3:
    print("🎯 PARTIAL PROFIT SIGNIFICANTLY IMPROVES consistency")
    print("   Better for live trading with real capital")
elif improvements >= 2:
    print("✅ PARTIAL PROFIT MODERATELY IMPROVES risk profile")
    print("   Worth considering for production")
else:
    print("⚠️  PARTIAL PROFIT doesn't clearly improve over full runner")
    print("   May need stage adjustment or different market conditions")
