# Feed Integrity Audit Guide

Quick reference for running feed audits on Bookmap/Rithmic streams.

## Overview

Two tools analyze different aspects of the live trade feed:

1. **`feed_integrity_audit.py`** — Batch analysis of historical JSONL files
2. **`inspect_live_feed.py`** — Live tail with 60-second snapshot metrics

## Usage

### Full Audit of a JSONL File

```bash
# Default: read entire file
python3 scripts/feed_integrity_audit.py \
  market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl

# Limit to first 1M events (for quick sampling)
python3 scripts/feed_integrity_audit.py \
  market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl \
  --limit 1000000

# Output as JSON (for scripting)
python3 scripts/feed_integrity_audit.py \
  market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl \
  --json > audit_results.json
```

### Live Feed Inspection

```bash
# Default: tail live file for 60 seconds
python3 scripts/inspect_live_feed.py

# Custom duration
python3 scripts/inspect_live_feed.py --duration 120

# Tail a specific file
python3 scripts/inspect_live_feed.py \
  --file /path/to/es_orderflow_2026-05-05.jsonl \
  --duration 60
```

## Output Interpretation

### Batch Audit Output

```
TRADE ANALYSIS:
  Total trades:                886,927
  Zero-size trades:            310,244 (34.98%)  ← BLOCKER
  Avg size:                    0.80
  Max size:                    878
  Aggressors:                  {'bid': 449707, 'ask': 437220}
```

**Key Metrics:**
- **Zero-size percent** > 5% → Filter trades
- **Aggressors balanced** (near 50/50) → Delta likely valid
- **Max size vs avg** — Check for outliers (possible data errors)

```
DEPTH ANALYSIS:
  Total depth events:          14,121,186
  Spread violations:           166,012  ← BLOCKER
  Bid updates:                 {...}
  Ask updates:                 {...}
```

**Key Metrics:**
- **Spread violations** > 0.1% → Validate bid < ask
- **Bid/ask ratio** — Should be ~50/50

```
SEQUENCING ANALYSIS:
  Out-of-order events:         2
  Sequence gaps:               2,145,623
  Duplicate timestamps:        {...}
  Timestamp inversions:        2
```

**Key Metrics:**
- **Out-of-order** > 10 → Implement event buffering
- **Gaps** — Expected if upstream filtering; monitor trends
- **Duplicate timestamps** — Use seq as tiebreaker for ordering

```
BLOCKERS:
  • BLOCKER: 310244 zero-size trades (34.98%) — breaks delta/absorption logic
  • BLOCKER: 166012 bid >= ask violations — depth inconsistent
```

**Action:**
- Red items = must fix before production
- Yellow items = should fix before trading
- Green = acceptable risk

### Live Inspection Output

```
THROUGHPUT:
  Trades/sec:                  49.9
  Depth updates/sec:           1029.4
```

**Sanity Check:**
- Trades/sec should be 50-200 during active hours
- Depth updates/sec should be 500-2000 (depends on volatility)

```
QUALITY:
  Zero-size trades:            979 (32.71%)
```

**Same threshold as batch:** > 5% is bad.

```
TOP SYMBOLS:
  ESM6.CME@RITHMIC:
    Trades: 2,086
    Delta: +249 (up:1,228, down:979)
    Spread: 14.25 ticks (avg: 8.55, max: 728.00)
```

**Interpretation:**
- **Delta +249** → Net buying (bullish)
- **Avg spread 8.55** → Typical for ES (varies by time of day)
- **Max spread 728** → Anomaly; check if this was during news/gap

```
SAFETY VERDICT FOR DELTA/ABSORPTION/DISPLACEMENT/FOLLOW-THROUGH:
  ⚠️  UNSAFE: 32.7% zero-size trades — delta computation unreliable
  ✓ Aggressor flags present and reliable
  ✓ Spread sanity maintained
  
  ❌ OVERALL: Feed has issues; recommend fixing before production
```

**Final Decision:**
- ✅ = Safe for trading
- ⚠️ = Proceed with caution; monitor closely
- ❌ = DO NOT trade until issues fixed

## Blockers Reference

### CRITICAL

