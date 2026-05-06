# Pipeline Funnel Tracking - Quick Start

## What's New?

The live alert engine now tracks a **5-stage conversion funnel** showing how efficiently candidates progress through the signal pipeline. This identifies which gate is the bottleneck.

## Quick Commands

### Start instrumented engine + monitor

```bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
python3 scripts/live_alert_engine_instrumented.py &
```

### View funnel metrics (one-time)

```bash
python3 scripts/monitor_pipeline_funnel.py --once
```

### Live funnel monitor (continuous)

```bash
python3 scripts/monitor_pipeline_funnel.py
```

### Check raw metrics JSON

```bash
cat state/orderflow/live/pipeline_metrics.json | jq .
```

### View top-level summary

```bash
cat state/orderflow/live/pipeline_metrics.json | jq '.bottleneck_analysis'
```

## The Five Stages

```
📊 Raw Events (all JSONL events)
   ↓ 23% pass basic validation
📊 Reclaim Candidates (valid symbol, size, price, side)
   ↓ 43% pass regime filter
📊 Regime Passed (trending/mean_revert/compression conditions)
   ↓ 17% pass follow-through gate ← BOTTLENECK
📊 FollowThrough Passed (2+ bars confirmed)
   ↓ 50% pass confidence threshold
📊 Final Alerts (confidence ≥ 65%)
```

## What To Look For

| Metric | Means | Action |
|--------|-------|--------|
| Raw → Reclaim: 10-30% | Normal malformed event rate | Monitor feed health |
| Raw → Reclaim: <5% | Feed quality excellent | Good baseline |
| Reclaim → Regime: 30-50% | Healthy regime diversity | Expected |
| Regime → FollowThrough: <30% | Follow-through strict (typical) | OK unless too few alerts |
| Overall: <1% | Highly selective (good) | Continue monitoring |
| Overall: >5% | Too many alerts | May need tuning |

## Typical Output

```
🔥 PIPELINE CONVERSION FUNNEL ANALYSIS

📊 STAGE COUNTS
  Stage 1 - Raw Absorption:        3,847 events
  Stage 2 - Reclaim:                 892 events
  Stage 3 - Regime Passed:           376 events
  Stage 4 - FollowThrough Passed:     41 events
  Stage 5 - Final Alerts:             12 events

📊 CONVERSION RATES (% passing each gate)
  Raw → Reclaim:                   23.19%
  Reclaim → Regime:                42.16%
  Regime → FollowThrough:          10.90%  ← Most restrictive
  FollowThrough → Final:           29.27%
  Overall (Raw → Final):            0.31%

🔗 FUNNEL FLOW
  Raw Absorption: 3,847
  → Reclaim (23.2%): 892
  → Regime (42.2%): 376
  → FollowThrough (10.9%): 41
  → Final Alerts (29.3%): 12

🚨 BOTTLENECK ANALYSIS
  Most Restrictive Gate: Regime → FollowThrough
  Filtering Out:         89.10% of candidates
```

## Key Stats Explained

### raw_absorption_candidates
- **Definition**: All incoming JSONL events
- **What it means**: Raw event volume from Bookmap feed
- **Expected**: High (hundreds to thousands per 5 minutes)

### reclaim_candidates  
- **Definition**: Events passing basic validation (symbol, size, price, side)
- **What it means**: % of feed events are well-formed
- **Expected**: 20-30% of raw (typical: 70-80% malformed/noisy)

### regime_passed_candidates
- **Definition**: Events in trending, mean-reverting, or compression conditions
- **What it means**: Market conditions favorable for trading
- **Expected**: 30-50% of reclaim events

### followthrough_passed_candidates
- **Definition**: Events with 2+ bars confirmed in same direction, size >500
- **What it means**: Aggressive order flow with follow-through confirmation
- **Expected**: 10-30% of regime candidates (typical bottleneck)

### final_alerts
- **Definition**: Events with confidence ≥ 65%
- **What it means**: Signals ready for trading consideration
- **Expected**: 25-75% of followthrough candidates

## Files

| File | Purpose |
|------|---------|
| `scripts/live_alert_engine_instrumented.py` | Main engine with funnel tracking |
| `scripts/monitor_pipeline_funnel.py` | Funnel display tool |
| `scripts/start_instrumented_pipeline.sh` | Startup helper |
| `state/orderflow/live/pipeline_metrics.json` | Metrics (updated every 5 min) |
| `PIPELINE_INSTRUMENTATION.md` | Full documentation |
| `FUNNEL_QUICK_START.md` | This file |

## Do NOT

❌ Auto-trade based on these metrics  
❌ Loosen thresholds automatically  
❌ Modify code during live trading  
❌ Assume low conversion rate = bad (could be market condition)  

## DO

✅ Run for 30+ minutes to collect baseline data  
✅ Compare metrics across different market conditions  
✅ Look for patterns in bottleneck stages  
✅ Use insights to make informed tuning decisions  
✅ Keep this data for retrospective analysis  

## Example Analysis

**Scenario**: Regime → FollowThrough is filtering out 85%

**Possible causes**:
1. Follow-through bar minimum (2) is too high
2. Size filter (>500) too aggressive
3. Market is choppy (no sustained directional moves)

**Next steps**:
- Check market regime (trending? sideways?)
- Run backtest with lower follow-through minimum
- Analyze alerts generated when bar opens (fewer bars means earlier entry)
- Do NOT automatically lower threshold

---

**Last Updated**: 2026-05-05  
**Engine Version**: Instrumented with 5-stage funnel tracking
