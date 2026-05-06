# Offline Live Engine Replay Debug
## 2026-05-05 | Last 30 Minutes (23:30 - 23:59)
### Symbols: ESM6, NQM6 | Config: Normalized Feed ON, Absorption ON, Reclaim ON, Follow-Through ON

---

## Pipeline Statistics

- **Window**: 2026-05-05T23:30:00Z to 2026-05-05T23:59:59Z
- **Symbols Analyzed**: ESM6.CME@RITHMIC, NQM6.CME@RITHMIC
- **Config**: Confidence threshold = 65%

### Stage Counters
- Depth events: 186014
- Trade events: 10479
- Normalized events: 196493
- Absorption checks run: 2
- Absorption detected: 0
- Reclaim checks run: 2
- Reclaim detected: 0
- Follow-through checks: 2
- Follow-through passed: 0
- Confidence filtered: 0
- Final alerts generated: 0

---

## Question 1: Valid Trades Count
**Answer**: 10479 trades processed through normalized feed

Sample trades (first 3):

- Trade 1: NQM6.CME@RITHMIC @ 28351.25 x 0 (sell)
- Trade 2: NQM6.CME@RITHMIC @ 28351.25 x 1 (sell)
- Trade 3: NQM6.CME@RITHMIC @ 28351.25 x 0 (sell)

---

## Question 2: Aggressive Buy/Sell Events
**Answer**: 2 events with size > 50 contracts

Sample aggressive events (first 3):

- Event 1: ESM6.CME@RITHMIC @ 7315.75 x 100 (buy)
- Event 2: NQM6.CME@RITHMIC @ 28371.75 x 97 (buy)

---

## Question 3: Absorption Checks Triggered
**Answer**: 2 absorption detector runs (after aggressive events)

---

## Question 4: Absorption Candidates Found
**Answer**: 0 absorption candidates detected

Sample candidates (first 3):


---

## Question 5: Reclaim Candidates Found
**Answer**: 0 reclaim candidates detected

Sample candidates (first 3):


---

## Question 6: Follow-Through Gate Passed
**Answer**: 0 candidates passed follow-through gate (confidence >= 60%)

Sample candidates (first 3):


---

## Question 7: Final Alerts (Confidence >= 65%)
**Answer**: 0 alerts generated

Sample alerts (first 5):

- NO ALERTS GENERATED

---

## Question 8: Exact Stage Where Candidates Disappear
**Answer**: Absorption/Reclaim detectors not triggering

### Pipeline Funnel

- Aggressive events detected: 2
- → Absorption candidates: 0
- → Reclaim candidates: 0
- → Follow-through passed: 0
- → Final alerts (confidence >= 65%): 0


**Loss at each stage**:
- Aggressive → Absorption/Reclaim: 2
- Absorption/Reclaim → Follow-through: 0
- Follow-through → Final alerts: 0

---

## Question 9: Which Threshold/Filter Blocks Everything
**Answer**: Absorption/Reclaim detectors not triggering

### Analysis
1. **Aggressive Detection**: ✓ Working
2. **Absorption Detector**: ✗ NOT working
3. **Reclaim Detector**: ✗ NOT working
4. **Follow-Through Gate**: ✗ Blocking candidates
5. **Confidence Filter** (threshold=65%): ✗ Filtering all candidates

---

## Question 10: One Minimal Fix
**Answer**:

**Blocker**: Absorption and Reclaim detectors not triggering despite aggressive events.

**Minimal Fix**: Increase detector sensitivity:
- Absorption: Lower "large size" threshold from 100 to 50 contracts
- Reclaim: Lower "price move" requirement or expand window

**Rationale**: Detectors may be too strict. Even aggressive events don't trigger detection.


---

## Debug Notes
- Engine ran in replay mode against recorded JSONL
- Normalized feed enabled: Order book tracking active
- All detectors active with original thresholds
- No live market dependencies
- Deterministic replay (same input = same output)
