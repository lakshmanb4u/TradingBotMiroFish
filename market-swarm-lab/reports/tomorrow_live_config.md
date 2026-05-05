# Tomorrow's Live Alert Engine Configuration

**Date:** 2026-05-05 (May 5)  
**Purpose:** Observational validation of approval gate  
**Mode:** Manual alerts only (no auto-execution)

---

## Quick Start Tomorrow Morning

```bash
# 1. Start the live engine (before market open)
python3 scripts/run_live_orderflow_alerts_v5.py \
  --symbol ESM6.CME@RITHMIC \
  --alert-engine discretionary \
  --gate-mode follow-through-confirmed \
  --output-dir state/orderflow/live/ \
  --whatsapp-alerts \
  --csv-flush-interval 5s

# 2. Monitor alerts
tail -f state/orderflow/live/live_alerts.csv

# 3. Check heartbeat every 30s
cat state/orderflow/live/heartbeat.json
```

---

## Alert Conditions (ALL Must Pass)

### Gate 1: Regime Filter ✅

**SKIP if:**
- Balance/consolidation (multiple false breakouts)
- Low ATR chop (dead time, commission-heavy)
- Late session dead tape (< 30 min to close, thin liquidity)

**ACCEPT if:**
- Opening drive (first 2 hours)
- Trend expansion (delta expansion, lower lows/highs)
- Structural breaks (new level, fresh aggression)

### Gate 2: Absorption Detection ✅

Requires:
- Large order cluster at key level
- Price fails to continue past level
- Liquidity pull evident
- Delta shows absorption (buy/sell imbalance)

### Gate 3: Reclaim/Reject ✅

Requires:
- Price returns to absorption level or breaks through
- Delta acceleration detected
- Liquidity stack begins to release

### Gate 4: Follow-Through Confirmation ✅ **REQUIRED**

**This is the critical gate. Trade only if ALL:**

1. **Displacement:** 2.0+ ticks past initial adverse point
2. **Delta acceleration:** Increasing buy/sell pressure
3. **Range expansion:** New local highs/lows
4. **Structure break:** Previous resistance/support violated
5. **Continued aggression:** No pullback into initial level

---

## Alert Fields

Every alert includes:

```json
{
  "timestamp_utc": "2026-05-05T13:42:11Z",
  "timestamp_et": "09:42:11",
  "symbol": "ESM6",
  "direction": "LONG",
  
  "entry_price": 7234.25,
  "stop_price": 7231.75,
  "target1_price": 7238.25,
  "target2_price": 7242.25,
  
  "confidence": 82.0,
  "displacement_ticks": 3.25,
  "delta_acceleration": "strong",
  
  "regime": "trend",
  "reason_codes": ["absorption", "reclaim", "delta_accel", "breakout"],
  "follow_through_quality": "strong",
  
  "absorption_price": 7232.0,
  "reclaim_price": 7233.5
}
```

---

## Entry Decision Framework

### TIER 1: Take (High Confidence)

**Conditions:**
- Confidence ≥ 80%
- Displacement ≥ 3.0 ticks
- Delta acceleration: strong
- Follow-through quality: strong

**Action:** Consider entry (manual execution)

### TIER 2: Review (Medium Confidence)

**Conditions:**
- Confidence 60-79%
- Displacement 2.0-2.99 ticks
- Delta acceleration: moderate
- Follow-through quality: moderate

**Action:** Wait for confirmation or skip

### TIER 3: Skip (Low Confidence)

**Conditions:**
- Confidence < 60%
- Displacement < 2.0 ticks
- Delta acceleration: weak
- Follow-through quality: weak

**Action:** Pass (not worth the risk)

---

## Regime Detection

### Opening Drive (First 2 Hours)

- Volatility: Expanding
- Direction: Usually trending one way
- Quality: High (fresh participants)
- Alert frequency: Medium (10-20/hour)

### Trend Expansion (Morning/Afternoon)

- Volatility: Sustained
- Direction: Lower lows/higher highs
- Quality: Good (structural breaks)
- Alert frequency: Medium (8-15/hour)

### Balance/Consolidation

- Volatility: Contracting
- Direction: Sideways, multiple reversals
- Quality: Poor (false breakouts)
- Alert frequency: High but low quality (20-40/hour, 80% false)

### Dead Tape (Late Session)

- Volatility: Dying
- Direction: Random
- Quality: Very poor
- Alert frequency: Very high false positives

---

## Output Files

