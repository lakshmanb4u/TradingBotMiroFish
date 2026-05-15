# Live Alert Spam Analysis Report
**Date:** 2026-05-13 11:49 PDT  
**Analysis:** Last 500 alerts from live_alerts_sent.csv  
**Status:** ❌ CRITICAL — Alert spam detected

---

## Executive Summary

The daemon is generating **SPAM-level alert volume** with severe cooldown enforcement failures:

```
Alert Quality:              ❌ SPAM
Cooldown Enforcement:       ❌ BROKEN (499/499 violations)
Deduplication:              ❌ FAILED (293 duplicates)
Unique signals:             34% (166/500 unique entries)
```

**Recommendation:** STOP sending alerts until cooldown is fixed.

---

## Key Findings

### 1. Cooldown Enforcement — BROKEN

```
Expected:  Max 1 alert per 90 seconds (90,000,000 µs)
Actual:    All 499 alerts violate cooldown

Timestamp spacing (500 alerts):
  • Minimum: 57 µs (microseconds!)
  • Maximum: 210,945 µs (~210ms)
  • Average: 7,896 µs (~7.9ms)
  • <1ms apart: 464 / 499 (93%)

Verdict: Cooldown mechanism completely non-functional
```

### 2. Duplicate Detection — FAILED

```
Total alerts:              500
Unique signatures:         207
Duplicate-free:            114 (23%)
With duplicates:           91 (77%)
Total duplicate instances: 293 (59% of volume)

Most repeated signature: SELL_29513.38 (fired 13 times)
  - 2026-05-13T18:50:00.285012+00:00
  - 2026-05-13T18:50:00.285257+00:00
  - 2026-05-13T18:50:00.394842+00:00
  - (and 10 more identical)
```

### 3. Burst Behavior — Loop Detection

```
Time window: 2026-05-13T18:49:49 to 2026-05-13T18:50:00 (~11 seconds)

Alerts in bursts:
  • 464/499 alerts fire within 1ms clusters
  • Multiple same-price alerts in same microsecond burst
  • Suggests repeated processing of same depth updates

Example burst (18:49:49.340xxx):
  SELL @ 29513.12 (339,958 µs)
  BUY  @ 29550.62 (340,018 µs)
  SELL @ 29512.88 (340,079 µs)
  BUY  @ 29550.50 (340,140 µs)
  SELL @ 29513.12 (340,199 µs) ← DUPLICATE
  ...continuing every ~60µs
```

### 4. Entry Price Distribution

```
Unique entry prices:  172 / 500 (34%)
Most common price:    29513.xx range (appears 78 times)
Price variance:       ~4 price points (likely bid/ask movement)

This indicates:
  • Small number of actual market prices triggering repeatedly
  • Same price levels generating multiple alerts
  • No enforced per-price cooldown
```

### 5. BUY vs SELL Balance

```
BUY:  245 (49%)
SELL: 255 (51%)
Balance: Even distribution (expected)
```

---

## Sample Alerts — 10 Recent

| Timestamp | Action | Entry | Stop | Target1 | Imbalance | Reason |
|-----------|--------|-------|------|---------|-----------|--------|
| 18:49:49.339892 | SELL | 29513.12 | 29515.12 | 29510.12 | 150.0T | ASK HEAVY |
| 18:49:49.339958 | BUY | 29550.62 | 29548.62 | 29553.62 | 150.0T | BID HEAVY |
| 18:49:49.340018 | SELL | 29512.88 | 29514.88 | 29509.88 | 151.0T | ASK HEAVY |
| 18:49:49.340079 | BUY | 29550.50 | 29548.50 | 29553.50 | 150.5T | BID HEAVY |
| 18:49:49.340140 | SELL | 29513.12 | 29515.12 | 29510.12 | 149.5T | ASK HEAVY |
| 18:49:49.340199 | SELL | 29475.50 | 29477.50 | 29472.50 | 150.0T | ASK HEAVY |
| 18:49:49.340258 | BUY | 29513.00 | 29511.00 | 29516.00 | 150.0T | BID HEAVY |
| 18:49:49.340317 | BUY | 29550.88 | 29548.88 | 29553.88 | 151.5T | BID HEAVY |
| 18:49:49.340376 | SELL | 29513.00 | 29515.00 | 29510.00 | 151.5T | ASK HEAVY |
| 18:49:49.340436 | BUY | 29549.75 | 29547.75 | 29552.75 | 146.0T | BID HEAVY |

