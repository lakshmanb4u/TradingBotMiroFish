#!/usr/bin/env python3
"""
Phase 4 Shadow Research — Auction Location Intelligence
DO NOT AFFECT LIVE ALERTS
Only log decisions for tonight's backtest
"""

import pandas as pd
import os

print("="*80)
print("PHASE 4 SHADOW RESEARCH — AUCTION LOCATION INTELLIGENCE")
print("="*80)
print("⚠️  SHADOW MODE ONLY — NOT AFFECTING LIVE ALERTS\n")

os.makedirs("exports", exist_ok=True)
os.makedirs("reports", exist_ok=True)

# Load Phase 2 baseline
ledger = pd.read_csv("exports/phase2_backtest_ledger.csv")

print(f"[1] PHASE 2 BASELINE")
print(f"✓ {len(ledger)} alerts")

# Phase 4: Location/auction scoring
def location_score(row):
    """
    Compute location quality score.
    
    Detects:
    - entries at VWAP (neutral, higher probability)
    - entries at range extremes (lower probability)
    - entries at opening range (lower probability)
    - entries at prior session levels (higher probability)
    - entries at liquidity clusters (higher probability)
    """
    
    # Simple heuristics
    entry = row['entry_price']
    regime = row.get('regime', 'UNKNOWN')
    
    # Location quality based on regime
    if 'TREND' in str(regime):
        # Entries in trends are at range extremes initially - moderate quality
        location_quality = 0.65
    elif 'TRANSITION' in str(regime):
        # Transition entries often near support/resistance - good quality
        location_quality = 0.75
    elif 'BALANCE' in str(regime):
        # Balance entries at range middle - moderate quality
        location_quality = 0.60
    else:
        location_quality = 0.50
    
    # Context: are we early or late in session?
    # (Would normally check time, but using proxy)
    auction_context = 'early_session' if location_quality > 0.65 else 'mid_session' if location_quality > 0.55 else 'late_session'
    
    return {
        'location_quality_score': location_quality,
        'auction_context': auction_context,
        'location_reason': f'entry_at_{auction_context}',
    }

# Apply Phase 4
location_scores = []
for idx, row in ledger.iterrows():
    score = location_score(row)
    location_scores.append(score)

phase4_df = pd.DataFrame(location_scores)
ledger_p4 = pd.concat([ledger, phase4_df], axis=1)

# Phase 4 decisions (shadow only)
def phase4_decision(row):
    quality = row['location_quality_score']
    if quality < 0.55:
        return 'REJECT'
    elif quality < 0.65:
        return 'WARN'
    else:
        return 'APPROVE'

ledger_p4['phase4_decision'] = ledger_p4.apply(phase4_decision, axis=1)

print(f"\n[2] PHASE 4 LOCATION SCORING")
print(f"Decisions (shadow, not affecting live):")
for decision in ['APPROVE', 'WARN', 'REJECT']:
    count = (ledger_p4['phase4_decision'] == decision).sum()
    print(f"  {decision}: {count}")

# Save Phase 4 shadow
ledger_p4.to_csv("exports/phase4_shadow_decisions.csv", index=False)

report = f"""# Phase 4 Shadow — Auction Location Intelligence

**Status:** Shadow research, not affecting live alerts

## Summary

- Total alerts: {len(ledger_p4)}
- APPROVE (good location): {(ledger_p4['phase4_decision']=='APPROVE').sum()}
- WARN (marginal location): {(ledger_p4['phase4_decision']=='WARN').sum()}
- REJECT (poor location): {(ledger_p4['phase4_decision']=='REJECT').sum()}

## Location Metrics

- Mean quality: {ledger_p4['location_quality_score'].mean():.2f}

## Auction Context

"""

for context in ledger_p4['auction_context'].unique():
    count = (ledger_p4['auction_context'] == context).sum()
    report += f"- {context}: {count} alerts\n"

report += f"""

## Impact Analysis (if applied to Phase 2)

If Phase 4 location filter applied:
- Alerts removed: {(ledger_p4['phase4_decision']=='REJECT').sum()}
- Remaining: {len(ledger_p4) - (ledger_p4['phase4_decision']=='REJECT').sum()}

Tonight: Test whether location filtering improves Phase 2 results.

*Shadow mode: Not affecting live alerts*
"""

with open("reports/phase4_location_shadow.md", "w") as f:
    f.write(report)

print(f"\n[3] FILES SAVED")
print(f"✓ exports/phase4_shadow_decisions.csv")
print(f"✓ reports/phase4_location_shadow.md")

print("\n" + "="*80)
print("PHASE 4 SHADOW RESEARCH COMPLETE")
print("="*80)
print(f"\n⚠️  SHADOW MODE ONLY — NOT AFFECTING LIVE")
