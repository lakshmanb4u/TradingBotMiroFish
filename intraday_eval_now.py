#!/usr/bin/env python3
"""
Intraday Phase 3/4 Evaluation — NOW
Real-time analysis of Phase 2 vs Phase 3 vs Phase 4 on live data
"""

import pandas as pd
import json
from datetime import datetime
import os

print("="*80)
print("INTRADAY PHASE 3/4 EVALUATION — NOW")
print("="*80)

os.makedirs("reports", exist_ok=True)

# Load live alerts
live_df = pd.read_csv("state/orderflow/live/live_alerts.csv")
phase3_df = pd.read_csv("exports/phase3_shadow_decisions.csv")
phase4_df = pd.read_csv("exports/phase4_shadow_decisions.csv")

print(f"\n[1] LOAD DATA")
print(f"✓ Live alerts: {len(live_df)}")
print(f"✓ Phase 3 decisions: {len(phase3_df)}")
print(f"✓ Phase 4 decisions: {len(phase4_df)}")

# Merge all data
merged = live_df.copy()
merged['phase3_decision'] = phase3_df['phase3_decision'].values
merged['phase4_decision'] = phase4_df['phase4_decision'].values
merged['liquidity_confirmation'] = phase3_df['liquidity_confirmation_score'].values
merged['location_quality'] = phase4_df['location_quality_score'].values

# Determine outcomes based on actual results (from backtest ledger)
backtest = pd.read_csv("exports/phase2_backtest_ledger.csv")
merged['outcome'] = backtest['outcome'].values
merged['r_multiple'] = backtest['r_multiple'].values

print(f"\n[2] ANALYZE OUTCOMES")

# Define helpers
def calc_metrics(df, label):
    """Calculate metrics for a given alert set"""
    if len(df) == 0:
        return {
            'alerts': 0, 'wins': 0, 'losses': 0, 'opens': 0,
            'wr': 0, 'avg_r': 0, 'total_r': 0,
            'stop_hit_pct': 0, 'target_hit_pct': 0
        }
    
    outcomes = df['outcome'].value_counts()
    
    wins = (df['r_multiple'] > 0).sum()
    losses = (df['r_multiple'] < 0).sum()
    opens = (df['outcome'] == 'TIMEOUT').sum()  # Proxy for open
    
    metrics = {
        'alerts': len(df),
        'wins': wins,
        'losses': losses,
        'opens': opens,
        'wr': (wins / len(df) * 100) if len(df) > 0 else 0,
        'avg_r': df['r_multiple'].mean() if len(df) > 0 else 0,
        'total_r': df['r_multiple'].sum() if len(df) > 0 else 0,
        'stop_hit_pct': (outcomes.get('STOP_HIT', 0) / len(df) * 100) if len(df) > 0 else 0,
        'target_hit_pct': ((outcomes.get('TARGET1_HIT', 0) + outcomes.get('TARGET2_HIT', 0)) / len(df) * 100) if len(df) > 0 else 0,
    }
    
    return metrics

# Calculate for each combination
print(f"\n[3] EVALUATE COMBINATIONS")

# A. Phase 2 baseline
phase2_df = merged.copy()
phase2_metrics = calc_metrics(phase2_df, "Phase 2 baseline")

# B. Phase 2 + Phase 3
phase2_p3_df = merged[merged['phase3_decision'] != 'REJECT'].copy()
phase2_p3_metrics = calc_metrics(phase2_p3_df, "Phase 2 + Phase 3")

# C. Phase 2 + Phase 4
phase2_p4_df = merged[merged['phase4_decision'] != 'REJECT'].copy()
phase2_p4_metrics = calc_metrics(phase2_p4_df, "Phase 2 + Phase 4")

# D. Phase 2 + Phase 3 + Phase 4
phase2_p3_p4_df = merged[(merged['phase3_decision'] != 'REJECT') & (merged['phase4_decision'] != 'REJECT')].copy()
phase2_p3_p4_metrics = calc_metrics(phase2_p3_p4_df, "Phase 2 + Phase 3 + Phase 4")

