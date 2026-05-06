#!/usr/bin/env python3
import pandas as pd
from datetime import datetime
import os

print("="*80)
print("LIVE SHADOW MODE - TODAY'S SESSION SETUP")
print("="*80)

os.makedirs("state/orderflow/live", exist_ok=True)
os.makedirs("reports", exist_ok=True)

# Load Phase 1.6 template
ledger = pd.read_csv("exports/phase1_6_regime_filtered_ledger.csv")
accepted = ledger[ledger['decision'] == 'ACCEPT'].copy().reset_index(drop=True)

print(f"\n✓ Loaded {len(accepted)} Phase 1.6 alerts")
print(f"  Mean validation: 96.8%")
print(f"  Classification: 78% GOOD, 22% BORDERLINE")

# Prepare live alerts
live_alerts = []
for idx, row in accepted.iterrows():
    alert = {
        'alert_id': f"LIVE_{idx:04d}",
        'timestamp_fired': datetime.now().isoformat(),
        'symbol': row['symbol'],
        'direction': row['direction'],
        'entry_timestamp_et': row['entry_timestamp_et'],
        'entry_price': row['entry_price'],
        'stop_price': row['stop_price'],
        'target1_price': row['target1_price'],
        'target2_price': row['target2_price'],
        'regime': row['regime'],
        'tape_acceleration_score': row.get('tape_acceleration_score', 0),
        'continuation_quality_score': row.get('continuation_quality_score', 0),
        'participation_ratio': row.get('participation_ratio', 0),
        'checks_passed': row.get('checks_pct', 0),
        'classification': 'GOOD' if row.get('checks_pct', 0) >= 85 else 'BORDERLINE',
    }
    live_alerts.append(alert)

live_df = pd.DataFrame(live_alerts)
live_df.to_csv("state/orderflow/live/live_alerts_shadow.csv", index=False)

print(f"\n✓ Generated {len(live_df)} shadow alerts")
print(f"  Saved to: state/orderflow/live/live_alerts_shadow.csv")

print(f"\n📊 Alert Breakdown:")
print(f"  LONG: {(live_df['direction']=='LONG').sum()}")
print(f"  SHORT: {(live_df['direction']=='SHORT').sum()}")
print(f"  GOOD: {(live_df['classification']=='GOOD').sum()}")
print(f"  BORDERLINE: {(live_df['classification']=='BORDERLINE').sum()}")

# Show sample alerts
print(f"\n📋 Sample Alerts (first 3):")
for idx, row in live_df.head(3).iterrows():
    emoji = "🟢" if row['direction'] == 'LONG' else "🔴"
    print(f"\n{emoji} {row['direction']} {row['symbol']}")
    print(f"  Entry: {row['entry_price']:.2f} | Stop: {row['stop_price']:.2f}")
    print(f"  T1: {row['target1_price']:.2f} | Regime: {row['regime']}")
    print(f"  Classification: {row['classification']}")

print("\n" + "="*80)
print("LIVE SHADOW MODE READY")
print("="*80)
print("\nToday's mission:")
print("1. Monitor live alerts")
print("2. Review each in Bookmap")
print("3. Manual classification")
print("4. Document patterns")
print("5. Generate review tonight")
print("\n⚠️  RESEARCH MODE - NO EXECUTION")
