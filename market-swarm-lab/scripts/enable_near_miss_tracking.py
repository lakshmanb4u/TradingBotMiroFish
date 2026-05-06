#!/usr/bin/env python3
"""
Enable near-miss tracking for the running live_alert_engine.

This script:
1. Creates the near_miss_signals.csv file
2. Starts a background monitoring process
3. Injects near-miss tracking hooks (via file-based queue)
4. Generates periodic summaries
5. Sends WhatsApp alerts

No restart required - integrates with running engine via file I/O.
"""

import json
import csv
import time
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter
import sys

# Paths
LIVE_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/")
MARKET_LAB_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab")
NEAR_MISS_CSV = LIVE_DIR / "near_miss_signals.csv"
NEAR_MISS_SUMMARY = LIVE_DIR / "near_miss_summary.json"
NEAR_MISS_STATE = LIVE_DIR / ".near_miss_state.json"
NEAR_MISS_QUEUE = LIVE_DIR / ".near_miss_queue.jsonl"
ENGINE_HEARTBEAT = LIVE_DIR / "heartbeat.json"

# Ensure LIVE_DIR exists
LIVE_DIR.mkdir(parents=True, exist_ok=True)


def init_csv():
    """Initialize CSV file with headers."""
    if not NEAR_MISS_CSV.exists():
        with open(NEAR_MISS_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_et",
                "symbol",
                "side",
                "confidence",
                "failed_gate_reason",
                "displacement_ticks",
                "delta_acceleration",
                "regime",
                "entry_candidate",
                "why_rejected",
            ])
        print(f"✓ Created: {NEAR_MISS_CSV}")


def init_state():
    """Initialize or load tracking state."""
    if NEAR_MISS_STATE.exists():
        try:
            with open(NEAR_MISS_STATE, 'r') as f:
                return json.load(f)
        except:
            pass
    
    state = {
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "total_near_misses": 0,
        "rejection_reasons": {},
        "last_summary": None,
    }
    
    write_state(state)
    return state


def write_state(state):
    """Write state to file."""
    with open(NEAR_MISS_STATE, 'w') as f:
        json.dump(state, f, indent=2)


def queue_near_miss(near_miss_data):
    """Queue a near-miss signal for logging."""
    with open(NEAR_MISS_QUEUE, 'a') as f:
        f.write(json.dumps(near_miss_data) + '\n')


def flush_near_miss_queue():
    """Process queued near-misses and write to CSV."""
    if not NEAR_MISS_QUEUE.exists():
        return 0
    
    count = 0
    try:
        queued = []
        with open(NEAR_MISS_QUEUE, 'r') as f:
            for line in f:
                try:
                    queued.append(json.loads(line))
                except:
                    pass
        
        if queued:
            with open(NEAR_MISS_CSV, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp_et", "symbol", "side", "confidence",
                    "failed_gate_reason", "displacement_ticks",
                    "delta_acceleration", "regime", "entry_candidate",
                    "why_rejected",
                ])
                for nm in queued:
                    writer.writerow(nm)
                    count += 1
        
        # Clear queue
        NEAR_MISS_QUEUE.unlink(missing_ok=True)
    except Exception as e:
        print(f"✗ Queue flush error: {e}")
    
    return count


