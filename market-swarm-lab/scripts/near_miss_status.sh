#!/bin/bash
# Near-Miss Tracking Status Report

LIVE_DIR="/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live"

echo ""
echo "=================================="
echo "NEAR-MISS TRACKING STATUS"
echo "=================================="
echo ""

# Check processes
echo "📊 PROCESSES:"
echo -n "  Engine (live_alert_engine.py): "
if ps aux | grep -q "[l]ive_alert_engine.py"; then
    ENGINE_PID=$(pgrep -f "live_alert_engine.py" | head -1)
    echo "✅ Running (PID: $ENGINE_PID)"
else
    echo "❌ NOT running"
fi

echo -n "  Monitor (inject_near_miss_tracking): "
if ps aux | grep -q "[i]nject_near_miss_tracking"; then
    MONITOR_PID=$(pgrep -f "inject_near_miss_tracking" | head -1)
    echo "✅ Running (PID: $MONITOR_PID)"
else
    echo "❌ NOT running"
fi

echo ""
echo "📁 OUTPUT FILES:"

# Check near_miss_signals.csv
if [ -f "$LIVE_DIR/near_miss_signals.csv" ]; then
    LINES=$(wc -l < "$LIVE_DIR/near_miss_signals.csv")
    SIZE=$(ls -lh "$LIVE_DIR/near_miss_signals.csv" | awk '{print $5}')
    echo "  ✅ near_miss_signals.csv ($LINES rows, $SIZE)"
else
    echo "  ❌ near_miss_signals.csv (not found)"
fi

# Check near_miss_summary.json
if [ -f "$LIVE_DIR/near_miss_summary.json" ]; then
    echo "  ✅ near_miss_summary.json"
else
    echo "  ⏳ near_miss_summary.json (awaiting first 15-min cycle)"
fi

# Check engine files
if [ -f "$LIVE_DIR/heartbeat.json" ]; then
    echo "  ✅ heartbeat.json (engine alive)"
else
    echo "  ⚠️  heartbeat.json (not found)"
fi

if [ -f "$LIVE_DIR/live_alerts.csv" ]; then
    ALERTS=$(tail -1 "$LIVE_DIR/live_alerts.csv" 2>/dev/null | cut -d',' -f1)
    echo "  ✅ live_alerts.csv (last alert: $ALERTS)"
else
    echo "  ⚠️  live_alerts.csv (no alerts yet)"
fi

echo ""
echo "📈 STATISTICS:"

if [ -f "$LIVE_DIR/heartbeat.json" ]; then
    echo -n "  Events processed: "
    jq '.events_processed' "$LIVE_DIR/heartbeat.json" 2>/dev/null || echo "N/A"
    
    echo -n "  Alerts generated: "
    jq '.alerts_generated' "$LIVE_DIR/heartbeat.json" 2>/dev/null || echo "N/A"
    
    echo -n "  Feed status: "
    jq -r '.feed_status' "$LIVE_DIR/heartbeat.json" 2>/dev/null || echo "N/A"
fi

if [ -f "$LIVE_DIR/near_miss_summary.json" ]; then
    echo ""
    echo "📊 LATEST 15-MIN SUMMARY:"
    echo -n "  Total near-misses: "
    jq '.total_near_misses' "$LIVE_DIR/near_miss_summary.json" 2>/dev/null || echo "N/A"
    
    echo -n "  Gate assessment: "
    jq -r '.gate_strictness_assessment' "$LIVE_DIR/near_miss_summary.json" 2>/dev/null || echo "N/A"
    
    echo -n "  Top rejection reason: "
    jq -r '.rejection_reasons | keys[0]' "$LIVE_DIR/near_miss_summary.json" 2>/dev/null || echo "N/A"
fi

echo ""
echo "🔍 RECENT NEAR-MISSES:"
if [ -f "$LIVE_DIR/near_miss_signals.csv" ]; then
    RECENT=$(tail -5 "$LIVE_DIR/near_miss_signals.csv" 2>/dev/null | head -4)
    if [ -n "$RECENT" ]; then
        echo "$RECENT" | while IFS= read -r line; do
            SYMBOL=$(echo "$line" | cut -d',' -f2)
            CONF=$(echo "$line" | cut -d',' -f4)
            REASON=$(echo "$line" | cut -d',' -f5)
            if [ -n "$SYMBOL" ]; then
                echo "  • $SYMBOL: $CONF% confidence, rejected: $REASON"
            fi
        done
    else
        echo "  (no near-misses yet)"
    fi
fi

echo ""
echo "✅ Near-miss tracking is ACTIVE"
echo ""
echo "Next 15-min summary update: $(date -d '+14 minutes' 2>/dev/null || echo '(in ~14 minutes)')"
echo ""
