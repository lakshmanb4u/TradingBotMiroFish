#!/usr/bin/env python3
"""Debug the sweep detector directly."""
import sys, json
sys.path.insert(0, '/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')

from scripts.run_live_orderflow_alerts import LiveSweepDetector

det = LiveSweepDetector(min_sweep_ticks=0.5, max_sweep_ticks=50.0)

# Feed 50 trades at 5000
for i in range(50):
    det.process_events([{
        "seq": i+1, "ts_event": f"2026-05-04T14:30:{i:02d}Z",
        "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
        "price": 5000.0, "size": 1, "side": "buy"
    }])

print(f"After 50 baseline trades: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")

# Sweep to 4998
sweeps = det.process_events([{
    "seq": 51, "ts_event": "2026-05-04T14:31:01Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 4998.0, "size": 5, "side": "sell"
}])
print(f"After sweep to 4998: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")
if det._pending:
    for k,v in det._pending.items():
        print(f"  Pending: {k} -> {v}")

# Reclaim to 5000.5
sweeps = det.process_events([{
    "seq": 52, "ts_event": "2026-05-04T14:31:02Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5000.5, "size": 3, "side": "buy"
}])
print(f"After reclaim to 5000.5: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")
for s in sweeps:
    print(f"  FIRED SWEEP: {s.direction} at {s.level}, trigger={s.trigger_price}")

# Now test bearish
for i in range(20):
    det.process_events([{
        "seq": 53+i, "ts_event": f"2026-05-04T14:32:{i:02d}Z",
        "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
        "price": 5005.0, "size": 1, "side": "buy"
    }])

print(f"After 20 at 5005: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")

# Pop to 5007.5
sweeps = det.process_events([{
    "seq": 74, "ts_event": "2026-05-04T14:33:00Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5007.5, "size": 5, "side": "buy"
}])
print(f"After pop to 5007.5: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")
if det._pending:
    for k,v in det._pending.items():
        print(f"  Pending: {k} -> type={v['type']}, level={v['level']}")

# Reject to 5004.5
sweeps = det.process_events([{
    "seq": 75, "ts_event": "2026-05-04T14:33:01Z",
    "symbol": "ESU1.CME@RITHMIC", "event_type": "trade",
    "price": 5004.5, "size": 3, "side": "sell"
}])
print(f"After reject to 5004.5: prices={len(det.recent_prices)}, sweeps={len(det.sweeps)}, pending={len(det._pending)}")
for s in sweeps:
    print(f"  FIRED SWEEP: {s.direction} at {s.level}, trigger={s.trigger_price}")

print(f"\nTotal sweeps detected: {len(det.sweeps)}")
