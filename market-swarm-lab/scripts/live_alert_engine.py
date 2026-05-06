#!/usr/bin/env python3
"""
Live Observational Alert Engine - Bookmap JSONL Processor
Reads live JSONL orderflow data, detects sweep patterns, generates alerts.
Observational only - no broker execution, no auto-trading.
"""

import json
import time
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict, deque
from pathlib import Path
import threading
import signal

# Config
JSONL_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/")
LIVE_DIR = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/")
LIVE_DIR.mkdir(parents=True, exist_ok=True)

# Output files
HEARTBEAT_FILE = LIVE_DIR / "heartbeat.json"
FEED_HEALTH_FILE = LIVE_DIR / "feed_health.json"
LATEST_SIGNAL_FILE = LIVE_DIR / "latest_signal.json"
ALERTS_CSV_FILE = LIVE_DIR / "live_alerts.csv"
SESSION_STATS_FILE = LIVE_DIR / "session_stats.json"

# Regime filter thresholds
REGIME_THRESHOLDS = {
    "trending": {"min_delta": 0.8, "min_displacement": 0.6},
    "mean_revert": {"max_delta": 0.3, "max_displacement": 0.3},
    "compression": {"min_vol": 5, "max_price_range": 1.0},
}

# Alert gate configuration
CONFIDENCE_MIN = 65  # Minimum confidence to alert
FOLLOW_THROUGH_MIN = 2  # Min bars to confirm follow-through
WHATSAPP_ENABLED = True  # Enable now that feed is SAFE

# Global state
running = True
stats = {
    "events_processed": 0,
    "alerts_generated": 0,
    "errors": 0,
    "start_time": datetime.now(timezone.utc).isoformat(),
    "last_event_time": None,
    "symbols_seen": set(),
    "feed_health": "INITIALIZING",
    "alert_engine": "STARTING",
    "whatsapp_enabled": WHATSAPP_ENABLED,
}

# Per-symbol tracking
symbol_state = defaultdict(lambda: {
    "last_price": None,
    "last_bid": None,
    "last_ask": None,
    "depths": deque(maxlen=100),
    "regime": None,
    "displacement": 0,
    "delta_accel": 0,
    "follow_through_count": 0,
})


def get_latest_jsonl_file():
    """Find the latest active JSONL file."""
    try:
        files = sorted([f for f in JSONL_DIR.glob("*.jsonl")])
        if not files:
            return None, "FEED_NOT_FOUND"
        
        latest = files[-1]
        stat = latest.stat()
        return latest, latest.name
    except Exception as e:
        return None, f"ERROR: {str(e)}"


def check_file_growth(filepath, poll_interval=5):
    """Check if JSONL file is actively growing."""
    try:
        initial_size = filepath.stat().st_size
        time.sleep(poll_interval)
        current_size = filepath.stat().st_size
        
        if current_size > initial_size:
            return True, current_size - initial_size
        return False, 0
    except Exception as e:
        return False, str(e)


def get_last_event_timestamp(filepath, tail_lines=50):
    """Extract last event timestamp from JSONL file."""
    try:
        with open(filepath, 'r') as f:
            f.seek(0, 2)  # End of file
            size = f.tell()
            buffer_size = min(size, 10000)
            f.seek(max(0, size - buffer_size))
            
            lines = f.read().split('\n')
            for line in reversed(lines):
                if line.strip():
                    try:
                        event = json.loads(line)
                        return event.get("ts_event"), event.get("seq")
                    except:
                        continue
        return None, None
    except Exception as e:
        return None, str(e)


