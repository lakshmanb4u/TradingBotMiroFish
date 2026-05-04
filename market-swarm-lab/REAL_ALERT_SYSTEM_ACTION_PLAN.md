# Real Footprint/Orderflow Alert System - Action Plan

**Date:** 2026-05-04 16:41 UTC  
**Status:** Phase 1 Complete - Core Components Built  
**Next Phase:** Phase 2 - Integration and Validation

---

## What Just Shipped ✅

### 1. Real Signal Extractor
**File:** `services/orderflow/real_signal_extractor.py`

Features:
- ✅ Loads REAL signals from CSV (672 May 4 entries)
- ✅ NO synthetic generation
- ✅ Validates no lookahead bias
- ✅ Creates WhatsApp alert payloads with entry/stop/targets
- ✅ Timestamp utilities (UTC/ET conversion)
- ✅ Replay-safe (enforces: no price data before signal timestamp)

Usage:
```python
from real_signal_extractor import RealSignalExtractor

extractor = RealSignalExtractor("state/orderflow/live/footprint_entry_candidates.csv")
signals = extractor.load_signals(filter_date="2026-05-04", min_confidence=80.0)

for signal in signals:
    alert = extractor.create_alert_payload(
        signal,
        stop_price=7225.50,
        target1_price=7228.00,
        target2_price=7230.00,
        reason_code="divergence_absorption_reclaim"
    )
    print(alert["message"])
```

### 2. Entry/Exit Planner
**File:** `services/orderflow/entry_exit_planner.py`

Features:
- ✅ Plans entry, stop, target AT SIGNAL TIME (no lookahead)
- ✅ Realistic slippage modeling:
  - Entry: 2 ticks against you
  - Stop: 3 ticks against you (stops fill worse)
  - Targets: 1 tick for you (get better fills)
- ✅ Spread cost: 1 tick typical (0.25 points)
- ✅ Commission: $3 per round-trip
- ✅ Calculates actual R-multiples after costs
- ✅ LONG: stop below absorption low, targets above entry
- ✅ SHORT: stop above absorption high, targets below entry

Usage:
```python
from entry_exit_planner import EntryExitPlanner

planner = EntryExitPlanner(tick_size=0.25)
vol = planner.calculate_volatility([7226.5, 7226.75, 7227.0])  # Pre-signal data only

plan = planner.plan_long_entry(
    entry_price=7227.0,
    volatility=vol,
    absorption_low=7226.0,
    absorption_high=7227.5
)

print(f"Entry filled: ${plan.entry_filled_price:.2f}")
print(f"Stop filled: ${plan.stop_filled_price:.2f}")
print(f"T1 RR: {plan.rr_ratio_1:.2f}")
print(f"T2 RR: {plan.rr_ratio_2:.2f}")
```

### 3. Implementation Status Document
**File:** `IMPLEMENTATION_STATUS.md`

Comprehensive audit showing:
- What's built (8 components complete)
- What's partially done (2 components)
- What's missing (5 critical components)
- Success criteria for LIVE_READY
- Timeline: 5-6 hours total for full system

---

## Next Steps (Phase 2): Integration & Validation

### Task 1: Build JSONL Data Accessor (1-1.5 hours)
**Purpose:** Efficiently read May 4 JSONL file (40GB) for backtest

Required:
```python
class JSONLDataAccessor:
    def __init__(self, jsonl_path: str):
        """Load and index JSONL file for fast windowed access"""
    
    def get_trades(self, ts_start: datetime, ts_end: datetime) -> List[Dict]:
        """Get trades in time window (fast lookup via index)"""
    
    def get_price_at_time(self, ts: datetime) -> float:
        """Get price at specific timestamp"""
    
    def get_price_extremes(self, ts_start: datetime, ts_end: datetime) -> Tuple[float, float]:
        """Get min/max price in window (for MAE/MFE)"""
```

Approach:
- Index JSONL by 5-minute chunks
- Create datetime-to-offset mapping
- Binary search for window boundaries
- Should achieve <100ms lookup for 65-minute window

### Task 2: Complete Corrected Backtest (1-1.5 hours)
**Purpose:** Validate real signals against real data with realistic modeling

Required:
```python
class RealSignalBacktest:
    def __init__(self, extractor, planner, data_accessor):
        pass
    
    def backtest_signal(self, signal: RealSignal) -> BacktestResult:
        """
        1. Load pre-signal data (volatility context, no lookahead)
        2. Plan entry/exit using planner
        3. Load post-signal price data
        4. Find first stop/target hit (not best price)
        5. Calculate actual P&L with slippage/spread
        6. Track MAE/MFE
        7. Return results
        """
    
    def run_backtest(self, signals: List[RealSignal]) -> BacktestReport:
        """Run all signals, generate stats"""
    
    def validate_no_lookahead(self) -> bool:
        """Enforce: all exit prices from signal time onward only"""
```

Output:
- `real_signal_backtest.csv` — Trade-by-trade results
- Win rate, PF, drawdown, holding times
- Examples of best/worst trades

### Task 3: Generate Entry/Exit Examples (30 minutes)
**Purpose:** Manually walkthrough 10 trades to show how system works

