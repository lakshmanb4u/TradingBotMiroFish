#!/usr/bin/env python3
"""
v3_position_managed_validation.py

Run 15-minute V3 validation with position state machine enforced.

Expected: No overlapping positions, no contradictions, realistic trade lifecycle.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from live_jsonl_tailer import LiveJSONLTailer
from v3_alert_engine_position_managed import V3PositionManagedEngine


def run_position_managed_validation(duration_sec: int = 900):
    """Run 15-minute V3 position-managed validation."""
    
    print("\n" + "="*80)
    print("V3 POSITION-MANAGED VALIDATION SESSION")
    print("="*80)
    print(f"Duration: {duration_sec}s (15 minutes)")
    print(f"Position state machine: ENABLED")
    print(f"Contradict alerts: BLOCKED")
    print(f"Min opposite interval: 60 seconds")
    print(f"Reversal criteria: 6.0x+ imbalance, 5s+ persistence")
    print("="*80 + "\n")
    
    canonical_path = (
        Path.home() / ".openclaw" / "workspace" / 
        "market-swarm-lab" / "state" / "orderflow" / "bookmap_api"
    )
    
    tailer = LiveJSONLTailer(data_dir=str(canonical_path), staleness_threshold_sec=30.0)
    engine = V3PositionManagedEngine()
    
    event_count = 0
    start_time = time.time()
    last_report = start_time
    
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
                completed_trade = engine.process_event(obj)
                
                if completed_trade:
                    elapsed = time.time() - start_time
                    print(f"[{elapsed:.1f}s] ✓ TRADE CLOSED #{completed_trade.trade_num}")
                    print(f"   {completed_trade.direction} {completed_trade.result_ticks:+.1f}t (${completed_trade.result_pnl:+.0f})")
                    print(f"   Exit: {completed_trade.exit_reason} | Hold: {completed_trade.hold_duration_sec:.0f}s")
                    print()
                
                # Periodic report
                if now - last_report >= 60:
                    elapsed = time.time() - start_time
                    rate = event_count / elapsed
                    status = engine.position_state.value
                    print(f"[{elapsed:.1f}s] {event_count} events ({rate:.0f}/s) | State: {status} | Trades: {len(engine.completed_trades)} closed, Alerts: {engine.alerts_generated}\n", file=sys.stderr)
                    last_report = now
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
    print(f"   Alerts generated: {engine.alerts_generated}")
    print(f"   Trades completed: {len(engine.completed_trades)}\n")
    
    # Print summary
    engine.print_summary()
    
    # Export
    out_dir = Path.home() / ".openclaw" / "workspace" / "state" / "orderflow" / "live"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine.export_trades_csv(str(out_dir / "v3_position_managed_trades.csv"))
    
    return engine.completed_trades


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="V3 position-managed validation")
    parser.add_argument("--duration", type=int, default=900, help="Duration (seconds)")
    
    args = parser.parse_args()
    
    trades = run_position_managed_validation(duration_sec=args.duration)
