# Price Mismatch Analysis — CRITICAL FINDING

**Status:** 🛑 STOP ALL ALERT WORK  
**Verdict:** WRONG_DATA_OR_FILE_STALE

---

## Summary

OpenClaw is reading prices from the correct file (NQM6.CME@RITHMIC) and parsing correctly, but **the prices don't match the current Bookmap visual**.

**Reconstructed from file:**
- Latest BID: 28336.0
- Latest ASK: 28412.0

**Your visual Bookmap shows:**
- BID ~29760
- ASK ~29765

**Difference: 1424 points (5696 ticks)**

---

## Root Cause Analysis

### File Information
- **Path:** `state/orderflow/bookmap_api/es_orderflow_2026-05-12.jsonl`
- **Size:** 54 MB
- **Total events:** 179,769 lines
- **Last timestamp:** 2026-05-12T18:30:12.124Z
- **Age:** ~45 hours old (from 2 days ago)

### Schema Verified ✅
- **Price field:** `"price"` ← Correct
- **Side field:** `"side"` ← Correct (bid/ask)
- **Size field:** `"size"` ← Correct
- **Symbol:** `"NQM6.CME@RITHMIC"` ← Correct
- **Level:** `null` (top-of-book) ← Correct
- **No scaling factor found in data**

### Price Extraction ✅
Parsing logic is correct:
```json
{
  "price": 28336.0,
  "side": "bid",
  "size": 1
}
```
→ Extracted correctly as bid=28336.0

---

## Why Prices Don't Match

### Hypothesis 1: Data is Stale (MOST LIKELY)
- File is from 2026-05-12 (yesterday)
- Last event: 2026-05-12T18:30:12 UTC (6:30 PM UTC / 11:30 AM PDT)
- No live data from 2026-05-14
- **NQ probably moved 1400 points overnight**

### Hypothesis 2: Different Market Session
- File contains close/after-hours data from May 12
- Your visual is from May 14 session
- Prices naturally different

### Hypothesis 3: Wrong Data Source
- Confirmed file contains NQM6 symbol
- Confirmed top-of-book format (level: null)
- Confirmed prices tick-aligned
- **NOT a parsing error**

---

## What This Means

| Issue | Status |
|-------|--------|
| Price field extracted correctly | ✅ |
| Side field (bid/ask) correct | ✅ |
| Size field correct | ✅ |
| Tick alignment correct | ✅ |
| Schema parsing | ✅ |
| **Data is live/current** | ❌ FAIL |
| **Data matches current Bookmap** | ❌ FAIL |

---

## Why Alerts Won't Work

1. **No live data** — File hasn't been updated since May 12
2. **Stale timestamps** — Events are 45+ hours old
3. **Prices are wrong** — 1400 points below current market
4. **Tailing EOF returns nothing** — No new events appending

**Result:** Alerts can't fire because there's nothing new to alert on.

---

## What Needs to Happen

### Immediate
1. ❌ **Stop alert development** — Can't validate against Bookmap
2. ✅ **Wait for live data** — Need 2026-05-14 orderflow file
3. ✅ **Confirm data source** — Verify file location is correct

### When Live Data Arrives
- New file: `es_orderflow_2026-05-14.jsonl`
- Should have timestamps from today
- Prices should match Bookmap visual within <1 second

### Validation Plan
1. Run tailer + probe on new file
2. Confirm event age < 5 seconds
3. Confirm prices match Bookmap (within 5 ticks)
4. Then resume alert development

---

## File Status

```
/Users/laxman_2026_mac_mini/.openclaw/workspace/state/orderflow/bookmap_api/

├── es_orderflow_2026-05-06.jsonl  (9.7GB, May 6 - VERY STALE)
├── es_orderflow_2026-05-12.jsonl  (54MB, May 12 18:30 UTC - STALE)
└── es_orderflow_2026-05-14.jsonl  (⏳ WAITING - NEEDED FOR LIVE)
```

**Waiting for:** Live 2026-05-14 data

---

## Proof of Correct Parsing

Raw samples from file EOF:

```json
BID Event:
{
  "price": 28336.0,
  "side": "bid",
  "size": 1,
  "ts_recv": "2026-05-12T18:30:12.124Z",
  "symbol": "NQM6.CME@RITHMIC",
  "level": null
}

ASK Event:
{
  "price": 28412.0,
  "side": "ask",
  "size": 0,
  "ts_recv": "2026-05-12T18:30:12.124Z",
  "symbol": "NQM6.CME@RITHMIC",
  "level": null
}
```

Parsing: ✅ **Correct**  
Schema: ✅ **Correct**  
Data currency: ❌ **Wrong (too old)**

---

## Next Steps

**DO NOT:**
- Generate alerts (data too stale)
- Claim validation (can't validate against current Bookmap)
- Deploy alerts (prices wrong)

**DO:**
- Wait for live 2026-05-14 data
- Re-run audit when new file appears
- Verify prices match Bookmap first
- Then resume alert development

---

## Verdict: **DATA_SOURCE_STALE**

The OpenClaw code is working correctly. The problem is the input data is 45 hours old. When live data arrives, resume validation.
