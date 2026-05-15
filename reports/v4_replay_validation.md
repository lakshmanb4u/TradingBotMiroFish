# V4 Replay Validation — Empirical Results

**Status:** IN PROGRESS  
**Date:** 2026-05-14 19:37 PDT

---

## Validation Methodology

For each of the 5 V3/V4 alerts, we:

1. **Load entry time and price** from alert
2. **Replay next 15 minutes** of post-entry price data
3. **Track hits/misses** for conservative/primary/runner targets
4. **Measure MFE/MAE** (best/worst price reached)
5. **Calculate hold time** (seconds to exit)
6. **Audit structure evidence** (was the target justified?)
7. **Compare V3 vs V4** target accuracy

---

## Alert 1: SELL @ 13:06:47 PDT

### Entry
- **Price:** 29718.88
- **Direction:** SELL
- **Stop:** 29721.00 (8 ticks above)

### V3 Targets
- **T1:** 29713.88 (-20 ticks, arbitrary)
- **T2:** 29703.88 (-60 ticks, arbitrary)

### V4 Targets
- **Conservative:** 29710.00 (-9 ticks, bid shelf)
- **Primary:** 29705.00 (-14 ticks, prior low + HVN)
- **Runner:** 29700.00 (-19 ticks, session low)

### Post-Entry Replay (Next 15 min)
**Data loading in progress...** Awaiting post-entry price data from canonical source.

#### Preliminary Structure Audit
**Bid cluster at 29710 claim:**
- [ ] Did order book actually show 450+ contracts clustered at 29710?
- [ ] Or was this a noise detection false positive?
- [ ] Status: PENDING VERIFICATION

**Prior low at 29705 claim:**
- [ ] Did price actually swing high 120 seconds before entry?
- [ ] Did it touch 29705 exactly?
- [ ] Status: PENDING VERIFICATION

---

## Alert 2: BUY @ 13:06:55 PDT

### Entry
- **Price:** 29719.38
- **Direction:** BUY
- **Stop:** 29717.25 (8 ticks below)

### V3 Targets
- **T1:** 29724.38 (+20 ticks, arbitrary)
- **T2:** 29734.38 (+60 ticks, arbitrary)

### V4 Targets
- **Conservative:** 29730.00 (+11 ticks, HVN)
- **Primary:** 29735.00 (+16 ticks, prior high + ask shelf)
- **Runner:** 29740.00 (+21 ticks, trapped short stops)

### Post-Entry Replay (Next 15 min)
**Data loading in progress...**

#### Preliminary Structure Audit
**HVN at 29730 claim:**
- [ ] Was 29730 actually the highest volume node?
- [ ] Or is it volume profile overfitting?
- [ ] Status: PENDING VERIFICATION

**Trapped shorts at 29740 claim:**
- [ ] Where did shorts enter? At 29735?
- [ ] Are their stops really stacked at 29740?
- [ ] Or is this speculation?
- [ ] Status: PENDING VERIFICATION

---

## Alert 3: SELL @ 13:08:47 PDT (125s Persistence)

### Entry
- **Price:** 29714.63
- **Direction:** SELL
- **Stop:** 29716.75 (8 ticks above)
- **Context:** "absorption_after_rejection" (125s sustained ask dominance)

### V3 Targets
- **T1:** 29709.63 (-20 ticks, arbitrary)
- **T2:** 29699.63 (-60 ticks, arbitrary)
- **Context used:** NO (detected but ignored)

### V4 Targets
- **Conservative:** 29708.00 (-7 ticks, absorption zone)
- **Primary:** 29705.00 (-10 ticks, prior low + HVN)
- **Runner:** 29700.00 (-15 ticks, LVN gap)

### Post-Entry Replay (Next 15 min)
**Data loading in progress...**

#### Critical Structure Audit
**Absorption zone at 29708 claim:**
- [ ] Did 100+ contracts actually get absorbed at 29708 with <3 tick move?
- [ ] Or is this post-hoc curve-fitting?
- [ ] Status: PENDING VERIFICATION (CRITICAL)

**Low-volume node (LVN) at 29702-29703 claim:**
- [ ] Was there actually a volume gap here?
- [ ] Or did price trade through every tick?
- [ ] Status: PENDING VERIFICATION

---

## Alert 4: BUY @ 13:09:08 PDT (145.5s Persistence, 9.0x Imbalance)

### Entry
- **Price:** 29711.88
- **Direction:** BUY
- **Stop:** 29709.75 (9 ticks below)

### V3 Targets
- **T1:** 29716.88 (+20 ticks, arbitrary)
- **T2:** 29726.88 (+60 ticks, arbitrary)
- **Note:** 145s persistence but same templates as 5s alerts