| Issue | Threshold | Fix |
|-------|-----------|-----|
| Zero-size trades | > 5% | Filter `size == 0` at ingestion |
| Bid >= ask | > 100 events | Validate spread before updating |
| Out-of-order | > 50 events | Buffer 100ms, sort by (ts_event, seq) |

### HIGH

| Issue | Threshold | Fix |
|-------|-----------|-----|
| Timestamp inversions | > 10 events | Enforce `ts_new >= ts_last` |
| Duplicate timestamps | > 50% | Use seq as tiebreaker |
| Missing aggressors | > 1% | Ensure all trades have `is_bid_aggressor` |

## Workflow Example

### Pre-Production Checklist

```bash
# 1. Audit latest file
python3 scripts/feed_integrity_audit.py \
  market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl \
  --json > audit_2026-05-05.json

# 2. Check if any blockers
grep -i "blocker" audit_2026-05-05.json

# 3. Run live inspection
python3 scripts/inspect_live_feed.py --duration 120

# 4. Compare live metrics to historical (% zero-size should match)
# If live % zero-size > historical, there's a data issue

# 5. Deploy fixes if needed
# 6. Re-audit after fixes: should see < 5% zero-size
```

### Post-Fix Validation

```bash
# After deploying zero-size filter:
python3 scripts/feed_integrity_audit.py \
  market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl \
  --limit 100000

# Should show:
#   Zero-size trades: 0 (0.00%)  ← Confirmed fix works
```

## Common Issues & Fixes

### Issue: "32% zero-size trades"

**Cause:** Bookmap API generates zero-sized events (possibly for order management).

**Fix:**
```python
if event["event_type"] == "trade" and event["size"] == 0:
    continue  # Skip
```

**Validation:**
```bash
python3 scripts/feed_integrity_audit.py ... | grep "zero-size"
# Should show: Zero-size trades: 0 (0.00%)
```

---

### Issue: "166K bid >= ask violations"

**Cause:** Async bid/ask updates create race conditions.

**Fix:**
```python
if new_bid >= current_ask:
    log.warn(f"Spread violation, skipping bid update")
    continue
```

**Validation:**
```bash
python3 scripts/feed_integrity_audit.py ... | grep "Spread violations"
# Should show: Spread violations: 0
```

---

### Issue: "Duplicate timestamps (73.9%)"

**Cause:** Sub-millisecond events share the same timestamp.

**Fix:** Use `seq` as tiebreaker:
```python
events.sort(key=lambda e: (e["ts_event"], e["seq"]))
```

**Note:** This is acceptable; events within the same ms are inherently unordered.

---

### Issue: "2 out-of-order events"

**Cause:** Very rare; clock skew or buffering issue.

**Fix:** Enable 100ms event buffer:
```python
buffer.wait(100)  # Collect events for 100ms
buffer.sort(key=lambda e: (e["ts_event"], e["seq"]))
for event in buffer.flush():
    process(event)
```

---

### Issue: "Live audit shows different % than batch"

**Cause:** Market conditions change; morning vs. midday vs. close have different quality.

**Fix:**
```bash
# Run multiple audits throughout the day
for file in /path/to/es_orderflow_2026-05-*.jsonl; do
  python3 scripts/feed_integrity_audit.py "$file" --limit 100000
done

# If all show similar %, feed is stable ✓
# If they diverge, investigate specific times
```

---

## Metrics to Track

For ongoing monitoring, log these metrics daily:

```json
{
  "date": "2026-05-05",
  "total_trades": 886927,
  "zero_size_pct": 34.98,
  "spread_violations": 166012,
  "out_of_order_events": 2,
  "duplicate_timestamps_pct": 73.9,
  "aggressor_coverage": 100.0,
  "status": "UNSAFE"
}
```

**Trends to monitor:**
- ✅ Zero-size % should **decrease** after filter deployed
- ✅ Spread violations should **decrease** after validation added
- ✅ Out-of-order should **stay < 10**
- ⚠️ Duplicate timestamps are OK; just need seq tiebreaker

---

## Files

- **Audit Script:** `scripts/feed_integrity_audit.py`
- **Live Inspection:** `scripts/inspect_live_feed.py`
- **Full Report:** `reports/live_feed_integrity_audit.md`
- **Data Source:** `market-swarm-lab/state/orderflow/bookmap_api/`

---

**Last Updated:** 2026-05-05 07:51 PDT
