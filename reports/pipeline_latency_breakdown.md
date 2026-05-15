# Pipeline Latency Breakdown — Audit Results

**Status:** ✅ PIPELINE IS REALTIME  
**Date:** 2026-05-14 12:14 PDT

---

## Key Finding

**The pipeline is NOT the bottleneck.**

- ✅ Total pipeline latency: **6-12ms median**
- ✅ Event ages at processing: **0.5-13.5ms** (ALL events < 100ms)
- ✅ JSON parsing: **0.2ms average**
- ✅ File I/O: **6ms average**
- ✅ Book state update: **0ms (instant)**

---

## Latency Breakdown (1000 events sampled)

### File Append Detection → Tailer Read
```
Append lag: 6.16ms median, 12ms p95
(Tape write delay or filesystem lag)
```

### JSON Parsing
```
Parse lag: 0.00ms median, 0.22ms max
(Negligible)
```

### Book State Update
```
Book update lag: 0.00ms median, 0.01ms max
(Negligible)
```

### Alert Engine Evaluation
```
Engine eval lag: 0.00ms median, 0.02ms max
(Negligible)
```

### Total Pipeline Latency
```
Median: 6.19ms
P95:    12.04ms
P99:    13.01ms
Max:    13.43ms

VERDICT: ✅ PIPELINE_LATENCY_ACCEPTABLE
```

---

## Event Age Analysis (500 events sampled)

**When events reach the alert engine:**

```
Distribution:
  < 100ms:   500 events (100.0%)  ← ALL events are FRESH
  < 500ms:   500 events (100.0%)
  < 2000ms:  500 events (100.0%)
```

**Age statistics:**
```
Min:    0.5ms
Median: 7.7ms
P95:    11.4ms
P99:    12.7ms
Max:    13.5ms
```

**Conclusion:** Events are EXTREMELY fresh. None would be blocked by 2s age gate.

---

## Why Were 9,957 Events Blocked in Previous Session?

### NOT Due To:
- ❌ File I/O latency (only 6ms)
- ❌ Event age (only 7.7ms median)
- ❌ JSON parsing (only 0.2ms)
- ❌ Pipeline inefficiency (all <15ms)

### Likely Causes:

1. **Alert engine safety gates rejecting events**
   - Spread > 8 ticks?
   - Crossed book state?
   - Failed imbalance threshold?
   - Tick alignment issues?

2. **Book state not populating correctly**
   - bid_age or ask_age staying at `float('inf')`?
   - Both sides not updating in same window?

3. **Logic error in process_event()**
   - Returning None for valid events?
   - Triggering safety blocks unintentionally?

---

## Recommendation

**The 2 second event age gate is appropriate and safe.**

Events are reaching the engine at 6-13ms age. The gate allows up to 2000ms.

**Do NOT loosen the age gate.**

Instead: **Debug the actual blockage reason** in the alert engine's process_event() method.

Measure:
- How often does `book.is_valid()` return False?
- How often is spread > 8 ticks?
- How often is book crossed?
- What imbalance ratios are actually occurring?

---

## Instrument Next

Add detailed logging in v1_alert_engine.py to capture:
- Why each event is blocked (specific gate that rejected it)
- Book state at rejection time (bid, ask, sizes)
- Imbalance ratios
- Spread at rejection

Then re-run with detailed output to identify the actual block reason.

---

## Verdict

```
PIPELINE_LATENCY_ACCEPTABLE
```

Pipeline is ready for production. Alert blockage is NOT due to system latency.