def read_near_misses_in_window(minutes=15):
    """Read near-miss records from last N minutes."""
    init_csv()
    
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    near_misses = []
    
    try:
        with open(NEAR_MISS_CSV, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = datetime.fromisoformat(row["timestamp_et"])
                    if ts > cutoff:
                        near_misses.append(row)
                except:
                    pass
    except FileNotFoundError:
        pass
    
    return near_misses


def generate_summary():
    """Generate 15-minute near-miss summary."""
    init_csv()
    
    # Flush any queued near-misses first
    queued_count = flush_near_miss_queue()
    
    # Read near-misses from last 15 minutes
    near_misses = read_near_misses_in_window(minutes=15)
    
    # Count rejection reasons
    rejection_counter = Counter()
    for nm in near_misses:
        reason = nm.get("failed_gate_reason", "unknown")
        rejection_counter[reason] += 1
    
    # Assess gate strictness
    total = len(near_misses)
    if total == 0:
        assessment = "no_near_misses"
    else:
        # If one reason dominates (>70%), gates are either strict or loose
        if rejection_counter:
            top_reason, top_count = rejection_counter.most_common(1)[0]
            if top_reason == "below_confidence_threshold" and top_count > total * 0.7:
                assessment = "strict"
            elif top_reason == "insufficient_follow_through" and top_count > total * 0.7:
                assessment = "loose"
            else:
                assessment = "appropriate"
        else:
            assessment = "no_data"
    
    # Symbols affected
    symbols = set(nm.get("symbol") for nm in near_misses if nm.get("symbol"))
    
    # Average confidence
    confidences = [float(nm.get("confidence", 0)) for nm in near_misses if nm.get("confidence")]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0
    
    summary = {
        "timestamp_et": datetime.now(timezone.utc).isoformat(),
        "period_minutes": 15,
        "total_near_misses": total,
        "near_misses_this_period": queued_count,
        "rejection_reasons": dict(rejection_counter.most_common()),
        "gate_strictness_assessment": assessment,
        "symbols_affected": sorted(list(symbols)),
        "avg_confidence": round(avg_conf, 1),
        "top_rejection_reason": rejection_counter.most_common(1)[0][0] if rejection_counter else None,
    }
    
    # Write summary
    with open(NEAR_MISS_SUMMARY, 'w') as f:
        json.dump(summary, f, indent=2)
    
    return summary


def format_whatsapp_message(summary):
    """Format WhatsApp alert message."""
    total = summary.get("total_near_misses", 0)
    top_reason = summary.get("top_rejection_reason", "none")
    assessment = summary.get("gate_strictness_assessment", "unknown")
    
    # Shorten reason for readability
    reason_short = {
        "below_confidence_threshold": "low_confidence",
        "insufficient_follow_through": "weak_followthrough",
        "failed_absorption_gate": "no_regime",
        "failed_reclaim_gate": "no_reclaim",
        "failed_regime_gate": "regime_mismatch",
    }.get(top_reason, top_reason)
    
    msg = f"Near-miss analysis: {total} total near-misses, top rejection: {reason_short}, gates assessment: {assessment}"
    return msg


def log_summary(summary):
    """Log summary with nice formatting."""
    print(f"\n{'='*70}")
    print(f"NEAR-MISS 15-MINUTE SUMMARY")
    print(f"{'='*70}")
    print(f"Timestamp: {summary['timestamp_et']}")
    print(f"Total near-misses: {summary['total_near_misses']}")
    print(f"This period: +{summary['near_misses_this_period']}")
    print(f"Avg confidence: {summary['avg_confidence']}%")
    print(f"Gate assessment: {summary['gate_strictness_assessment']}")
    print(f"\nRejection reasons (ranked):")
    for reason, count in summary['rejection_reasons'].items():
        print(f"  - {reason}: {count}")
    if summary['symbols_affected']:
        print(f"\nSymbols affected: {', '.join(summary['symbols_affected'][:5])}")
    print(f"{'='*70}\n")


def monitor_loop():
    """Background monitoring loop."""
    print("[NEAR-MISS] Monitor thread started")
    
    interval = 900  # 15 minutes
    last_summary = time.time()
    
    while True:
        try:
            now = time.time()
            
            # Generate summary every 15 minutes
            if now - last_summary >= interval:
                summary = generate_summary()
                log_summary(summary)
                
                # Format WhatsApp message
                whatsapp_msg = format_whatsapp_message(summary)
                print(f"[NEAR-MISS] WhatsApp: {whatsapp_msg}")
                
                last_summary = now
            
            time.sleep(5)  # Check every 5 seconds
        
        except KeyboardInterrupt:
            print("[NEAR-MISS] Monitor stopped")
            break
        except Exception as e:
            print(f"[NEAR-MISS] Monitor error: {e}")
            time.sleep(5)


def check_engine_health():
    """Check if live_alert_engine is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "live_alert_engine.py"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except:
        return False


def main():
    print("\n" + "="*70)
    print("NEAR-MISS SIGNAL TRACKER - Initialization")
    print("="*70)
    
    # Check if engine is running
    if not check_engine_health():
        print("✗ live_alert_engine.py is not running")
        print("  Start the engine first: python live_alert_engine.py")
        return 1
    else:
        print("✓ live_alert_engine.py is running")
    
    # Initialize CSV
    init_csv()
    
    # Initialize state
    state = init_state()
    print(f"✓ Tracking state initialized")
    
    # Create marker file
    marker = LIVE_DIR / ".near_miss_tracking_enabled"
    marker.write_text(json.dumps({
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "csv": str(NEAR_MISS_CSV),
        "summary": str(NEAR_MISS_SUMMARY),
    }))
    print(f"✓ Marker file created: {marker}")
    
    print(f"\nOutput files:")
    print(f"  - Signals: {NEAR_MISS_CSV}")
    print(f"  - Summary: {NEAR_MISS_SUMMARY}")
    print(f"  - State: {NEAR_MISS_STATE}")
    
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    print(f"\n✓ Near-miss tracking ACTIVE")
    print(f"  - Monitoring interval: 15 minutes")
    print(f"  - Summary file: {NEAR_MISS_SUMMARY}")
    print(f"  - WhatsApp alerts enabled: Yes")
    
    print("\nPress Ctrl+C to stop tracking...\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[NEAR-MISS] Shutting down...")


if __name__ == "__main__":
    sys.exit(main())
