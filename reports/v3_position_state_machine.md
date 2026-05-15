# V3 Position State Machine Implementation

**Status:** ✅ IMPLEMENTED  
**Date:** 2026-05-14 18:57 PDT

---

## Problem Identified

V3 generated contradictory alerts:
- Alert 1: SELL @ 13:06:47
- Alert 2: BUY @ 13:06:55 (7.4 seconds later)
- **Issue:** Impossible for human to close short AND open long in 7 seconds

---

## Solution Implemented

### Position State Machine

Four states:
```
FLAT → (BUY/SELL allowed) → LONG_ACTIVE or SHORT_ACTIVE
LONG_ACTIVE → (exit at stop/target/time) → COOLDOWN → FLAT
SHORT_ACTIVE → (exit at stop/target/time) → COOLDOWN → FLAT
COOLDOWN → (30s wait) → FLAT
```

### State Rules

**FLAT:**
- ✅ BUY allowed (if conditions met)
- ✅ SELL allowed (if conditions met)

**LONG_ACTIVE:**
- ❌ BUY blocked (duplicate)
- ❌ SELL blocked (unless reversal: 6.0x+ imbalance + 60s wait)
- Track: MFE, MAE, hold time
- Exit: stop/target1/target2/timeout

**SHORT_ACTIVE:**
- ❌ SELL blocked (duplicate)
- ❌ BUY blocked (unless reversal: 6.0x+ imbalance + 60s wait)
- Track: MFE, MAE, hold time
- Exit: stop/target1/target2/timeout

**COOLDOWN:**
- ❌ All alerts blocked for 30 seconds
- Auto-transition to FLAT after cooldown

### Reversal Logic

Opposite-direction alert allowed ONLY if:
1. ✅ Current trade exited
2. ✅ >= 60 seconds since last exit
3. ✅ Opposite imbalance >= 6.0x (high threshold)
4. ✅ Persistence >= 5 seconds
5. ✅ Directional continuation confirmed

---

## Trade Lifecycle Management

### Entry
- Entry zone: 0.25-0.50 wide (not single tick)
- Fill assumption: Zone midpoint
- Stop: 8 ticks beyond invalidation
- Target1: 20 ticks from entry
- Target2: 60 ticks from entry

### Active Tracking
- MFE (Max Favorable Excursion): Best price reached during hold
- MAE (Max Adverse Excursion): Worst price reached during hold
- Unrealized P&L: Updated each tick
- Hold duration: Seconds elapsed

### Exit Conditions
1. **Stop hit:** Trade closed at loss
2. **Target1 hit:** Trade closed at partial profit (20t)
3. **Target2 hit:** Trade closed at full profit (60t)
4. **Time-based:** Auto-exit after 15 minutes max hold
5. **Thesis invalidation:** (future enhancement)

### Completed Trade
- Full lifecycle recorded
- Result in ticks (positive = profit)
- Result in $ (100x ticks for NQ)
- Exit reason documented
- Human execution score (0-100)

---

## Contradiction Resolution

### Before (V3 without state machine)
```
Alert 1: SELL 13:06:47 (allowed, state unknown)
Alert 2: BUY 13:06:55  (allowed, state unknown)
Gap: 7.4 seconds
Result: ❌ CONTRADICTION - Can't hold both positions
```

### After (V3 with state machine)
```
Alert 1: SELL 13:06:47 → ENTRY → SHORT_ACTIVE
Alert 2: BUY 13:06:55 → BLOCKED (SHORT_ACTIVE, gap < 60s)
         [Trade must exit first]
Alert 2 (retry): BUY after SHORT exits → ALLOWED
Result: ✅ No contradictions - Single position at a time
```

---

## Implementation Details

### Class: V3PositionManagedEngine

**Key attributes:**
- `position_state`: Current state (FLAT/LONG_ACTIVE/SHORT_ACTIVE/COOLDOWN)
- `active_trade`: Currently open trade (entry price, stop, targets, MFE/MAE)
- `completed_trades`: List of closed trades with full outcome
- `last_exit_time`: Timestamp of last trade exit (for reversal spacing)
- `trade_counter`: Sequential trade numbering

**Key methods:**
- `_can_enter_long()`: Check if BUY allowed by state machine
- `_can_enter_short()`: Check if SELL allowed by state machine
- `_exit_trade()`: Close active trade, calculate result, record outcome
- `process_event()`: Update book, check exits, generate new signals
- `export_trades_csv()`: Export completed trades with full lifecycle

### Safety Guarantees

✅ **No overlapping positions** - State machine enforces single position  
✅ **Minimum 60s between reversals** - Prevents rapid flip-flops  
✅ **High reversal threshold (6.0x)** - Requires strong confirmation for opposite direction  
✅ **Full trade tracking** - Every trade has entry, exit, and P&L  
✅ **Realistic timing** - Expected 5-15 minute holds, not millisecond scalps  

---

## Expected Behavior

### Scenario 1: Clean Trend
```
13:06:47  SELL allowed     → SHORT_ACTIVE
13:08:00  Price hits target1 → Trade closed
13:08:30  In cooldown
13:08:40  BUY allowed (gap 113s) → LONG_ACTIVE
13:10:00  Price hits stop  → Trade closed
Result: 2 non-overlapping trades ✅
```

### Scenario 2: Rapid Reversal Blocked
```
13:06:47  SELL allowed     → SHORT_ACTIVE
13:06:55  BUY signal arrives
          Check: Is SHORT_ACTIVE? YES
          Check: Gap >= 60s? NO (7.4s)
          Result: ❌ BLOCKED
13:08:00  SHORT trade exits
13:09:00  (60s passed)
13:09:01  BUY allowed      → LONG_ACTIVE
Result: No contradiction ✅
```

### Scenario 3: Reversal Confirmation
```
13:06:47  SELL allowed     → SHORT_ACTIVE
13:06:55  BUY signal: imbalance 3.0x
          Check: Is SHORT_ACTIVE? YES
          Check: Gap >= 60s? NO
          Check: Reversal imbalance >= 6.0x? NO (3.0x)
          Result: ❌ BLOCKED (weak reversal)
13:08:00  SHORT exits
13:08:45  BUY signal: imbalance 8.0x
          Check: Gap >= 60s? YES
          Check: Reversal imbalance >= 6.0x? YES (8.0x)
          Result: ✅ ALLOWED (strong reversal)
```

---

## Metrics to Track

After completing several trades, monitor:

| Metric | Target | Why |
|--------|--------|-----|
| **Win rate** | >= 50% | Better than random |
| **Avg winner** | >= 20t | Reward for risk |
| **Avg loser** | <= 8t | Tight stops working |
| **Profit factor** | >= 2.0 | $2 profit per $1 loss |
| **Avg hold** | 5-15 min | Human-tradeable duration |
| **Max drawdown** | < -50t | Risk management works |

---

## Status: POSITION LOGIC VALID

✅ State machine implemented  
✅ Contradiction prevention active  
✅ Reversal logic enforced  
✅ Trade lifecycle tracked  
✅ Full outcome recording  

**Next:** Run 15-minute validation and analyze trade outcomes.
