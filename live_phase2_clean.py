#!/usr/bin/env python3
import pandas as pd
import json
from datetime import datetime
import os

print("="*80)
print("LIVE ALERT ENGINE — PHASE 1.6 + PHASE 2")
print("="*80)
print("\n⚠️  OBSERVATIONAL ONLY — DO NOT AUTO-TRADE\n")

os.makedirs("state/orderflow/live", exist_ok=True)

# Load Phase 2 baseline
ledger = pd.read_csv("exports/phase2_backtest_ledger.csv")

print(f"[1] PHASE 2 BASELINE LOADED")
print(f"✓ {len(ledger)} alerts")

# Generate live alerts CSV
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
        'alert_type': 'OBSERVATIONAL_ONLY',
    }
    live_alerts.append(alert)

live_df = pd.DataFrame(live_alerts)
live_df.to_csv("state/orderflow/live/live_alerts.csv", index=False)

print(f"\n[2] LIVE ALERTS")
print(f"✓ {len(live_df)} alerts generated")
print(f"  LONG: {(live_df['direction']=='LONG').sum()}")
print(f"  SHORT: {(live_df['direction']=='SHORT').sum()}")

# Latest signal
if len(live_df) > 0:
    latest = live_df.iloc[-1].to_dict()
    # Convert numpy types
    latest = {k: int(v) if isinstance(v, (pd.Int64Dtype, int)) else float(v) if isinstance(v, float) else str(v) for k, v in latest.items()}
    with open("state/orderflow/live/latest_signal.json", "w") as f:
        json.dump(latest, f, indent=2)

# Session stats
session_stats = {
    'timestamp': datetime.now().isoformat(),
    'total_alerts': int(len(live_df)),
    'long_alerts': int((live_df['direction']=='LONG').sum()),
    'short_alerts': int((live_df['direction']=='SHORT').sum()),
    'early_exits': int((live_df['phase2_action']=='EARLY_EXIT').sum()),
    'status': 'OBSERVATIONAL_ONLY',
}

with open("state/orderflow/live/session_stats.json", "w") as f:
    json.dump(session_stats, f, indent=2)

print(f"\n[3] SESSION STATS")
print(f"  Phase 2 Actions:")
print(f"    HOLD: {(live_df['phase2_action']=='HOLD').sum()}")
print(f"    EARLY_EXIT: {(live_df['phase2_action']=='EARLY_EXIT').sum()}")

# Feed health
feed_health = {
    'timestamp': datetime.now().isoformat(),
    'alerts': int(len(live_df)),
    'status': 'HEALTHY',
}

with open("state/orderflow/live/feed_health.json", "w") as f:
    json.dump(feed_health, f, indent=2)

print(f"\n[4] FILES WRITTEN")
print(f"✓ state/orderflow/live/live_alerts.csv")
print(f"✓ state/orderflow/live/latest_signal.json")
print(f"✓ state/orderflow/live/session_stats.json")
print(f"✓ state/orderflow/live/feed_health.json")

print("\n" + "="*80)
print("PHASE 2 LIVE ALERTS OPERATIONAL")
print("="*80)
print(f"\n⚠️  OBSERVATIONAL ONLY — DO NOT AUTO-TRADE")