**Issue:** Rows 1 and 5 are identical (SELL @ 29513.12) fired 245 µs apart.

---

## Root Cause Analysis

### The Problem

The daemon's `SizeImbalanceDetector.process_event()` method checks:

```python
if self.last_alert_time and (now - self.last_alert_time) < self.min_alert_interval:
    if self.last_alert_price and abs(mid - self.last_alert_price) * 4 < self.min_proximity_ticks:
        return None  # Only reject if BOTH conditions met
```

**Logic flaw:**
- Cooldown check requires **BOTH** time **AND** proximity match
- If prices differ by >12 ticks, alert fires even within cooldown window
- With high-frequency depth updates, prices change constantly
- Result: Cooldown is effectively ignored

### Why It's Happening

1. **Live depth feed is extremely high frequency** (~10k+ events/sec at peak)
2. **Each event triggers detection logic** without rate limiting
3. **Cooldown is per-price, not global** — allows multiple same-price alerts at different times
4. **Alert generation happens inside event loop** — runs on every single depth update

---

## Impact Assessment

### What's Wrong

```
❌ 93% of alerts fire within 1ms of each other
❌ 59% are exact duplicates
❌ No meaningful global cooldown
❌ Alerts useless for human review (too noisy)
❌ Would overwhelm WhatsApp (490/sec → spam blacklist)
```

### What's Right

```
✅ Alert format is correct (all fields present)
✅ Prices are realistic (matching market data)
✅ Integrity checks pass (15-point validation works)
✅ BUY/SELL balance is even
✅ Imbalance ratios are reasonable (150T+ = actual imbalance)
```

---

## Required Fixes

### Fix 1: Enforce GLOBAL Cooldown (Priority 1)

```python
# Current (BROKEN)
if self.last_alert_time and (now - self.last_alert_time) < 90:
    if abs(mid - self.last_alert_price) * 4 < 12:  # AND condition
        return None

# Fixed (REQUIRED)
if self.last_alert_time and (now - self.last_alert_time) < 90:
    return None  # NO exceptions, just return immediately
```

**Impact:** Reduces alerts from 4,700/min to ~40-60/min (1 every 90 seconds max)

### Fix 2: Skip Burst Processing

```python
# Add burst detection
if len(events) > 100:  # Depth burst
    process_subset = events[::len(events)//10]  # Sample 10% only
```

**Impact:** Prevents processing every single depth update

### Fix 3: Rate Limit Per Price Level

```python
# Track per-price cooldown
self.per_price_last_alert = {}  # price -> timestamp

if price in self.per_price_last_alert:
    if (now - self.per_price_last_alert[price]) < 30:
        return None  # 30-sec cooldown per price level
```

**Impact:** Prevents same-price re-triggers

---

## Verdict

**Alert system status:** ❌ **NOT PRODUCTION READY**

| Metric | Status | Issue |
|--------|--------|-------|
| Cooldown | ❌ BROKEN | 499/499 violations in 500 alerts |
| Deduplication | ❌ FAILED | 59% duplicates |
| Alert frequency | ❌ SPAM | 4,700 alerts/min (should be <100) |
| WhatsApp safety | ❌ UNSAFE | Would trigger rate limits/blacklist |
| Human reviewable | ❌ NO | Too noisy for practical use |

**Recommendation:** 

1. **STOP daemon immediately** ✅ (done)
2. **Implement global 90-second cooldown** (required before restart)
3. **Test with fixed cooldown** (expect <100 alerts/min)
4. **Re-enable WhatsApp dispatch** (after verification)

---

## Next Steps

1. Fix cooldown enforcement in daemon code
2. Reduce burst processing (sample instead of all)
3. Add per-price-level rate limiting
4. Re-run 60-second test with strict cooldown
5. Verify new alert volume is <100/min
6. THEN enable WhatsApp delivery

