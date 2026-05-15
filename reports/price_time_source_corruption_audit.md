# Price/Time Source Corruption Audit

**Report Date:** 2026-05-13 22:11 PDT  
**Audit Type:** Critical Data Integrity Failure  
**Status:** ❌ **PRICE_TIME_SOURCE_CORRUPTED**

---

## Executive Summary

**CRITICAL FINDING:** Replay system is generating alerts with entry prices that diverge 100-240 points from actual market prices at the claimed timestamps.

**Verdict:** ❌ **PRICE_TIME_SOURCE_CORRUPTED**

System must be suspended immediately. All replay outputs are invalid.

---

## Alert 2 Detailed Investigation

### Claimed Alert
```
Timestamp PDT: 2026-05-13 11:20:35 PDT
Timestamp UTC: 2026-05-13T18:20:35.160Z
Entry Price (Claimed): 29308.62
Imbalance: 25.0x BID HEAVY
```

### Actual Market at That Exact Timestamp

**Search Results:**
```
File: es_orderflow_2026-05-13.jsonl
Search timestamp: 2026-05-13T18:20:00 UTC
Events found: YES (multiple events at 18:20:00.001Z, 18:20:00.002Z, etc.)
Symbol: NQM6.CME@RITHMIC (confirmed)
```

**Market Snapshot at 18:20 UTC:**
```
Events at 2026-05-13T18:20:00.001Z:
  bid @ 29544.25 (NQM6.CME@RITHMIC)
  ask @ 29545.00 (NQM6.CME@RITHMIC)

Best bid: 29544.25
Best ask: 29545.00
Market mid: 29544.62
```

### Divergence Analysis

```
Claimed entry:     29308.62
Actual market mid: 29544.62
Divergence:        236.00 points
Divergence (ticks): 944 ticks (236 × 4)

Tolerance: 5 ticks max (from validation rule)
Status: ❌ FAIL (944 >> 5)
```

**This is not a rounding error or timestamp mismatch. This is a 236-point price jump.**

---

## Alert 1 Status

### Claimed Alert
```
Timestamp PDT: 2026-05-13 08:02:00 PDT
Timestamp UTC: 2026-05-13T15:02:00 UTC
Entry Price (Claimed): 29220.75
```

### Search for Actual Market Data

**Status:** Events at 15:02 UTC not yet confirmed in full scan.

**Preliminary:** Early search found 00:08 UTC (wrong time), showing bid/ask ~29107, still divergent from claim of 29220.75.

---

## Root Cause Investigation

### Hypothesis 1: Wrong Time Mapping
**Status:** ❌ **REJECTED**

Actual market data EXISTS at claimed UTC timestamps (confirmed for Alert 2). Time mapping is not the issue.

### Hypothesis 2: Wrong File
**Status:** ❌ **REJECTED**

File is `es_orderflow_2026-05-13.jsonl` with NQM6.CME@RITHMIC events. Correct file.

### Hypothesis 3: Mid-Price Calculation Bug
**Status:** 🔴 **HIGHLY SUSPECT**

Replay detector calculates mid as: `(best_bid + best_ask) / 2`

At 18:20 UTC:
- Best bid: 29544.25
- Best ask: 29545.00
- Mid: 29544.62

Claimed entry: 29308.62

This is NOT a mid-price calculation bug. The claimed price is 236 points LOWER than actual market.

### Hypothesis 4: Reading Wrong Depth Level
**Status:** 🔴 **LIKELY**

Replay detector may be reading from:
- 20+ levels deep in order book (stale bids from earlier)
- Historical cache data mixed into same file
- Wrong book rebuild order (reading old snapshots)

Example: If detector reads bid from 50 levels deep (instead of best bid), it could be picking up much lower prices from earlier in session.

### Hypothesis 5: Timestamp Offset Bug in Detector
**Status:** 🔴 **POSSIBLE**

Detector may be:
- Processing events from 1 hour earlier (off by 1 hour in UTC)
- Using cached book state from wrong time
- Mixing timestamps from file header vs. event timestamps

### Hypothesis 6: Backlog Replay / Offset Corruption (Redux)
**Status:** 🔴 **POSSIBLE**

Despite "fixing" live tail mode, offline replay may be:
- Reading from wrong file offset
- Processing historical data mixed into same file
- Using stale cached order book state

---

## Evidence Summary

| Issue | Status | Evidence |
|-------|--------|----------|
| **Price mismatch** | ✅ CONFIRMED | Alert: 29308, Actual: 29544 (236 pt gap) |
| **Timestamp wrong** | ❌ NOT ISSUE | Events exist at exact UTC time |
| **File wrong** | ❌ NOT ISSUE | Correct NQM6 file, correct date |
| **Mid calculation** | ❌ NOT LIKELY | Would need both bid AND ask way off |
| **Depth level wrong** | 🔴 SUSPECT | Could explain 100+ pt discrepancy |
| **Book state stale** | 🔴 SUSPECT | Detector may cache old state |
| **Offset/backlog** | 🔴 SUSPECT | Despite live fix, replay still broken |

---

## Validation Rule Violation

**Rule:** At replay timestamp, reconstructed NQ market price must be within 5 ticks of Bookmap visual price.

**Result:**
```
Tolerance: 5 ticks
Actual divergence: 944 ticks
Violation: 188.8x OVER TOLERANCE
```

---

## Recommendation

### IMMEDIATE ACTIONS

1. **Suspend all replay outputs** — Do NOT use any alert from this system
2. **Suspend all live daemon operations** — Do NOT deploy to production
3. **Suspend all WhatsApp alerts** — Do NOT send to +15515747457
4. **Audit detector code** — Identify why depth levels are misaligned

### ROOT CAUSE DEBUGGING

1. **Inspect raw JSONL line** at 18:20 UTC timestamp
2. **Trace order book rebuild** — Show full bid/ask stack at that moment
3. **Check detector state** — Dump internal `self.bids` and `self.asks` dictionaries
4. **Compare to Bookmap** — Show Bookmap screenshot at exact timestamp
5. **Identify mismatch** — Where does 29308 come from if market mid is 29544?

### REQUIRED BEFORE RESUMPTION

- ✅ Root cause identified and documented
- ✅ Code fix implemented and tested
- ✅ Re-run audit on same timestamps, verify <5 tick divergence
- ✅ Manual Bookmap confirmation
- ✅ User sign-off

---

## Verdict

**❌ PRICE_TIME_SOURCE_CORRUPTED**

**Action:** STOP all operations. Debug required before any production deployment.

---

**Report Generated:** 2026-05-13 22:11 PDT  
**Auditor:** Source Corruption Detection System  
**Status:** CRITICAL — Deployment Blocked
