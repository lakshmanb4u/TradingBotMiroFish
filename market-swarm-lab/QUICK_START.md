# Near-Miss Tracking - Quick Start

**Status**: ✅ ACTIVE NOW

## What's Running

```
Engine    (PID 72417): live_alert_engine.py - Generating signals
Monitor   (PID 73002): inject_near_miss_tracking.py - Tracking near-misses
```

## Files to Watch

```bash
# Real-time near-miss log
tail -f state/orderflow/live/near_miss_signals.csv

# Latest 15-min summary (updates every 15 min)
cat state/orderflow/live/near_miss_summary.json | jq .

# Engine health
cat state/orderflow/live/heartbeat.json | jq .
```

## Quick Commands

```bash
# Check status
bash scripts/near_miss_status.sh

# Watch summary (updates live)
watch -n 1 'cat state/orderflow/live/near_miss_summary.json | jq .'

# See recent near-misses
tail -20 state/orderflow/live/near_miss_signals.csv

# Check if processes are alive
ps aux | grep -E "live_alert|inject_near_miss" | grep -v grep
```

## What Gets Tracked

Every signal that:
- ✅ Has valid regime classification
- ✅ Has follow-through initiated  
- ❌ BUT fails confidence (< 65%) or follow-through (< 2 bars) gate

## Output

- **near_miss_signals.csv**: Every rejected signal with full context
- **near_miss_summary.json**: 15-min summary (total, reasons, assessment)
- **WhatsApp alert**: Every 15 minutes with summary

## Example Summary

```json
{
  "total_near_misses": 47,
  "rejection_reasons": {
    "below_confidence_threshold": 28,
    "insufficient_follow_through": 19
  },
  "gate_strictness_assessment": "appropriate"
}
```

## Strictness Levels

| Assessment | Meaning | Action |
|---|---|---|
| **strict** | Too many confidence rejections | Raise CONFIDENCE_MIN threshold |
| **appropriate** | Balanced rejections | Keep as-is |
| **loose** | Too many follow-through rejections | Raise FOLLOW_THROUGH_MIN |
| **no_near_misses** | No data to assess | Monitor next period |

## Stop/Restart

```bash
# Stop tracking (engine keeps running)
kill 73002

# Restart tracking
python3 scripts/inject_near_miss_tracking.py &

# Stop everything
kill 72417 73002

# Restart engine with native tracking (v6)
python3 scripts/live_alert_engine_v6_with_nearness.py &
```

## Key Metrics

- **Engine uptime**: 42+ minutes
- **Events processed**: 747,000+
- **Alerts generated**: 0 (waiting for qualified signals)
- **Feed status**: CONNECTED
- **Tracking interval**: 30 seconds
- **Summary interval**: 15 minutes

## Next Steps

1. ✅ Monitor is ACTIVE - no action needed
2. ⏳ First 15-min summary: ~11:16 PDT
3. 📊 Review summary and gate assessment
4. 🔧 Adjust thresholds if needed (optional)
5. 📲 WhatsApp alerts enable (when ready)

## Help

```bash
# Full documentation
cat NEAR_MISS_IMPLEMENTATION.md

# Status details
bash scripts/near_miss_status.sh

# View tracked signals
head -20 state/orderflow/live/near_miss_signals.csv

# Latest summary
jq . state/orderflow/live/near_miss_summary.json
```

---

**Started**: 2026-05-05 11:01 PDT  
**Status**: ✅ ACTIVE - No action required