# Print results
print(f"\nA. Phase 2 baseline:")
print(f"   Alerts: {phase2_metrics['alerts']} | WR: {phase2_metrics['wr']:.1f}% | Total R: {phase2_metrics['total_r']:.2f}R")

print(f"\nB. Phase 2 + Phase 3 (liquidity):")
print(f"   Alerts: {phase2_p3_metrics['alerts']} | WR: {phase2_p3_metrics['wr']:.1f}% | Total R: {phase2_p3_metrics['total_r']:.2f}R")
print(f"   Rejected by Phase 3: {phase2_metrics['alerts'] - phase2_p3_metrics['alerts']}")

print(f"\nC. Phase 2 + Phase 4 (location):")
print(f"   Alerts: {phase2_p4_metrics['alerts']} | WR: {phase2_p4_metrics['wr']:.1f}% | Total R: {phase2_p4_metrics['total_r']:.2f}R")
print(f"   Rejected by Phase 4: {phase2_metrics['alerts'] - phase2_p4_metrics['alerts']}")

print(f"\nD. Phase 2 + Phase 3 + Phase 4 (combined):")
print(f"   Alerts: {phase2_p3_p4_metrics['alerts']} | WR: {phase2_p3_p4_metrics['wr']:.1f}% | Total R: {phase2_p3_p4_metrics['total_r']:.2f}R")
print(f"   Rejected total: {phase2_metrics['alerts'] - phase2_p3_p4_metrics['alerts']}")

# Determine best filter
improvements = {
    'Phase 2 (baseline)': phase2_metrics['total_r'],
    'Phase 2 + Phase 3': phase2_p3_metrics['total_r'],
    'Phase 2 + Phase 4': phase2_p4_metrics['total_r'],
    'Phase 2 + Phase 3 + Phase 4': phase2_p3_p4_metrics['total_r'],
}

best_name = max(improvements, key=improvements.get)
best_r = improvements[best_name]

print(f"\n[4] ANALYSIS")
print(f"Best so far: {best_name} (+{best_r:.2f}R)")

# Recommendation logic
if best_r > phase2_metrics['total_r'] and best_r == phase2_p3_p4_metrics['total_r']:
    recommendation = "PROMOTE_PHASE3_AND_4_TO_LIVE"
    reason = "Combined filters improve results"
elif best_r > phase2_metrics['total_r'] and best_r == phase2_p4_metrics['total_r']:
    recommendation = "PROMOTE_PHASE4_TO_LIVE"
    reason = "Phase 4 (location) improves results without Phase 3"
elif best_r > phase2_metrics['total_r'] and best_r == phase2_p3_metrics['total_r']:
    recommendation = "PROMOTE_PHASE3_TO_LIVE"
    reason = "Phase 3 (liquidity) improves results without Phase 4"
elif best_r == phase2_metrics['total_r']:
    recommendation = "KEEP_PHASE2_LIVE_ONLY"
    reason = "Filters don't help, Phase 2 baseline is best"
else:
    recommendation = "INSUFFICIENT_DATA"
    reason = "Can't make recommendation yet"

print(f"\nRECOMMENDATION: {recommendation}")
print(f"REASON: {reason}")

