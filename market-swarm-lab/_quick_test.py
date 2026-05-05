#!/usr/bin/env python3
"""Quick test script to verify pipeline wiring with lowered thresholds."""
import sys
import os
sys.path.insert(0, '/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')

# Monkey-patch to force alerts
from scripts.run_live_orderflow_alerts import *

engine = LiveOrderflowAlertEngine(
    watch_pattern="state/orderflow/bookmap_api/*.jsonl",
    spy_source="cached",
    notify_mode="none",
    confidence_threshold=30,  # Very low for test
    cooldown_minutes=0,       # No cooldown for test
    dry_run=True,
)

# Lower sweep thresholds to catch micro-movement
engine.sweep_detector = LiveSweepDetector(
    min_sweep_ticks=0.1,   # 0.025 points
    max_sweep_ticks=50.0,  # 12.5 points
)

report = engine.run_replay_test(
    replay_file="state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl",
    confidence_threshold=30,
    cooldown_minutes=0,
)

print("\n" + "="*60)
for k, v in report.items():
    if k == "field_error_details" and not v:
        continue
    print(f"  {k}: {v}")
print("="*60)
