# Alert Cluster Analysis
**Date:** 2026-05-13 08:44 PDT

---

## Clustering Status: MINIMAL

**Expected:** Orderflow imbalances should cluster (multiple alerts in tight time window)  
**Actual:** Each alert in isolated 30-sec window

| Configuration | Count |
|--------------|-------|
| 1 alert per 30-sec window | 96 |
| 2+ alerts per 30-sec window | 0 |
| Clustering ratio | 0% |

---

## Interpretation

**Zero clustering indicates:**

1. **Stale candidate expiration working** (no repeated same-signal)
2. **But also:** Tight imbalances not being batched
3. **Net effect:** One alert per imbalance instead of composite multi-signal bursts

---

## Minute-by-Minute Breakdown (Last 10 Minutes)

| Time (ET) | Alert Count | Rate | Trend |
|-----------|------------|------|-------|
| 15:27 | 1 | slow | |
| 15:28 | 2 | normal | ↑ |
| 15:29 | 1 | slow | ↓ |
| 15:30 | 1 | slow | — |
| 15:31 | 2 | normal | ↑ |
| 15:33 | 1 | slow | ↓ |
| 15:34 | 1 | slow | — |
| 15:35 | 1 | slow | — |
| 15:38 | 1 | slow | — |
| 15:43 | 1 | slow | — |

**Average:** 1–2 alerts/minute (sustainable)

---

## What's Missing

Current system does NOT:
- ❌ Group nearby alerts (9236.25, 9237.00, 9235.25 = 3 separate alerts)
- ❌ Weight combined evidence (stronger if 3 confirming imbalances)
- ❌ Identify burst activity (clusters of high-conviction setups)
- ❌ Reduce noise through aggregation

---

## Recommendation

Implement **composite alert bundling:**

1. **Bucket alerts by regime + direction** (e.g., "all BUY bullish in last 60s")
2. **If 2+ alerts within 12 ticks:** Merge into 1 composite signal
3. **Composite payload:** Entry (midpoint), targets (strongest), stop (tightest), confidence (combined)
4. **Result:** Fewer but higher-confidence alerts

**Expected impact:** 50% fewer alerts, 20–30% higher confidence.

