#!/usr/bin/env python3
"""Verify pipeline with synthetic sweep data."""
import sys, json, tempfile, os
from pathlib import Path

sys.path.insert(0, '/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')

from scripts.run_live_orderflow_alerts import *

# Create synthetic JSONL with clear ES sweeps
test_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
seq = 0

# 50 trades establishing support at 5000
for _ in range(50):
    seq += 1
    test_file.write(json.dumps({
        "seq": seq, "ts_event": f"2026-05-04T14:30:{seq:02d}Z",
        "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
        "price": 5000.0, "size": 1, "side": "buy"
    }) + "\n")

# Sweep below support to 4998.0 (2 points below)
seq += 1
test_file.write(json.dumps({
    "seq": seq, "ts_event": f"2026-05-04T14:31:01Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 4998.0, "size": 5, "side": "sell"
}) + "\n")

# Reclaim back above 5000
seq += 1
test_file.write(json.dumps({
    "seq": seq, "ts_event": f"2026-05-04T14:31:02Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5000.5, "size": 3, "side": "buy"
}) + "\n")

# More establishing trades
for i in range(20):
    seq += 1
    test_file.write(json.dumps({
        "seq": seq, "ts_event": f"2026-05-04T14:32:{i:02d}Z",
        "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
        "price": 5005.0, "size": 1, "side": "buy"
    }) + "\n")

# Bearish sweep above resistance to 5007.5
seq += 1
test_file.write(json.dumps({
    "seq": seq, "ts_event": "2026-05-04T14:33:00Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5007.5, "size": 5, "side": "buy"
}) + "\n")

# Reject back below
seq += 1
test_file.write(json.dumps({
    "seq": seq, "ts_event": "2026-05-04T14:33:01Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5004.5, "size": 3, "side": "sell"
}) + "\n")

test_file.close()

engine = LiveOrderflowAlertEngine(
    watch_pattern="state/orderflow/bookmap_api/*.jsonl",
    spy_source="cached",
    notify_mode="none",
    confidence_threshold=5,  # Very low
    cooldown_minutes=0,
    dry_run=True,
)

# Reset detector with small thresholds
engine.sweep_detector = LiveSweepDetector(min_sweep_ticks=0.5, max_sweep_ticks=50.0)

report = engine.run_replay_test(
    replay_file=test_file.name,
    confidence_threshold=5,
    cooldown_minutes=0,
)

print("\nSYNTHETIC TEST REPORT")
print("="*60)
for k, v in report.items():
    if k == "field_error_details" and not v:
        continue
    print(f"  {k}: {v}")
print("="*60)

os.unlink(test_file.name)