def calculate_regime(symbol_data, symbol):
    """Classify market regime: trending, mean_revert, compression."""
    depths = symbol_data["depths"]
    
    if len(depths) < 10:
        return "insufficient_data"
    
    # Calculate metrics
    prices = [d.get("price", 0) for d in depths if d.get("price")]
    if len(prices) < 2:
        return "insufficient_data"
    
    price_range = max(prices) - min(prices)
    avg_price = sum(prices) / len(prices)
    volatility = sum((p - avg_price) ** 2 for p in prices) / len(prices) ** 0.5
    
    # Displacement: deviation from MA
    ma_10 = sum(prices[-10:]) / min(10, len(prices))
    displacement = abs(prices[-1] - ma_10) / ma_10 if ma_10 != 0 else 0
    
    # Delta acceleration: rate of change
    if len(prices) >= 3:
        delta1 = prices[-1] - prices[-2]
        delta2 = prices[-2] - prices[-3]
        delta_accel = abs(delta1 - delta2)
    else:
        delta_accel = 0
    
    # Classify
    if displacement > 0.6 and delta_accel > 0.01:
        regime = "trending"
    elif displacement < 0.3 and volatility < 0.02:
        regime = "mean_revert"
    elif price_range < 1.0 and volatility > 0.01:
        regime = "compression"
    else:
        regime = "transition"
    
    symbol_data["displacement"] = displacement
    symbol_data["delta_accel"] = delta_accel
    symbol_data["regime"] = regime
    
    return regime


def detect_sweep_signal(symbol, symbol_data, event):
    """Detect sweep patterns: aggressive buy/sell through multiple levels."""
    side = event.get("side")
    size = event.get("size", 0)
    price = event.get("price", 0)
    
    # Weak heuristic: large size at support/resistance
    regime = calculate_regime(symbol_data, symbol)
    
    # Track follow-through
    if size > 500 and side in ["bid", "ask"]:
        symbol_data["follow_through_count"] = symbol_data.get("follow_through_count", 0) + 1
    
    if symbol_data["follow_through_count"] >= FOLLOW_THROUGH_MIN:
        # Generate signal confidence
        confidence = min(100, 65 + (size / 1000 * 10) + (symbol_data["displacement"] * 20))
        
        direction = "LONG" if side == "bid" else "SHORT"
        entry = price
        stop = price * 0.99 if side == "bid" else price * 1.01
        target1 = price * 1.01 if side == "bid" else price * 0.99
        target2 = price * 1.02 if side == "bid" else price * 0.98
        
        signal = {
            "timestamp_et": datetime.now().isoformat(),
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
        
        return signal if confidence >= CONFIDENCE_MIN else None
    
    return None


def process_jsonl_line(line):
    """Process a single JSONL event."""
    global stats
    
    try:
        event = json.loads(line.strip())
        symbol = event.get("symbol")
        
        if not symbol:
            return None
        
        # Track state
        stats["events_processed"] += 1
        stats["last_event_time"] = event.get("ts_event")
        stats["symbols_seen"].add(symbol)
        
        # Update symbol state
        sdata = symbol_state[symbol]
        sdata["last_price"] = event.get("price")
        sdata["depths"].append(event)
        
        # Detect signals
        signal = detect_sweep_signal(symbol, sdata, event)
        
        return signal
    except json.JSONDecodeError:
        stats["errors"] += 1
        return None
    except Exception as e:
        stats["errors"] += 1
        return None


def write_heartbeat():
    """Write live heartbeat."""
    heartbeat = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "feed_status": stats["feed_health"],
        "alert_engine": stats["alert_engine"],
        "events_processed": stats["events_processed"],
        "alerts_generated": stats["alerts_generated"],
        "last_event_time": stats["last_event_time"],
        "symbols_active": len(stats["symbols_seen"]),
        "whatsapp_enabled": stats["whatsapp_enabled"],
    }
    
    with open(HEARTBEAT_FILE, 'w') as f:
        json.dump(heartbeat, f, indent=2)


def write_feed_health():
    """Write feed health status."""
    health = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SAFE" if stats["feed_health"] == "CONNECTED" else "DEGRADED",
        "feed_connected": stats["feed_health"] == "CONNECTED",
        "events_sec": stats["events_processed"] / max(1, (time.time() - stats.get("session_start", time.time()))),
        "last_event": stats["last_event_time"],
        "active_symbols": list(stats["symbols_seen"])[:10],
    }
    
    with open(FEED_HEALTH_FILE, 'w') as f:
        json.dump(health, f, indent=2)


