#!/usr/bin/env python3
"""
v3_human_validation_session.py

Run 15-minute V3 human-executable validation session.

Expected: Fewer, cleaner, larger-target alerts suitable for manual trading.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from live_jsonl_tailer import LiveJSONLTailer
from v3_alert_engine_human_executable import V3HumanExecutableEngine


def run_v3_validation(duration_sec: int = 900):
    """Run 15-minute V3 validation."""
    
    print("\n" + "="*80)
    print("V3 HUMAN-EXECUTABLE VALIDATION SESSION")
    print("="*80)
    print(f"Duration: {duration_sec}s (15 minutes)")
    print(f"Persistence: 5s minimum (vs 750ms in V2)")
    print(f"Anti-flip: 30s suppression (vs 5s in V2)")
    print(f"Targets: 20-60 ticks (vs 8-16 in V2)")
    print(f"Max alerts: 5 (vs 10 in V1/V2)")
    print(f"Entry style: Zones, not exact ticks")
    print("="*80 + "\n")
    
    canonical_path = (
        Path.home() / ".openclaw" / "workspace" / 
        "market-swarm-lab" / "state" / "orderflow" / "bookmap_api"
    )
    
    tailer = LiveJSONLTailer(data_dir=str(canonical_path), staleness_threshold_sec=30.0)
    engine = V3HumanExecutableEngine()
    
    event_count = 0
    start_time = time.time()
    
    print(f"Starting at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    try:
        while (time.time() - start_time) < duration_sec:
            # File selection
            now = time.time()
            if now - tailer.last_file_check > 5.0 or not tailer.current_file:
                tailer.last_file_check = now
                if tailer._should_rollover():
                    newest = tailer._select_newest_active_file()
                    if newest:
                        tailer._open_file(newest)
            
            if tailer.file_handle:
                line = tailer.file_handle.readline()
                if not line:
                    time.sleep(0.01)
                    continue
                
                tailer.current_offset += len(line)
                event_count += 1
                
                try:
                    import json
                    obj = json.loads(line.decode('utf-8', errors='ignore'))
                except:
                    continue
                
                # Process event
                alert = engine.process_event(obj)
                
                if alert:
                    elapsed = time.time() - start_time
                    score_str = "⭐" if alert.human_score >= 70 else "✓"
                    print(f"[{elapsed:.1f}s] {score_str} ALERT #{len(engine.alerts)}")
                    print(f"   {alert.direction} Zone: {alert.entry_zone_low:.2f}-{alert.entry_zone_high:.2f}")
                    print(f"   T1: {alert.target1:.2f} | T2: {alert.target2:.2f} | Hold: {alert.expected_hold_minutes:.0f}min")
                    print(f"   Imbalance: {alert.imbalance_ratio:.2f}x | Human Score: {alert.human_score:.0f}/100")
                    print()
                
                if event_count % 1000 == 0:
                    elapsed = time.time() - start_time
                    rate = event_count / elapsed
                    print(f"[{elapsed:.1f}s] {event_count} events ({rate:.0f}/s), {len(engine.alerts)} alerts\n", file=sys.stderr)
                
                if len(engine.alerts) >= engine.max_alerts:
                    print("\n✅ Max alerts reached (5)")
                    break
            else:
                time.sleep(0.1)
    
    except KeyboardInterrupt:
        print("\n⏹️  Session interrupted")
    finally:
        if tailer.file_handle:
            tailer.file_handle.close()
    
    elapsed = time.time() - start_time
    print(f"\n✅ Session complete")
    print(f"   Duration: {elapsed:.1f}s")
    print(f"   Events: {event_count}")
    print(f"   Alerts: {len(engine.alerts)}/5\n")
    
    # Print and export
    engine.print_alerts()
    if engine.alerts:
        csv_path = engine.export_alerts_csv()
        return engine.alerts, csv_path
    
    return engine.alerts, None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="V3 human-executable validation")
    parser.add_argument("--duration", type=int, default=900, help="Duration (seconds)")
    
    args = parser.parse_args()
    
    alerts, csv_path = run_v3_validation(duration_sec=args.duration)
