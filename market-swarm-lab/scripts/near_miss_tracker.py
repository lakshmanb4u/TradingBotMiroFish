#!/usr/bin/env python3
"""
Near-Miss Signal Tracker - Monitors rejected signals that pass initial gates.
Integrates with live_alert_engine.py via shared state files.

Near-miss definition:
  - Passes absorption gate (regime classification)
  - Passes reclaim gate (follow-through detected)
  - Passes regime gate (matching regime thresholds)
  - FAILS follow-through gate OR confidence threshold gate

Outputs:
  - state/orderflow/live/near_miss_signals.csv: All rejected signals
  - state/orderflow/live/near_miss_summary.json: 15-min summaries
  - WhatsApp alerts every 15 minutes
"""

import json
import time
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, Counter
import threading

# Paths
LIVE_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/")
NEAR_MISS_CSV = LIVE_DIR / "near_miss_signals.csv"
NEAR_MISS_SUMMARY = LIVE_DIR / "near_miss_summary.json"
NEAR_MISS_STATE = LIVE_DIR / ".near_miss_state.json"
ENGINE_HEARTBEAT = LIVE_DIR / "heartbeat.json"

# Initialize CSV if not exists
def init_csv():
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


def log_near_miss(near_miss_data):
    """Log a near-miss signal to CSV."""
    init_csv()
    
    row = [
        near_miss_data.get("timestamp_et"),
        near_miss_data.get("symbol"),
        near_miss_data.get("side"),
        near_miss_data.get("confidence"),
        near_miss_data.get("failed_gate_reason"),
        near_miss_data.get("displacement_ticks"),
        near_miss_data.get("delta_acceleration"),
        near_miss_data.get("regime"),
        near_miss_data.get("entry_candidate"),
        near_miss_data.get("why_rejected"),
    ]
    
    with open(NEAR_MISS_CSV, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)


def evaluate_signal_for_near_miss(signal, gate_context):
    """
    Determine if a signal is a near-miss (rejected despite passing initial gates).
    
    Gates:
    1. Absorption gate: regime must be classified (not "insufficient_data")
    2. Reclaim gate: follow_through_count >= 1
    3. Regime gate: displacement/delta_accel match regime thresholds
    4. Follow-through gate: follow_through_count >= FOLLOW_THROUGH_MIN
    5. Confidence gate: confidence >= CONFIDENCE_MIN
    
    Near-miss: Passes gates 1-3, fails gate 4 OR 5
    """
    
    rejection_reason = None
    
    # Gate 1: Absorption (regime classification)
    regime = signal.get("regime")
    if regime == "insufficient_data":
        return None, "failed_absorption_gate"
    
    # Gate 2: Reclaim (follow-through started)
    follow_through_count = gate_context.get("follow_through_count", 0)
    if follow_through_count < 1:
        return None, "failed_reclaim_gate"
    
    # Gate 3: Regime gate
    displacement = signal.get("displacement", 0)
    delta_accel = signal.get("delta_accel", 0)
    
    regime_thresholds = {
        "trending": {"min_delta": 0.8, "min_displacement": 0.6},
        "mean_revert": {"max_delta": 0.3, "max_displacement": 0.3},
        "compression": {"min_vol": 5, "max_price_range": 1.0},
    }
    
    if regime in regime_thresholds:
        thresholds = regime_thresholds[regime]
        if regime == "trending":
            if delta_accel < thresholds["min_delta"] or displacement < thresholds["min_displacement"]:
                return None, "failed_regime_gate"
        elif regime == "mean_revert":
            if delta_accel > thresholds["max_delta"] or displacement > thresholds["max_displacement"]:
                return None, "failed_regime_gate"
    
    # Gate 4: Follow-through gate (requires FOLLOW_THROUGH_MIN bars)
    FOLLOW_THROUGH_MIN = 2
    if follow_through_count < FOLLOW_THROUGH_MIN:
        rejection_reason = "insufficient_follow_through"
    
    # Gate 5: Confidence gate
    CONFIDENCE_MIN = 65
    confidence = signal.get("confidence", 0)
    if confidence < CONFIDENCE_MIN:
        rejection_reason = rejection_reason or "below_confidence_threshold"
    
    # If either gate 4 or 5 failed, it's a near-miss
    if rejection_reason:
        return {
            "timestamp_et": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.get("symbol"),
            "side": "LONG" if signal.get("direction") == "LONG" else "SHORT",
            "confidence": signal.get("confidence"),
            "failed_gate_reason": rejection_reason,
            "displacement_ticks": round(signal.get("displacement", 0) * 10000),  # Convert to ticks
            "delta_acceleration": signal.get("delta_accel", 0),
            "regime": regime,
            "entry_candidate": round(signal.get("entry", 0), 4),
            "why_rejected": f"{rejection_reason}: follow_through={follow_through_count}/{FOLLOW_THROUGH_MIN}, confidence={confidence}/{CONFIDENCE_MIN}",
        }, rejection_reason
    
    return None, None


