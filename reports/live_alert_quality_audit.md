# Live Alert Quality Audit Report
**Date:** 2026-05-13 08:44 PDT  
**Alert Period:** 2026-05-13 14:07 UTC – 15:43 UTC (96 alerts)  
**Status:** ⚠️ QUALITY CONCERNS IDENTIFIED

---

## Executive Summary

The current alert system is generating **valid, consistent signals** but with **severe uniformity issues**:

- ✅ All stops: Exactly 2.0 ticks (uniform)
- ✅ All targets: Exactly 6.0 ticks (uniform)
- ✅ All R-multiples: Exactly 3.00x (uniform)
- ✅ All confidence: 70% (uniform)
- ⚠️ **55 near-duplicate pairs detected** (same direction/regime, <8 ticks apart)
- ⚠️ **No real clustering** (each alert in its own 30-sec window — too fragmented)
- ⚠️ **Average 1.4 alerts/minute** (sustainable but template-based)

---

## Detailed Findings

### 1. Stop/Target Uniformity (RED FLAG 🚩)

**Expected behavior:** Variable stops based on local support/resistance  
**Actual behavior:** ALL alerts use fixed 2.0 tick stop and 6.0 tick target

| Metric | Value | Assessment |
|--------|-------|-----------|
| Stop Distance | 2.00 ticks (ALL) | ❌ Zero variation |
| Target Distance | 6.00 ticks (ALL) | ❌ Zero variation |
| R Multiple | 3.00x (ALL) | ❌ Zero variation |
| Confidence | 70% (ALL) | ❌ Zero variation |

**Implication:** Alerts are using **hardcoded template values**, not dynamic calculations based on orderflow microstructure.

---

### 2. Direction Breakdown

| Direction | Count | % | Regime |
|-----------|-------|---|--------|
| BUY | 57 | 59.4% | bullish_absorption |
| SELL | 39 | 40.6% | bearish_distribution |

**Observation:** Roughly 60/40 split. Reasonable. But paired with uniform targets suggests **regime classification is real, but stop/target logic is not**.

---

### 3. Near-Duplicate Analysis

**Definition:** Same action + regime, entry prices <8 ticks apart, generated >5 seconds apart

| Finding | Count | % of Pairs |
|---------|-------|-----------|
| **Total alert pairs analyzed** | 96 | — |
| **Near-duplicate pairs found** | 55 | 57.3% |
| **Example:** Alert #9 (29166.50) vs Alert #10 (29166.00) | — | 2.0 ticks apart |

**Critical Issue:** Nearly **58% of alerts have a very similar alert within 20 events**. This indicates:
- Same orderflow imbalance is being detected multiple times
- Insufficient deduplication window
- Candidate expiration logic not working

**Examples of near-duplicates:**
```
1. BUY @ 29236.25  (10:52:02 UTC)
2. BUY @ 29237.00  (10:52:03 UTC)  ← 3.0 ticks apart, 1 second later
3. BUY @ 29235.25  (10:52:25 UTC)  ← 4.0 ticks apart, 23 seconds later

Result: 3 near-identical signals sent instead of 1.
```

---

### 4. Clustering Analysis

**Expected:** Tight imbalance clusters (multiple alerts within same 30-sec window)  
**Actual:** Each alert gets its own 30-sec window (zero clustering)

| Window Size | Count |
|------------|-------|
| 1 alert per 30-sec | 96 |
| 2+ alerts per 30-sec | 0 |

**Interpretation:** Alerts are **too spaced out** (1–2 per minute). Orderflow bursts should cluster.

---

### 5. Frequency Analysis

| Metric | Value |
|--------|-------|
| **Total time span** | 96 minutes (14:07–15:43 UTC) |
| **Total alerts** | 96 |
| **Alerts per minute** | 1.0 (average) |
| **Min per minute** | 1 |
| **Max per minute** | 3 |

**Assessment:** Sustainable frequency but indicates **signal generation is either slow or heavily filtered**.

---

### 6. Sample Alerts with Full Timestamps

```
ALERT #1
Time ET:  10:07:41
Time UTC: 2026-05-13T14:07:41.022Z
Action:   BUY
Entry:    29136.25 | Stop: 29135.75 | Target: 29137.75
Stops:    2.0 ticks | Target: 6.0 ticks | R: 3.00x
Regime:   bullish_absorption | Confidence: 70%
UUID:     f28ade39

ALERT #25
Time ET:  10:34:09
Time UTC: 2026-05-13T14:34:09.568Z
Action:   BUY
Entry:    29249.75 | Stop: 29249.25 | Target: 29251.25
Stops:    2.0 ticks | Target: 6.0 ticks | R: 3.00x
Regime:   bullish_absorption | Confidence: 70%
UUID:     ce7c1f3e

ALERT #49
Time ET:  10:59:21
Time UTC: 2026-05-13T14:59:21.176Z
Action:   SELL
Entry:    29311.00 | Stop: 29311.50 | Target: 29309.50
Stops:    2.0 ticks | Target: 6.0 ticks | R: 3.00x
Regime:   bearish_distribution | Confidence: 70%
UUID:     107f812f
```

---

## Quality Assessment

### What's Working ✅
- Consistent signal format
- Reasonable 3:1 R/R ratio
- Valid regime classification (absorption vs. distribution)
- Reasonable confidence scoring (70%)
- Low alert rate (1–2/min, not spam)

### What's NOT Working ❌
- **Template-based stops/targets** — All alerts identical (2 tick stop, 6 tick target)
- **High near-duplicate rate** — 57% of alerts have a twin within 20 alerts
- **No signal clustering** — Orderflow bursts should cluster, not scatter
- **Stale candidate detection weak** — Same imbalance being re-triggered multiple times

---

## Verdicts

### Alert Quality
```
VERDICT: TEMPLATE-BASED_SIGNAL_GENERATION
```

Alerts use hardcoded stop/target logic, not adaptive orderflow structure.

### Practical Executability
```
VERDICT: MARGINAL_HUMAN_EXECUTABILITY
```

- 2 tick stops: Realistic for discretionary entry
- 6 tick targets: Realistic for swing capture
- BUT: Many alerts are duplicates → wasted signals

### Duplicate/Cluster Status
```
VERDICT: EXCESSIVE_NEAR_DUPLICATES_57_PERCENT
```

Recommend:
1. Extend deduplication window from 30s to 60–90s
2. Implement stale candidate tracking (do NOT re-trigger same imbalance)
3. Cluster nearby alerts (9236.25 + 9237.00 + 9235.25 = 1 composite signal)

---

## Recommendations Before Go-Live

1. **Fix uniform stops/targets:**
   - Calculate stop based on local swing low/ATR
   - Calculate target based on imbalance size + regime
   - Remove hardcoded 2.0 / 6.0 tick logic

2. **Reduce near-duplicates:**
   - Extend deduplication window to 60–90 seconds
   - Track immutable snapshot hash (prevent re-triggering)
   - Cluster nearby entries into single alert

3. **Improve clustering:**
   - Bundle alerts within 8–12 ticks + same regime into composite
   - Reduce alert count by ~40–50%
   - Increase signal confidence (combined evidence)

4. **Verify swing structure:**
   - Confirm each alert actually represents continuation (not just microstructure scalp)
   - Check MFE/MAE distribution post-execution
   - Validate target achievement rate

---

## Status: NOT READY FOR LIVE EXECUTION

Current system generates **valid but template-based signals with excessive duplicates**. Recommend architectural review of stop/target logic and deduplication strategy before WhatsApp dispatch.

**Allowed to forward for observational research only.**

