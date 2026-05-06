#!/usr/bin/env python3
"""
Integration module for near-miss tracking into live_alert_engine.py

This module patches the running engine to:
1. Intercept signals before confidence gate
2. Log rejected signals to near_miss_signals.csv
3. Send periodic summaries

Installation: Import this in live_alert_engine.py and call enable_near_miss_tracking()
"""

import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# Shared paths
LIVE_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/")
NEAR_MISS_CSV = LIVE_DIR / "near_miss_signals.csv"
NEAR_MISS_TRACKING_ENABLED = LIVE_DIR / ".near_miss_tracking_enabled"

# Global near-miss tracker state
near_miss_state = {
    "enabled": False,
    "tracked_signals": 0,
    "rejected_signals": 0,
    "rejection_reasons": defaultdict(int),
}


def init_near_miss_csv():
    """Initialize the near-miss signals CSV file."""
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


def log_rejected_signal(signal, gate_context, rejection_reason):
    """
    Log a signal that was rejected despite passing initial gates.
    
    Args:
        signal: The detected signal dict
        gate_context: Context about which gates it passed/failed
        rejection_reason: Why it was rejected (confidence, follow_through, etc.)
    """
    if not near_miss_state.get("enabled"):
        return
    
    init_near_miss_csv()
    
    # Determine side from direction
    side = "BUY" if signal.get("direction") == "LONG" else "SELL"
    
    # Convert displacement from ratio to ticks (approx)
    displacement_ticks = round(signal.get("displacement", 0) * 10000)
    
    row = [
        signal.get("timestamp_et", datetime.now(timezone.utc).isoformat()),
        signal.get("symbol"),
        side,
        signal.get("confidence", 0),
        rejection_reason,
        displacement_ticks,
        signal.get("delta_accel", 0),
        signal.get("regime"),
        round(signal.get("entry", 0), 4),
        gate_context.get("rejection_detail", f"Rejected: {rejection_reason}"),
    ]
    
    try:
        with open(NEAR_MISS_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(row)
        
        near_miss_state["rejected_signals"] += 1
        near_miss_state["rejection_reasons"][rejection_reason] += 1
    except Exception as e:
        print(f"[NEAR-MISS] Error logging: {e}")


def create_patched_detect_sweep_signal(original_detect_sweep_signal, confidence_min, follow_through_min):
    """
    Create a patched version of detect_sweep_signal that logs rejections.
    
    This wraps the original function to intercept signals that would be rejected.
    """
    
    def patched_detect_sweep_signal(symbol, symbol_data, event):
        """Patched version with near-miss tracking."""
        from datetime import datetime, timezone
        
        side = event.get("side")
        size = event.get("size", 0)
        price = event.get("price", 0)
        
        # Original logic (from live_alert_engine.py)
        regime = original_detect_sweep_signal.__globals__.get("calculate_regime")(symbol_data, symbol)
        
        # Track follow-through
        if size > 500 and side in ["bid", "ask"]:
            symbol_data["follow_through_count"] = symbol_data.get("follow_through_count", 0) + 1
        
        ft_count = symbol_data.get("follow_through_count", 0)
        
        if ft_count >= follow_through_min:
            # Generate signal confidence (same as original)
            confidence = min(100, 65 + (size / 1000 * 10) + (symbol_data["displacement"] * 20))
            
            direction = "LONG" if side == "bid" else "SHORT"
            entry = price
            stop = price * 0.99 if side == "bid" else price * 1.01
            target1 = price * 1.01 if side == "bid" else price * 0.99
            target2 = price * 1.02 if side == "bid" else price * 0.98
            
            signal = {
                "timestamp_et": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "direction": direction,
                "entry": round(entry, 4),
                "stop": round(stop, 4),
                "target1": round(target1, 4),
                "target2": round(target2, 4),
                "confidence": round(confidence, 1),
                "reason_codes": ["sweep_detected", "follow_through", regime],
                "regime": regime,
                "displacement": round(symbol_data["displacement"], 4),
                "delta_accel": round(symbol_data["delta_accel"], 6),
                "size": size,
            }
            
            # NEW: Check if this would be rejected
            if confidence < confidence_min:
                log_rejected_signal(
                    signal,
                    {
                        "follow_through_count": ft_count,
                        "follow_through_min": follow_through_min,
                        "confidence": confidence,
                        "confidence_min": confidence_min,
                        "rejection_detail": f"Confidence {confidence} < {confidence_min} (follow_through: {ft_count}/{follow_through_min})"
                    },
                    "below_confidence_threshold"
                )
                return None
            
            return signal
        else:
            # Signal hasn't reached follow_through threshold yet
            # Log it as near-miss for tracking
            if size > 500 and side in ["bid", "ask"] and ft_count > 0:
                direction = "LONG" if side == "bid" else "SHORT"
                confidence = min(100, 65 + (size / 1000 * 10) + (symbol_data["displacement"] * 20))
                
                partial_signal = {
                    "timestamp_et": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "direction": direction,
                    "entry": round(price, 4),
                    "confidence": round(confidence, 1),
                    "regime": regime,
                    "displacement": round(symbol_data["displacement"], 4),
                    "delta_accel": round(symbol_data["delta_accel"], 6),
                }
                
                log_rejected_signal(
                    partial_signal,
                    {
                        "follow_through_count": ft_count,
                        "follow_through_min": follow_through_min,
                        "rejection_detail": f"Insufficient follow-through: {ft_count}/{follow_through_min}"
                    },
                    "insufficient_follow_through"
                )
        
        return None
    
    return patched_detect_sweep_signal


def enable_near_miss_tracking(engine_module=None):
    """
    Enable near-miss tracking in the live alert engine.
    
    Can be called from live_alert_engine.py or as a standalone initialization.
    """
    init_near_miss_csv()
    near_miss_state["enabled"] = True
    
    # Write marker file so other processes know tracking is active
    NEAR_MISS_TRACKING_ENABLED.write_text(json.dumps({
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "csv_file": str(NEAR_MISS_CSV),
    }))
    
    print("[NEAR-MISS] Tracking enabled")
    print(f"[NEAR-MISS] Logging to: {NEAR_MISS_CSV}")


def get_near_miss_summary():
    """Get current near-miss summary."""
    return {
        "tracking_enabled": near_miss_state["enabled"],
        "total_tracked_signals": near_miss_state["tracked_signals"],
        "total_rejected_signals": near_miss_state["rejected_signals"],
        "rejection_reasons": dict(near_miss_state["rejection_reasons"]),
    }


def is_near_miss(signal_dict, confidence_threshold=65, follow_through_threshold=2):
    """
    Determine if a signal object is a near-miss.
    
    Near-miss: Signal that:
    - Has valid regime (not insufficient_data)
    - Has positive follow_through count
    - Fails confidence OR follow_through gate
    """
    regime = signal_dict.get("regime")
    if regime == "insufficient_data":
        return False
    
    confidence = signal_dict.get("confidence", 0)
    follow_through = signal_dict.get("follow_through_count", 0)
    
    # Passes initial gates but fails confidence/follow_through
    if follow_through > 0 and (confidence < confidence_threshold or follow_through < follow_through_threshold):
        return True
    
    return False