### Live Alert Log
```
state/orderflow/live/live_alerts.csv

Columns:
timestamp_utc, timestamp_et, symbol, direction, entry_price, stop_price,
target1_price, target2_price, confidence, displacement_ticks,
delta_acceleration, regime, reason_codes, follow_through_quality, alert_id
```

### Latest Signal
```
state/orderflow/live/latest_signal.json

Contains:
- alert (full data)
- message (formatted for WhatsApp)
- timestamp
```

### Heartbeat
```
state/orderflow/live/heartbeat.json

Updated every 30 seconds:
- timestamp
- alerts_logged
- status (monitoring/stopped/error)
```

---

## Constraints & Safety

✅ **Bounded memory:** No replay jobs > 100MB

✅ **CSV flushing:** Every 5 seconds (no data loss)

✅ **Heartbeat:** Every 30 seconds (health check)

✅ **Alert dedup:** No duplicate alerts within 60 seconds

✅ **No LLM calls:** Inside signal loop only use:
- Delta calculations
- Price comparisons
- Regime classification

✅ **No broker connection:** Alerts only, no execution

✅ **No auto-trading:** Manual review required for every trade

---

## Tomorrow's Workflow

### Pre-Market (8:30 AM)

1. Start engine with `--whatsapp-alerts`
2. Verify heartbeat file updates
3. Check that `live_alerts.csv` is empty
4. Monitor `latest_signal.json` for test alerts

### During Market (9:30 AM - 4:00 PM)

1. Receive WhatsApp alerts when conditions met
2. Each alert includes: entry, stop, targets, confidence
3. Manual decision: take or skip
4. If taken: execute manually in broker, track in spreadsheet
5. Log result: win/loss/timeout

### Post-Market (4:00 PM - close)

1. Stop engine
2. Export `live_alerts.csv` to research folder
3. Analyze: hit rate, accuracy, regime type
4. Update tomorrow's config based on results

---

## Command To Run Tomorrow

```bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab

python3 scripts/run_live_orderflow_alerts_v5.py \
  --symbol ESM6.CME@RITHMIC \
  --alert-engine discretionary \
  --gate-mode follow-through-confirmed \
  --output-dir state/orderflow/live/ \
  --whatsapp-alerts \
  --whatsapp-number +15515747457 \
  --csv-flush-interval 5s \
  --heartbeat-interval 30s \
  --alert-dedup-window 60s \
  --regime-filter "not balance, not chop, not dead_tape" \
  --min-confidence 70 \
  --require-follow-through true
```

---

## Success Criteria

### Day 1 Validation (Tomorrow)

**Collect data on:**
- How many alerts fired
- How many had follow-through confirmed
- Of those taken: win rate, avg R
- Of those skipped: would they have won/lost?
- Any false positives?

**Expected:**
- 10-30 alerts during trend/opening drive
- 70-80% pass rate (follow-through confirmed)
- Of passed: 60%+ win rate (based on Experiments #1-#2)

### Success = "OBSERVATIONAL_ALERTS_READY"

If:
- Engine doesn't crash
- Alerts log to CSV correctly
- HeartbeatJSON updates every 30s
- WhatsApp messages send on conditions

Then: Tomorrow can run live with manual observation

---

## Not Ready Yet

❌ Do NOT connect broker (no execution)  
❌ Do NOT auto-trade (gate not validated on live)  
❌ Do NOT run without manual review  
❌ Do NOT skip regime filter

---

## Regression Test: Before Running

```bash
# Test that alert engine works
python3 services/live_trading/discretionary_alert_engine.py

# Output should show:
# [✓] Alert created
# [✓] Saved to live_alerts.csv
```

If passes: Engine ready to run tomorrow.

---

## File Structure Ready

```
services/live_trading/
├── discretionary_alert_engine.py ✅ Built
└── live_alert_formatter.py (TODO if needed)

state/orderflow/live/
├── live_alerts.csv (created at runtime)
├── latest_signal.json (created at runtime)
└── heartbeat.json (created at runtime)

scripts/
└── run_live_orderflow_alerts_v5.py (TODO: needs discretionary mode)
```

---

## Summary

✅ **Alert engine built**  
✅ **Conditions specified**  
✅ **Output format defined**  
✅ **Safety constraints set**  
⏳ **Live script needs discretionary mode**  
⏳ **WhatsApp integration needs gate support**  

**Status: ALMOST READY**

For final "OBSERVATIONAL_ALERTS_READY" need:
1. Connect discretionary_alert_engine to live signal processor
2. Add WhatsApp formatter
3. Test one full run before market open
