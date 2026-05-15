# V4 False Positive Analysis

**Status:** FRAMEWORK READY, AUDIT PENDING  
**Date:** 2026-05-14 19:37 PDT

---

## Goal

Identify where V4 structure detection is **wrong**, not just different.

False positives:
- Liquidity shelves that aren't real support
- HVN detections that are just noise
- Swing points that are meaningless
- Trapped trader levels that don't exist
- Absorption zones that didn't actually occur

---

## Potential False Positive Categories

### Category 1: Fake Liquidity Shelves

**Detection method:** 3 consecutive bid/ask levels with >100 contracts

**Weakness:** 
- 100 contracts might be routine, not meaningful
- Could be one trader's order, not market structure
- Might disappear within 100ms

**Audit required:**
```
For each detected shelf:
1. How long did it persist?
2. How many contracts actually filled there?
3. Did price actually stop at this level?
4. Or did it trade through without hesitation?
```

**Null hypothesis:** Shelf is real if:
- Persisted >2 seconds
- Filled >50 contracts
- Price rebounded from this level (didn't trade through)

**Example Alert 1:**
- Claimed: Bid cluster at 29710 (450 contracts)
- Test: 
  - [ ] Did bid ladder show 450 total at 29710?
  - [ ] Or was it 100+80+120 spread across 3 nearby levels?
  - [ ] Status: PENDING VERIFICATION

---

### Category 2: Noise HVN/LVN Detection

**Detection method:** Volume profile top-5 prices

**Weakness:**
- Could be arbitrary clustering from uneven data
- Distribution might be flat (all prices traded equally)
- HVN could be outlier, not genuine support

**Audit required:**
```
For each detected HVN:
1. What % of total volume is at this level?
2. Is it >15% or just 5%?
3. How does it compare to adjacent levels?
4. Is there a clear "peak" or is distribution flat?
```

**Null hypothesis:** HVN is real if:
- >20% of total volume at this level
- >50% more volume than adjacent levels
- Visually distinguishable in volume profile

**Example Alert 2:**
- Claimed: HVN at 29730 (3900 volume)
- Test:
  - [ ] Total session volume? (Is 3900 > 20%?)
  - [ ] Next highest level volume?
  - [ ] Status: PENDING VERIFICATION

---

### Category 3: Meaningless Swing Points

**Detection method:** Local maxima/minima (2-bar confirmation on 5-min lookback)

**Weakness:**
- Could be noise spikes, not genuine structure
- 2-bar confirmation is weak (could be 10-second blip)
- Confuses tick-level noise with timeframe structure

**Audit required:**
```
For each swing point:
1. How much volume traded there?
2. How long did price hold at extreme?
3. Did it test this level again (real support)?
4. Or was it a 1-touch spike?
```

**Null hypothesis:** Swing is real if:
- >100 contracts traded at this price
- Price held for >5 seconds
- Price retested within 15 minutes

**Example Alert 3:**
- Claimed: Prior swing low at 29705 (tested 2 min ago)
- Test:
  - [ ] Did price actually reach 29705 before alert entry?
  - [ ] Or is this misaligned timestamp?
  - [ ] Was volume significant at that level?
  - [ ] Status: PENDING VERIFICATION

---

### Category 4: Speculative Trapped Trader Detection

**Detection method:** 
- Identify prior breakout price
- Assume traders entered at breakout
- Assume stops stacked 5 ticks beyond

**Weakness:**
- Complete speculation (no order book proof)
- Traders might not have entered at breakout
- Stops might not be where we assume
- Could be entirely wrong

**Audit required:**
```
For each trapped trader zone:
1. Did order book show actual stops at that level?
2. Or is this pure theory?
3. If 29735 was breakout, did we see stop orders placed there?
4. How many stops? (1000 or 10?)
```

**Null hypothesis:** Trapped traders are real if:
- Order book shows visible stops at predicted level
- Multiple orders clustered (not single order)
- Stops persist for >10 seconds

**Example Alert 2:**
- Claimed: Trapped shorts at 29740 (stops above 29735 breakout)
- Test:
  - [ ] Did we see actual stop orders in ladder at 29740?
  - [ ] How many? >500 or <50?
  - [ ] Status: PENDING VERIFICATION (HIGH UNCERTAINTY)

---

### Category 5: Absorption Zone False Positives

**Detection method:**
- Large volume + small price range = absorption
- Result: "Where buyers absorbed selling"

**Weakness:**
- Could just be stalled price (no particular support)
- Absorption might be one-time event, not repeatable
- Next time, price might trade through

**Audit required:**
```
For each absorption zone:
1. Volume absorbed: How much, exactly?
2. Price range: How small, exactly?
3. Did price return to this zone?
4. If yes, did buyers defend again?
5. If no, was first "absorption" just coincidence?
```

**Null hypothesis:** Absorption is real if:
- Volume >500 contracts
- Price range <3 ticks
- Price returned to zone AND bounced again

**Example Alert 3:**
- Claimed: Absorption at 29708 (100 contracts absorbed, 3-tick range)
- Test:
  - [ ] Did 100+ contracts actually trade between 29706-29710?
  - [ ] What was the exact volume and range?
  - [ ] Did price return to 29708 later?
  - [ ] If yes, did it bounce? (supporting the "buyers defended" narrative)
  - [ ] Status: CRITICAL VERIFICATION NEEDED

---

### Category 6: VWAP as Meaningful Support

**Detection method:** Calculate VWAP, treat as support/resistance

**Weakness:**
- VWAP is lagging (moves only as price moves)
- Might be meaningless (could align anywhere)
- Traders don't necessarily respect VWAP

**Audit required:**
- Not yet implemented in V4, but if added:
- Does price actually bounce from VWAP?
- Or does it trade through regularly?

---

## Structured False Positive Audit

### For Each Alert, Audit Each Target

```
ALERT 1, SELL @ 29714.88

V4 Conservative Target: 29710 (bid shelf)
  Question 1: Real shelf? (450 contracts at one level?)
    Status: PENDING
  Question 2: Did price reach 29710? (in next 15 min)
    Status: PENDING
  Question 3: If reached, did it bounce? (or trade through?)
    Status: PENDING
  Verdict: UNKNOWN (need data)

V4 Primary Target: 29705 (prior low + HVN)
  Question 1: Prior low real? (Did price swing to 29705 2 min before?)
    Status: PENDING
  Question 2: HVN real? (>20% of volume at 29705?)
    Status: PENDING
  Question 3: Did price reach 29705? (in next 15 min)
    Status: PENDING
  Verdict: UNKNOWN (need data)

V4 Runner Target: 29700 (session low)
  Question 1: Is 29700 actually session low? (or is it lower?)
    Status: NEEDS VERIFICATION
  Question 2: Did price reach 29700? (in next 15 min)
    Status: PENDING
  Verdict: UNKNOWN (need data)
```

Repeat for all 5 alerts × 3 targets each = 15 audit trails.

---

## Evidence Checklist Per Alert

### Alert 1: SELL @ 29714.88
- [ ] Bid cluster at 29710: verified from order book
- [ ] Prior low at 29705: verified from price history
- [ ] Session low at 29700: verified from session tracking
- [ ] Post-entry price reached 29710: verified from replay
- [ ] Post-entry price reached 29705: verified from replay
- [ ] Post-entry price reached 29700: verified from replay
- [ ] Structure evidence strong: YES/NO/UNCERTAIN
- [ ] Targets realistic given market: YES/NO/UNCERTAIN

### Alert 2: BUY @ 29719.38
- [ ] HVN at 29730: verified from volume profile
- [ ] Ask cluster at 29735: verified from order book
- [ ] Trapped shorts at 29740: verified from stop order visibility
- [ ] Post-entry price reached 29730: verified from replay
- [ ] Post-entry price reached 29735: verified from replay
- [ ] Post-entry price reached 29740: verified from replay
- [ ] Structure evidence strong: YES/NO/UNCERTAIN
- [ ] Targets realistic given market: YES/NO/UNCERTAIN

### Alert 3: SELL @ 29714.63
- [ ] Absorption at 29708: verified from volume/range
- [ ] Prior low at 29705: verified from price history
- [ ] LVN gap: verified from volume profile
- [ ] Post-entry price reached 29708: verified from replay
- [ ] Post-entry price reached 29705: verified from replay
- [ ] Post-entry price reached 29700: verified from replay
- [ ] Structure evidence strong: YES/NO/UNCERTAIN
- [ ] Targets realistic given market: YES/NO/UNCERTAIN

### Alert 4: BUY @ 29711.88
- [ ] HVN at 29720: verified from volume profile
- [ ] Ask cluster at 29720-29725: verified from order book
- [ ] Prior high at 29730: verified from price history
- [ ] Extended target at 29740: justified by 145s persistence? YES/NO/UNCERTAIN
- [ ] Post-entry price reached all targets: verified from replay
- [ ] Structure evidence strong: YES/NO/UNCERTAIN
- [ ] Targets realistic given market: YES/NO/UNCERTAIN

### Alert 5: SELL @ 29714.88
- [ ] Absorption at 29708: verified from volume/range
- [ ] Prior low at 29705: verified from price history
- [ ] HVN at 29705: verified from volume profile
- [ ] Session low at 29700: verified from session tracking
- [ ] Post-entry price reached all targets: verified from replay
- [ ] Structure evidence strong (triple-confirmation): YES/NO/UNCERTAIN
- [ ] Targets realistic given 245s conviction: YES/NO/UNCERTAIN

---

## Audit Status

**Current state:**
- Framework ready
- Checklist created
- Verification slots created

**Missing:**
- Actual data verification
- Post-entry replay results
- Visual inspection (Bookmap comparison)
- Hit/miss counts

**Next step:** Load post-entry data, verify each claim, mark YES/NO/UNCERTAIN.

---

## Preliminary Risk Assessment

### High Risk (Likely False Positives)
- **Trapped trader detection** (pure speculation)
- **Extended runner targets** (based on conviction scaling, unproven)

### Medium Risk (Could be False Positives)
- **Absorption zones** (only valid if price tests again and bounces)
- **Liquidity shelves** (could be routine order, not support)

### Lower Risk (More Likely Real)
- **Prior highs/lows** (based on verified price history)
- **HVN nodes** (based on volume profile, verifiable)

---

## Verdict Status: AUDIT PENDING

No false positives confirmed yet. Need:
1. Post-entry data
2. Visual inspection
3. Structure verification
4. Hit/miss measurements

Until then: ASSUME GUILTY (high uncertainty) until proven INNOCENT (data-backed).
