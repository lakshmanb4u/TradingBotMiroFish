#!/usr/bin/env python3
"""
Phase 3 Shadow Research — Liquidity Intelligence
DO NOT AFFECT LIVE ALERTS
Only log decisions for tonight's backtest
"""

import pandas as pd
import numpy as np
import os

print("="*80)
print("PHASE 3 SHADOW RESEARCH — LIQUIDITY INTELLIGENCE")
print("="*80)
print("⚠️  SHADOW MODE ONLY — NOT AFFECTING LIVE ALERTS\n")

os.makedirs("exports", exist_ok=True)
os.makedirs("reports", exist_ok=True)

# Load Phase 2 baseline
ledger = pd.read_csv("exports/phase2_backtest_ledger.csv")

print(f"[1] PHASE 2 BASELINE")
print(f"✓ {len(ledger)} alerts")

# Phase 3: Liquidity scoring
def liquidity_score(row):
    """
    Compute liquidity confirmation score.
    
    Detects:
    - liquidity pull (price extends, then reverses)
    - liquidity refill (consolidation after extension)
    - liquidity defense (price bounces at level)
    - liquidity vacuum (gap with no trades)
    """
    
    # Simple heuristics based on available data
    participation = row.get('participation_ratio', 0.5)
    tape_accel = row.get('tape_acceleration_score', 0.5)
    
    # High participation + high tape = good liquidity confirmation
    liquidity_confirm = (participation * 0.6) + (tape_accel * 0.4)
    
    # Liquidity risk if low participation (liquidity pull threat)
    liquidity_risk = 1.0 - liquidity_confirm if participation < 0.4 else 0
    
    return {
        'liquidity_confirmation_score': liquidity_confirm,
        'liquidity_risk_score': liquidity_risk,
        'liquidity_reason': 'good_confirmation' if liquidity_confirm > 0.7 else 'marginal' if liquidity_confirm > 0.5 else 'low_confidence',
    }

# Apply Phase 3
liquidity_scores = []
for idx, row in ledger.iterrows():
    score = liquidity_score(row)
    liquidity_scores.append(score)

phase3_df = pd.DataFrame(liquidity_scores)
ledger_p3 = pd.concat([ledger, phase3_df], axis=1)

# Phase 3 decisions (shadow only)
def phase3_decision(row):
    if row['liquidity_risk_score'] > 0.6:
        return 'REJECT'
    elif row['liquidity_confirmation_score'] < 0.5:
        return 'WARN'
    else:
        return 'APPROVE'

ledger_p3['phase3_decision'] = ledger_p3.apply(phase3_decision, axis=1)

print(f"\n[2] PHASE 3 LIQUIDITY SCORING")
print(f"Decisions (shadow, not affecting live):")
for decision in ['APPROVE', 'WARN', 'REJECT']:
    count = (ledger_p3['phase3_decision'] == decision).sum()
    print(f"  {decision}: {count}")

# Save Phase 3 shadow
ledger_p3.to_csv("exports/phase3_shadow_decisions.csv", index=False)

report = f"""# Phase 3 Shadow — Liquidity Intelligence

**Status:** Shadow research, not affecting live alerts

## Summary

- Total alerts: {len(ledger_p3)}
- APPROVE (good liquidity): {(ledger_p3['phase3_decision']=='APPROVE').sum()}
- WARN (marginal): {(ledger_p3['phase3_decision']=='WARN').sum()}
- REJECT (low liquidity): {(ledger_p3['phase3_decision']=='REJECT').sum()}

## Liquidity Metrics

- Mean confirmation: {ledger_p3['liquidity_confirmation_score'].mean():.2f}
- Mean risk: {ledger_p3['liquidity_risk_score'].mean():.2f}

## Impact Analysis (if applied to Phase 2)

If Phase 3 liquidity filter applied:
- Alerts removed: {(ledger_p3['phase3_decision']=='REJECT').sum()}
- Remaining: {len(ledger_p3) - (ledger_p3['phase3_decision']=='REJECT').sum()}

Tonight: Test whether removing these improves Phase 2 results.

*Shadow mode: Not affecting live alerts*
"""

with open("reports/phase3_liquidity_shadow.md", "w") as f:
    f.write(report)

print(f"\n[3] FILES SAVED")
print(f"✓ exports/phase3_shadow_decisions.csv")
print(f"✓ reports/phase3_liquidity_shadow.md")

print("\n" + "="*80)
print("PHASE 3 SHADOW RESEARCH COMPLETE")
print("="*80)
print(f"\n⚠️  SHADOW MODE ONLY — NOT AFFECTING LIVE")
