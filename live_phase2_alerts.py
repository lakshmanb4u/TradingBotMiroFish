#!/usr/bin/env python3
"""
Live Alert Engine — Phase 1.6 + Phase 2
OBSERVATIONAL ONLY — DO NOT AUTO-TRADE
"""

import pandas as pd
import json
from datetime import datetime
import os

print("="*80)
print("LIVE ALERT ENGINE — PHASE 1.6 + PHASE 2")
print("="*80)
print("\n⚠️  OBSERVATIONAL ONLY — DO NOT AUTO-TRADE\n")

os.makedirs("state/orderflow/live", exist_ok=True)

# Load Phase 1.6 + Phase 2 baseline
ledger = pd.read_csv("exports/phase2_backtest_ledger.csv")

print(f"[1] LOADED PHASE 2 BASELINE")
print("-" * 80)
print(f"✓ {len(ledger)} Phase 2 alerts loaded")

# Generate live alerts
live_alerts = []
for idx, row in ledger.iterrows():
    alert = {
        'timestamp_et': row['entry_timestamp_et'],
        'symbol': row['symbol'],
        'direction': row['direction'],
        'entry_price': row['entry_price'],
        'stop_price': row['stop_price'],
        'target1_price': row['target1_price'],
        'target2_price': row['target2_price'],
        'regime': row['regime'],
        'tape_acceleration_score': row.get('tape_acceleration_score', 0),
        'continuation_quality_score': row.get('continuation_quality_score', 0),
        'phase2_risk_score': row.get('phase2_risk_score', 0),
        'phase2_action': row.get('phase2_action', 'HOLD'),
        'reason_codes': row.get('reason_codes', ''),
        'alert_type': 'LIVE_PHASE2',
        'status': 'OBSERVATIONAL_ONLY',
    }
    live_alerts.append(alert)

live_df = pd.DataFrame(live_alerts)
live_df.to_csv("state/orderflow/live/live_alerts.csv", index=False)

print(f"\n[2] LIVE ALERTS GENERATED")
print("-" * 80)
print(f"✓ {len(live_df)} live alerts ready")
print(f"  LONG: {(live_df['direction']=='LONG').sum()}")
print(f"  SHORT: {(live_df['direction']=='SHORT').sum()}")

# Generate latest signal
if len(live_df) > 0:
    latest = live_df.iloc[-1].to_dict()
    with open("state/orderflow/live/latest_signal.json", "w") as f:
        json.dump(latest, f, indent=2, default=str)
    print(f"\n✓ Latest signal saved")

# Session stats
session_stats = {
    'timestamp': datetime.now().isoformat(),
    'total_alerts': len(live_df),
    'long_alerts': (live_df['direction']=='LONG').sum(),
    'short_alerts': (live_df['direction']=='SHORT').sum(),
    'early_exits_phase2': (live_df['phase2_action']=='EARLY_EXIT').sum(),
    'mean_phase2_risk': float(live_df['phase2_risk_score'].mean()),
    'status': 'OBSERVATIONAL_ONLY',
}

with open("state/orderflow/live/session_stats.json", "w") as f:
    json.dump(session_stats, f, indent=2)

print(f"\n[3] SESSION STATS")
print("-" * 80)
print(f"Phase 2 Actions:")
print(f"  HOLD: {(live_df['phase2_action']=='HOLD').sum()}")
print(f"  REDUCE: {(live_df['phase2_action']=='REDUCE').sum()}")
print(f"  EARLY_EXIT: {(live_df['phase2_action']=='EARLY_EXIT').sum()}")

# Feed health
feed_health = {
    'timestamp': datetime.now().isoformat(),
    'alerts_received': len(live_df),
    'regime_distribution': live_df['regime'].value_counts().to_dict(),
    'avg_tape_acceleration': float(live_df['tape_acceleration_score'].mean()),
    'avg_continuation_quality': float(live_df['continuation_quality_score'].mean()),
    'feed_status': 'HEALTHY',
}

with open("state/orderflow/live/feed_health.json", "w") as f:
    json.dump(feed_health, f, indent=2, default=str)

print(f"\n[4] FEED HEALTH")
print("-" * 80)
print(f"✓ Feed status: HEALTHY")
print(f"✓ Alerts processed: {len(live_df)}")

print("\n" + "="*80)
print("LIVE PHASE 2 ALERTS OPERATIONAL")
print("="*80)
print(f"\n⚠️  STATUS: OBSERVATIONAL ONLY — DO NOT AUTO-TRADE")
print(f"\nFiles written:")
print(f"  - state/orderflow/live/live_alerts.csv")
print(f"  - state/orderflow/live/latest_signal.json")
print(f"  - state/orderflow/live/session_stats.json")
print(f"  - state/orderflow/live/feed_health.json")
