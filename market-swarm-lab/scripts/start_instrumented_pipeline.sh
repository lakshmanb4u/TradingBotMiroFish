#!/bin/bash

# Start Script: Instrumented Pipeline with Funnel Tracking
# Kills the old engine, starts the new instrumented version, and optionally monitors the funnel

set -e

MARKET_SWARM_DIR="/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab"
SCRIPTS_DIR="$MARKET_SWARM_DIR/scripts"
LIVE_DIR="$MARKET_SWARM_DIR/state/orderflow/live"
VENV_DIR="$MARKET_SWARM_DIR/.venv"

echo "[STARTUP] Instrumented Pipeline with Conversion Funnel Tracking"
echo "[CONFIG] Market Swarm Lab: $MARKET_SWARM_DIR"
echo "[CONFIG] Live Output Dir: $LIVE_DIR"

# Kill existing live_alert_engine if running
echo "[STOP] Checking for running live_alert_engine..."
if pgrep -f "live_alert_engine.py" > /dev/null; then
    echo "[STOP] Found running live_alert_engine.py - terminating..."
    pkill -f "live_alert_engine.py" || true
    sleep 2
else
    echo "[INFO] No running live_alert_engine found"
fi

# Activate virtual environment if it exists
if [ -f "$VENV_DIR/bin/activate" ]; then
    echo "[ENV] Activating virtual environment..."
    source "$VENV_DIR/bin/activate"
fi

# Start instrumented engine
echo "[START] Launching instrumented live_alert_engine..."
python3 "$SCRIPTS_DIR/live_alert_engine_instrumented.py" &
ENGINE_PID=$!

echo "[PID] Instrumented engine started with PID: $ENGINE_PID"
sleep 2

# Give it a moment to initialize
echo "[WAIT] Waiting for engine initialization..."
sleep 3

# Optional: Start monitor if requested
if [ "$1" = "--monitor" ]; then
    echo "[MONITOR] Starting live funnel monitor..."
    sleep 1
    python3 "$SCRIPTS_DIR/monitor_pipeline_funnel.py"
else
    echo "[INFO] To monitor funnel metrics, run: python3 $SCRIPTS_DIR/monitor_pipeline_funnel.py"
    echo "[INFO] Keep this terminal open to maintain engine process"
    
    # Wait for engine to complete
    wait $ENGINE_PID
fi