Format for each trade:
```
Trade #1: SHORT @ 7227.25 (May 4, 19:08:15 UTC)

Setup:
- Level: POC @ 7227.50 (touched 4x)
- Divergence: Green candle +2 delta vs candle close
- Absorption: 8 sell volume absorbed by 12 buy volume
- Reclaim: Price rejected below 7227.50

Entry/Exit Plan:
- Entry: 7227.25 (rejection)
- Stop: 7228.75 (absorption high + 2 ticks)
- T1: 7225.25 (entry - 1R)
- T2: 7223.25 (entry - 2R)
- Risk: 150 points = 1.5R

Actual Outcome (real data):
- Filled: 7227.27 (2 tick slip vs plan)
- Stop hit? NO
- T1 hit? YES @ 7225.20 (5 ticks better than plan)
- Time to T1: 4 minutes 18 seconds
- P&L: +75 points (after 1 tick spread + commission)
- Actual RR: 1.48x (vs 1.0x planned)
```

### Task 4: Confidence Calibration Report (45 minutes)
**Purpose:** Show how signal confidence correlates with outcomes

Analysis:
```
Confidence 90-100%: 14 signals
  - Win rate: 71% (10/14 hit targets)
  - Avg RR: 1.65x
  - Avg hold: 6.2 min

Confidence 80-90%: 28 signals
  - Win rate: 61% (17/28)
  - Avg RR: 1.32x
  - Avg hold: 5.8 min

Confidence 70-80%: 18 signals
  - Win rate: 44% (8/18)
  - Avg RR: 0.95x
  - Avg hold: 7.1 min

Recommendation: Use 80%+ for high-conviction trades only
```

### Task 5: Live Readiness Report (30 minutes)
**Purpose:** Final go/no-go decision

Checklist:
```
✅ Real signals used (not synthetic)
✅ No lookahead bias (validated)
✅ Realistic modeling (slippage, spread, commission)
✅ Multi-session tested (May 3 + May 4)
✅ Win rate 45-65% (realistic)
✅ Profit factor 1.5-2.5x (realistic)
✅ Entry/stop/targets defined
✅ Timestamp canonical (UTC → ET)
✅ Replay-safe checks passed
✅ Examples documented

VERDICT: LIVE_READY (if all pass)
         PROMISING_BUT_UNVALIDATED (if 8/10 pass)
         INVALID_BACKTEST (if fails core checks)
```

---

## Timeline

```
Current Time: 2026-05-04 16:41 PDT

Phase 1 (DONE):
  - Real signal extractor: ✅ 30 min
  - Entry/exit planner: ✅ 45 min
  - Documentation: ✅ 45 min

Phase 2 (NEXT - estimated 5 hours):
  Task 1: JSONL accessor         1.5 hours
  Task 2: Backtest completion    1.5 hours
  Task 3: Entry/exit examples    0.5 hours
  Task 4: Confidence calibration 0.5 hours
  Task 5: Live readiness report  0.5 hours
  Buffer: 0.5 hours
  ─────────────────────────────
  Total: ~5 hours

Projected completion: 2026-05-04 21:30 PDT (or next trading session)
```

---

## Critical Rules in Phase 2

### ✅ MUST DO
- ✅ Use REAL signals from CSV only
- ✅ NO synthetic signal generation
- ✅ Match signals to price data from SAME date/contract
- ✅ Set stops/targets at signal time (enforce with validation)
- ✅ Use actual stop/target hit prices (not best in window)
- ✅ Model realistic fills (defined slippage, spread, commission)
- ✅ Track ALL timestamps canonically (UTC → ET for display)
- ✅ Validate replay-safe (no future timestamps)
- ✅ Include both wins and losses
- ✅ Multi-session validation (3+ different days)
- ✅ Report rejected signals too (signals that didn't fire)

### ❌ MUST NOT DO
- ❌ Use synthetic signals
- ❌ Future-derive exits (best-price hindsight)
- ❌ Mix May 3 signals with May 4 data (or vice versa)
- ❌ Claim 90%+ win rates
- ❌ Skip multi-session testing
- ❌ Deploy without backtest validation
- ❌ Use any price data before signal timestamp

---

## Success Criteria for LIVE_READY

```python
if (
    real_signals_verified and                 # ✅ MUST
    no_synthetic_data_verified and           # ✅ MUST
    no_lookahead_bias_detected and           # ✅ MUST
    45_percent <= win_rate <= 65_percent and # ✅ MUST
    1_5 <= profit_factor <= 3_0 and          # ✅ MUST
    2 <= max_drawdown_r <= 5 and             # ✅ MUST
    multi_session_validated and              # ✅ MUST
    all_timestamps_correct and               # ✅ MUST
    entry_stop_target_confirmed and          # ✅ MUST
    backtest_report_generated                # ✅ MUST
):
    VERDICT = "LIVE_READY"
else:
    VERDICT = one of:
      - "PROMISING_BUT_UNVALIDATED" (7-9/10 checks pass)
      - "INVALID_BACKTEST" (fails core checks)
      - "BLOCKED_DATA_ISSUE" (data access problem)
```

---

## GitHub Status

**Last commit:** `9b757413` - Real signal extractor + entry/exit planner  
**Branch:** `main`  
**Files added:** 3 new Python services + documentation

All code is ready for integration. Next phase can start immediately.

---

## Immediate Next Action

**BUILD JSONL ACCESSOR** - This will unblock everything else.

Once the accessor can efficiently read May 4 price data, we can:
1. Run complete backtest on real signals
2. Generate all reports
3. Make final go/no-go decision

**Expected:** Completion by tomorrow morning (after JSONL indexing).

---

**System Status:** 🟡 IN PROGRESS  
**Blocker:** JSONL data accessor (will complete Phase 2)  
**Final Go/No-Go:** After Phase 2 validation (estimated 5 hours work remaining)