def write_session_stats():
    """Write session statistics."""
    session = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "start_time": stats["start_time"],
        "runtime_seconds": time.time() - stats.get("session_start", time.time()),
        "total_events": stats["events_processed"],
        "total_alerts": stats["alerts_generated"],
        "errors": stats["errors"],
        "unique_symbols": len(stats["symbols_seen"]),
        "feed_health": stats["feed_health"],
        "alert_engine_status": stats["alert_engine"],
        "whatsapp_alerts_enabled": stats["whatsapp_enabled"],
    }
    
    with open(SESSION_STATS_FILE, 'w') as f:
        json.dump(session, f, indent=2)


def main_loop(jsonl_file):
    """Main event processing loop."""
    global running, stats
    
    stats["session_start"] = time.time()
    stats["feed_health"] = "CONNECTED"
    stats["alert_engine"] = "RUNNING"
    
    # Initialize CSV
    with open(ALERTS_CSV_FILE, 'w') as f:
        f.write("timestamp_et,symbol,direction,entry,stop,target1,target2,confidence,reason_codes,regime,displacement\n")
    
    try:
        with open(jsonl_file, 'r') as f:
            # Seek near end
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 100000))
            
            while running:
                line = f.readline()
                
                if not line:
                    # No new data, sleep briefly
                    time.sleep(0.1)
                    continue
                
                # Process event
                signal = process_jsonl_line(line)
                
                if signal:
                    stats["alerts_generated"] += 1
                    
                    # Write signal
                    with open(LATEST_SIGNAL_FILE, 'w') as sf:
                        json.dump(signal, sf, indent=2)
                    
                    # Append to CSV
                    csv_line = f"{signal['timestamp_et']},{signal['symbol']},{signal['direction']},{signal['entry']},{signal['stop']},{signal['target1']},{signal['target2']},{signal['confidence']},\"{';'.join(signal['reason_codes'])}\",{signal['regime']},{signal['displacement']}\n"
                    with open(ALERTS_CSV_FILE, 'a') as af:
                        af.write(csv_line)
                    
                    # WhatsApp alert if enabled
                    if stats["whatsapp_enabled"] and signal["confidence"] >= CONFIDENCE_MIN:
                        print(f"[ALERT] {signal['symbol']} {signal['direction']} @ {signal['entry']} (conf: {signal['confidence']}%)")
                
                # Periodic writes
                if stats["events_processed"] % 1000 == 0:
                    write_heartbeat()
                    write_feed_health()
                    write_session_stats()
    
    except KeyboardInterrupt:
        print("\n[STOP] Received interrupt signal")
        running = False
    except Exception as e:
        print(f"[ERROR] Main loop: {e}")
        stats["feed_health"] = "DEGRADED"
        stats["alert_engine"] = "ERROR"


def signal_handler(sig, frame):
    """Handle signals gracefully."""
    global running
    running = False
    print("\n[SHUTDOWN] Cleaning up...")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("[START] Live Orderflow Alert Engine")
    print(f"[CONFIG] Output: {LIVE_DIR}")
    
    # Find latest JSONL
    jsonl_file, status = get_latest_jsonl_file()
    print(f"[FEED] Status: {status}")
    
    if not jsonl_file:
        print("[ERROR] No JSONL file found!")
        stats["feed_health"] = "NOT_FOUND"
        write_heartbeat()
        write_feed_health()
        write_session_stats()
        sys.exit(1)
    
    print(f"[FEED] File: {jsonl_file.name}")
    
    # Check file growth
    is_growing, growth_bytes = check_file_growth(jsonl_file, poll_interval=2)
    print(f"[FEED] Growing: {is_growing} ({growth_bytes} bytes in 2s)")
    
    if not is_growing:
        print("[WARN] File not growing - feed may be stale")
        stats["feed_health"] = "DEGRADED"
    
    # Get last event
    last_ts, last_seq = get_last_event_timestamp(jsonl_file)
    print(f"[FEED] Last event: {last_ts} (seq: {last_seq})")
    
    # Start main loop
    print("[RUN] Starting event processing loop...")
    main_loop(jsonl_file)
    
    # Final status
    write_heartbeat()
    write_feed_health()
    write_session_stats()
    print("[END] Alert engine stopped")