### V4 Targets
- **Conservative:** 29720.00 (+8 ticks, HVN + ask cluster)
- **Primary:** 29730.00 (+18 ticks, prior high)
- **Runner:** 29740.00 (+28 ticks, extended structure)

### Post-Entry Replay (Next 15 min)
**Data loading in progress...**

#### Structure Audit
**Ask cluster at 29720-29725 claim:**
- [ ] Was there actually ask clustering here?
- [ ] How many contracts? At what price precision?
- [ ] Status: PENDING VERIFICATION

**Conviction scaling (145s → deeper targets):**
- [ ] Is runner target at 29740 justified by persistence?
- [ ] Or is this overconfidence?
- [ ] Status: PENDING VERIFICATION

---

## Alert 5: SELL @ 13:10:47 PDT (245s Persistence!)

### Entry
- **Price:** 29714.88
- **Direction:** SELL
- **Stop:** 29717.00 (8 ticks above)

### V3 Targets
- **T1:** 29709.88 (-20 ticks, arbitrary)
- **T2:** 29699.88 (-60 ticks, arbitrary)
- **Note:** LONGEST persistence (245s, 4+ minutes) but SAME targets as all other SELL alerts

### V4 Targets
- **Conservative:** 29708.00 (-7 ticks, absorption + prior tested)
- **Primary:** 29705.00 (-10 ticks, prior low + HVN triple-confirmation)
- **Runner:** 29700.00 (-15 ticks, session low)

### Post-Entry Replay (Next 15 min)
**Data loading in progress...**

#### Critical Structure Audit
**Triple-confirmation at 29705 claim:**
- [ ] Prior low? (YES - verified)
- [ ] HVN volume node? (NEED TO VERIFY)
- [ ] Absorption zone? (NEED TO VERIFY)
- [ ] Status: PARTIAL VERIFICATION

---

## Validation Status Summary

| Alert | Entry | V4 Conservative | V4 Primary | V4 Runner | Post-Data |
|-------|-------|---|---|---|---|
| 1 | ✓ | PENDING | PENDING | PENDING | ⏳ Loading |
| 2 | ✓ | PENDING | PENDING | PENDING | ⏳ Loading |
| 3 | ✓ | PENDING | PENDING | PENDING | ⏳ Loading |
| 4 | ✓ | PENDING | PENDING | PENDING | ⏳ Loading |
| 5 | ✓ | PENDING | PENDING | PENDING | ⏳ Loading |

---

## Expected Results (When Data Available)

### Hypothesis 1: Conservative Targets Hit Often
- **Claim:** Conservative hit rate 80-90%
- **Test:** Count hits across 5 alerts
- **Evidence needed:** Post-entry price reached conservative level

### Hypothesis 2: Primary Targets Hit 65-75%
- **Claim:** Primary targets are realistic
- **Test:** Measure hit/miss for each alert
- **Evidence needed:** Price behavior aligned with predicted structure

### Hypothesis 3: Structure Evidence is Real
- **Claim:** Detected shelves, HVNs, absorption zones are not false positives
- **Test:** Visual inspection of Bookmap/footprint at alert time
- **Evidence needed:** Human trader would see same structure

### Hypothesis 4: V4 Better than V3
- **Claim:** V4 targets are more accurate
- **Test:** Compare hit rates
- **Evidence needed:** V4 conservative/primary > V3 primary

---

## False Positive Audit (Preliminary)

### Potential Issues Identified

**Issue 1: Fake Liquidity Shelves**
- V4 detected bid clusters by looking for 3 consecutive levels >100 contracts
- This is a weak threshold
- Could be noise, not meaningful support
- **Test:** Do shelves persist? Or disappear immediately?

**Issue 2: HVN/LVN Overfitting**
- V4 detects HVN by volume profile peak
- Could be misleading if distribution is flat
- **Test:** Is HVN really a major concentration? (>20% of total volume?)

**Issue 3: Swing Point Noise**
- Prior highs/lows detected by local maxima (2-bar confirmation)
- Could be noise at small timeframes
- **Test:** Were those really significant levels?

**Issue 4: VWAP as Target**
- Not yet implemented, but conceptually: VWAP can be misleading
- Could assign target to random midpoint

---

## Empirical Ground Truth Needed

Before claiming V4 works, need:

1. **Post-entry price data** for all 5 alerts (next 15 min each)
2. **Visual inspection** of Bookmap at alert times
3. **Verification** of detected structure (did bid cluster really exist?)
4. **Hit/miss count** for each target level
5. **Hold time measurements** (how long until hit?)
6. **MFE/MAE calculations** (best/worst price)

---

## Verdict Status: AWAITING DATA

**Current:** Validation framework ready, structure audits written.

**Next:** Load post-entry data, measure ground truth, report real results (not estimates).

No claims until data speaks.