def read_near_miss_state():
    """Read persistent near-miss state."""
    if NEAR_MISS_STATE.exists():
        try:
            with open(NEAR_MISS_STATE, 'r') as f:
                return json.load(f)
        except:
            pass
    
    return {
        "last_summary": datetime.now(timezone.utc).isoformat(),
        "near_miss_count": 0,
        "rejection_reasons": {},
    }


def write_near_miss_state(state):
    """Write persistent near-miss state."""
    with open(NEAR_MISS_STATE, 'w') as f:
        json.dump(state, f, indent=2)


def generate_15min_summary():
    """Generate 15-minute summary of near-miss signals."""
    init_csv()
    
    # Read all near-miss records from last 15 minutes
    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    near_misses = []
    rejection_reasons = Counter()
    
    try:
        with open(NEAR_MISS_CSV, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = datetime.fromisoformat(row["timestamp_et"])
                    if ts > cutoff_time:
                        near_misses.append(row)
                        rejection_reasons[row["failed_gate_reason"]] += 1
                except:
                    pass
    except FileNotFoundError:
        pass
    
    # Assess gate strictness
    if len(near_misses) == 0:
        gate_assessment = "no_near_misses"
    elif len(rejection_reasons) >= 2 and rejection_reasons.most_common(1)[0][1] < len(near_misses) * 0.5:
        gate_assessment = "appropriate"
    elif rejection_reasons.get("below_confidence_threshold", 0) > len(near_misses) * 0.7:
        gate_assessment = "strict"
    else:
        gate_assessment = "loose"
    
    summary = {
        "timestamp_et": datetime.now(timezone.utc).isoformat(),
        "period_minutes": 15,
        "total_near_misses": len(near_misses),
        "rejection_reasons": dict(rejection_reasons.most_common()),
        "gate_strictness_assessment": gate_assessment,
        "symbols_affected": list(set(row["symbol"] for row in near_misses)),
        "detail": {
            "avg_confidence": sum(float(row["confidence"]) for row in near_misses) / len(near_misses) if near_misses else 0,
            "regimes_seen": list(set(row["regime"] for row in near_misses)),
        },
    }
    
    with open(NEAR_MISS_SUMMARY, 'w') as f:
        json.dump(summary, f, indent=2)
    
    return summary


def format_whatsapp_summary(summary):
    """Format summary for WhatsApp."""
    total = summary["total_near_misses"]
    top_rejection = list(summary["rejection_reasons"].items())[0] if summary["rejection_reasons"] else ("none", 0)
    assessment = summary["gate_strictness_assessment"]
    
    return (
        f"Near-miss analysis: {total} total near-misses, "
        f"top rejection: {top_rejection[0]} ({top_rejection[1]}), "
        f"gates assessment: {assessment}"
    )


def main():
    """Main tracker loop - runs in background thread."""
    init_csv()
    state = read_near_miss_state()
    
    print(f"[NEAR-MISS] Tracker initialized: {NEAR_MISS_CSV}")
    print(f"[NEAR-MISS] Summary output: {NEAR_MISS_SUMMARY}")
    
    # Every 15 minutes, generate summary and send WhatsApp
    summary_interval = 900  # 15 minutes
    last_summary = time.time()
    
    while True:
        try:
            now = time.time()
            
            if now - last_summary >= summary_interval:
                summary = generate_15min_summary()
                whatsapp_msg = format_whatsapp_summary(summary)
                
                print(f"[NEAR-MISS] 15-min summary: {summary['total_near_misses']} near-misses")
                print(f"[NEAR-MISS] WhatsApp: {whatsapp_msg}")
                
                # TODO: Integrate WhatsApp send here
                # For now, just log to summary file
                
                last_summary = now
            
            time.sleep(10)  # Check every 10 seconds if it's time for summary
        
        except Exception as e:
            print(f"[NEAR-MISS] Error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    print("[NEAR-MISS] Starting tracker thread...")
    
    # Run in background
    tracker_thread = threading.Thread(target=main, daemon=True)
    tracker_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[NEAR-MISS] Tracker stopped")