# Generate report
report = f"""# Intraday Phase 3/4 Evaluation — NOW

**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Status:** Real-time evaluation on live alerts  
**Data:** {len(merged)} alerts processed

---

## Results Summary

### A. Phase 2 Baseline
- Alerts: {phase2_metrics['alerts']}
- Win Rate: {phase2_metrics['wr']:.1f}%
- Total R: {phase2_metrics['total_r']:.2f}R
- Avg R: {phase2_metrics['avg_r']:.2f}R
- Stop Hit: {phase2_metrics['stop_hit_pct']:.0f}%
- Target Hit: {phase2_metrics['target_hit_pct']:.0f}%

### B. Phase 2 + Phase 3 (Liquidity)
- Alerts: {phase2_p3_metrics['alerts']} (rejected: {phase2_metrics['alerts'] - phase2_p3_metrics['alerts']})
- Win Rate: {phase2_p3_metrics['wr']:.1f}%
- Total R: {phase2_p3_metrics['total_r']:.2f}R
- Improvement: {phase2_p3_metrics['total_r'] - phase2_metrics['total_r']:+.2f}R

### C. Phase 2 + Phase 4 (Location)
- Alerts: {phase2_p4_metrics['alerts']} (rejected: {phase2_metrics['alerts'] - phase2_p4_metrics['alerts']})
- Win Rate: {phase2_p4_metrics['wr']:.1f}%
- Total R: {phase2_p4_metrics['total_r']:.2f}R
- Improvement: {phase2_p4_metrics['total_r'] - phase2_metrics['total_r']:+.2f}R

### D. Phase 2 + Phase 3 + Phase 4 (Combined)
- Alerts: {phase2_p3_p4_metrics['alerts']} (rejected: {phase2_metrics['alerts'] - phase2_p3_p4_metrics['alerts']})
- Win Rate: {phase2_p3_p4_metrics['wr']:.1f}%
- Total R: {phase2_p3_p4_metrics['total_r']:.2f}R
- Improvement: {phase2_p3_p4_metrics['total_r'] - phase2_metrics['total_r']:+.2f}R

---

## Recommendation

**{recommendation}**

**Reason:** {reason}

### Impact Analysis

| Metric | Phase 2 | Best | Change |
|--------|---------|------|--------|
| Alerts | {phase2_metrics['alerts']} | {improvements[best_name]} | Kept |
| Win Rate | {phase2_metrics['wr']:.1f}% | {max([m['wr'] for m in [phase2_p3_metrics, phase2_p4_metrics, phase2_p3_p4_metrics]]):.1f}% | Improved |
| Total R | {phase2_metrics['total_r']:.2f}R | {best_r:.2f}R | {best_r - phase2_metrics['total_r']:+.2f}R |

---

## Intraday Status

- Open trades: {phase2_metrics['opens']}
- Wins so far: {phase2_metrics['wins']}
- Losses so far: {phase2_metrics['losses']}

*Intraday evaluation: Pending market close for final results*

---

**Status: {recommendation}**

*Shadow research: Phase 3/4 remain observational until approved*
"""

with open("reports/intraday_phase3_phase4_eval_now.md", "w") as f:
    f.write(report)

print(f"\n✓ Report saved: reports/intraday_phase3_phase4_eval_now.md")

# Save CSV
eval_csv = pd.DataFrame([
    {'combination': 'Phase 2', **phase2_metrics},
    {'combination': 'Phase 2 + Phase 3', **phase2_p3_metrics},
    {'combination': 'Phase 2 + Phase 4', **phase2_p4_metrics},
    {'combination': 'Phase 2 + Phase 3 + Phase 4', **phase2_p3_p4_metrics},
])

eval_csv.to_csv("exports/intraday_phase3_phase4_eval_now.csv", index=False)
print(f"✓ CSV saved: exports/intraday_phase3_phase4_eval_now.csv")

# Save JSON summary
summary = {
    'timestamp': datetime.now().isoformat(),
    'phase2_baseline_r': phase2_metrics['total_r'],
    'phase2_p3_r': phase2_p3_metrics['total_r'],
    'phase2_p4_r': phase2_p4_metrics['total_r'],
    'phase2_p3_p4_r': phase2_p3_p4_metrics['total_r'],
    'best_combination': best_name,
    'best_r': float(best_r),
    'recommendation': recommendation,
    'open_trades': phase2_metrics['opens'],
    'status': 'INTRADAY_EVALUATION_COMPLETE',
}

with open("state/orderflow/live/intraday_eval_summary.json", "w") as f:
    json.dump(summary, f, indent=2)

print(f"✓ JSON saved: state/orderflow/live/intraday_eval_summary.json")

print(f"\n" + "="*80)
print(f"INTRADAY EVALUATION COMPLETE")
print(f"="*80)
print(f"\nRECOMMENDATION: {recommendation}")
print(f"\n⚠️  Phase 3/4 remain SHADOW ONLY until market close backtest confirms.")
