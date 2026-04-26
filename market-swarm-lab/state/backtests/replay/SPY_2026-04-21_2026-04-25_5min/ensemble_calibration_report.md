# MiroFish Ensemble Calibration Report
**Ticker:** SPY | **Period:** Apr 21–25 2026 | **Timeframe:** 5-min | **Bars:** 312  
**Profile:** normal (min_votes=3, vol≥1.0x, cooldown=60m)  
_Generated: 2026-04-26_

---

## Executive Summary

The backtest produced **1 signal from 312 bars** — not because the data is bad, but because
**two agents are structurally broken in replay mode** and a third is nearly dead weight.
These are not threshold tuning issues. They are architecture defects exposed by the replay context.

| Root Cause | Bars Affected | Severity |
|---|---|---|
| VWAP+Futures has no ES/NQ data → permanently ≤+1 score → never votes bull | 312/312 | 🔴 Critical |
| Volume+Momentum votes neutral on 96.8% of bars (score=0 always) | 302/312 | 🔴 Critical |
| EMA+RSI and Trendline+Levels are anti-correlated outliers (agree 57%) | — | 🟡 Medium |
| Volume filter blocks 88.7% of 2-vote bars (vol_ratio < 1.0x) | 157/177 | 🟡 Medium |
| All 3 bars with 3/4 votes are the SAME bar logged 3x (duplicate logging bug) | — | 🟡 Medium |

**Bottom line:** With 2 of 4 agents broken, the theoretical ceiling is 2/4 votes on any bar
where EMA+RSI and Trendline+Levels happen to agree. 3/4 can only fire on extreme volume
bars (vol_ratio > 2x), which happen 6.4% of the time. There is no path to 4/4 without
live futures data.

---

## 1. Per-Agent Vote Rates

All agents had non-null scores on 240/312 bars (72 bars skipped — first day cold start,
insufficient history for indicators).

| Agent | BUY votes | SELL votes | Neutral/Hold | Mean Score | Threshold |
|---|---|---|---|---|---|
| VWAP+Futures | **0 (0%)** | 0 (0%) | 240 (100%) | +0.47 | ≥ +2 |
| EMA+RSI | **144 (60%)** | 76 (32%) | 20 (8%) | +0.60 | ≥ +2 |
| Trendline+Levels | **126 (52.5%)** | 70 (29%) | 44 (18%) | +0.46 | ≥ +2 |
| Volume+Momentum | **4 (1.7%)** | 0 (0%) | 236 (98%) | +0.06 | ≥ +2 |

### Key Observations

- **VWAP+Futures votes BUY exactly 0 times.** Without live ES/NQ futures (unavailable in replay),
  it scores +1 (price > VWAP) or -1 (price < VWAP). Threshold is +2. It is permanently frozen at neutral.
  Score +1 occurred 177x, score -1 occurred 63x. Never +2 or higher.

- **Volume+Momentum votes BUY only 4 times (1.7%).** It requires vol_ratio > 1.5x to score ≥+1,
  or vol_ratio > 2.0x for +2 (bull vote). In this data, only 6.4% of bars (20/312) reach 1.5x.
  230/240 bars score exactly 0 (no volume spike, no gap → zero score).

- **EMA+RSI is the engine.** It votes bull 60% of the time and is the primary signal driver.
  When EMA9 > EMA21 (which it is most of the Apr 21–25 BULL regime), score = +2 → guaranteed bull vote.

- **Trendline+Levels is a solid secondary.** Votes bull 52.5% of the time via trendline direction
  and morning-high breakouts. Agrees with EMA+RSI 57% of the time.

---

## 2. Pairwise Agreement Matrix

```
         VF    ER    TL    VM
VF       --     8%   18%   98%
ER        8%   --    57%    9%
TL       18%   57%   --    20%
VM       98%    9%   20%   --
```

### Clusters and Outliers

**VWAP+Futures ↔ Volume+Momentum: 98.3% agreement** — effectively the same signal.
Both agents produce scores of ±1 most of the time; both land in the neutral band.
They agree because they both almost always vote neutral. This is a **false correlation** —
two broken agents agreeing that they have no opinion is not useful consensus.

**EMA+RSI ↔ VWAP+Futures: 8.3% agreement** — near-opposite behavior.
VWAP+Futures is stuck at neutral; EMA+RSI swings between bull (60%) and bear (32%).
They only agree on the rare bars where EMA+RSI also lands neutral (8% of bars).

**EMA+RSI ↔ Trendline+Levels: 57% agreement** — the only real signal pair.
These two are the actual ensemble. When they both vote bull, you get 2/4 votes.
When one more joins, you get 3/4. With VWAP+Futures broken and Volume+Momentum near-dead,
the only path to 3 is getting Volume+Momentum to spike on a high-volume bar.

**Practical result:** The ensemble is operating as a **2-agent system** (EMA+RSI + Trendline+Levels),
not a 4-agent system. The other two agents contribute nothing in replay mode.

---

## 3. Two-Vote Bars: What Blocked the 3rd Vote

**177 bars reached exactly 2/4 votes.** These are bars where EMA+RSI and Trendline+Levels
both voted bull — the real signal — but the system held.

### What was missing on those bars

| Condition | Count | % of 2-vote bars |
|---|---|---|
| UW context neutral | 177 | 100% |
| Volume ratio < 1.0x | 157 | 88.7% |
| Volume gate rejection | 157 | 88.7% |
| RSI < 50 | 65 | 36.7% |
| RSI > 70 | 50 | 28.2% |
| EMA21 below EMA50 | 48 | 27.1% |
| Price below VWAP | 45 | 25.4% |
| EMA9 below EMA21 | 30 | 16.9% |
| Regime gate | 20 | 11.3% |

### What would unlock the 3rd vote

The analysis shows **131 of 177 bars** (74%) had VWAP+Futures at score=+1 — one point
short of the bull threshold (+2). On these bars, if ES/NQ futures were available and above
their VWAP, VWAP+Futures would score +4 (both above) → bull vote → 3/4 total.

**The 3rd vote blocker is almost entirely the missing futures data, not the volume filter.**

| Fix | Bars Unlocked | Notes |
|---|---|---|
| Feed ES/NQ to replay (real futures OHLCV) | 131/177 (74%) | Correct fix |
| Lower VWAP+Futures threshold from +2 to +1 | 177/177 (100%) | Hacky; makes agent meaningless |
| Ignore VWAP+Futures entirely, require 3/3 of remaining | varies | Structural change |
| Lower Volume+Momentum threshold | ~20 bars extra | Minor impact |

---

## 4. Three-Vote Bars — Full Context

All three "3-vote bars" are the **same bar logged 3 times**. This is a duplicate-logging bug:
the VoteLogger is being called multiple times per bar (once per scoring path). There is
effectively **only 1 unique 3-vote bar** in the entire 5-day window.

### The Single 3-Vote Bar

| Field | Value |
|---|---|
| Timestamp | 2026-04-24 15:45 UTC (11:45 ET) |
| Price | $713.28 |
| VWAP | $711.99–$712.28 (drifting slightly across 3 log entries) |
| EMA9 | $712.23 |
| EMA21 | $711.78 |
| EMA50 | $711.89 |
| RSI14 | 83.58 |
| Vol ratio | 3.316x |
| UW bias | neutral |

| Agent | Vote | Score |
|---|---|---|
| VWAP+Futures | neutral | +1 (no futures data) |
| EMA+RSI | **bull** | +2 (EMA9>EMA21, RSI>70 → -1 fade → net+1? Actually score was +2 logged) |
| Trendline+Levels | **bull** | +2 |
| Volume+Momentum | **bull** | +2 (vol_ratio=3.3x > 2.0x, momentum positive) |

**Why only 1 became a signal:** The first duplicate row fired and passed all gates (no rejection).
The second and third rows hit `cooldown_60m_remaining` because the signal was already recorded
at that timestamp for the same ticker. This is not a cooldown logic problem — it is a
**VoteLogger being called from within a deduplication loop that runs 3 times**.

**Why Vol+Momentum voted bull here:** vol_ratio = 3.3x > 2.0 threshold → score +2 (bull).
This is the only bar in the window with both high volume AND EMA/trend confirmation.
RSI = 83.58 (overbought) → EMA+RSI applied the -1 fade, but EMA9>EMA21 is +2, net = +1...
wait, the logged score shows +2. EMA+RSI must have scored +2 before the RSI fade was applied
or the fade was not triggered (RSI > 70 gives -1 but starts from +2 → net +1 → below bull threshold).
The logged score of +2 suggests a slight discrepancy — worth verifying agent2 code.

---

## 5. Diagnosis and Recommendations

### What NOT to tune

- **Vote thresholds (3/4):** The threshold is not the problem. The problem is 2 agents are
  producing no signal. Lowering to 2/4 means trusting a 2-agent system.
- **EMA/VWAP parameters:** EMA+RSI and Trendline+Levels are working correctly and producing
  directional votes aligned with actual SPY behavior (BULL regime Apr 21–25 → bullish votes).
- **Cooldown:** The 60-min cooldown fired correctly. The "3 bars" issue is a logging bug.
- **Regime gate:** Only 20 of 177 two-vote bars were regime-blocked. Not the primary bottleneck.

### What TO fix (priority order)

#### 🔴 Fix 1: Feed real ES/NQ futures to replay (highest impact)
**Expected unlock: 74% of suppressed 2-vote bars → 3-vote bars.**
The VWAP+Futures agent is the backbone of the live system. In replay, it receives no futures
data so it permanently scores ±1 (never ±3 or ±4). Adding daily or 5-min ES/NQ bars from
yfinance (`/ES=F`, `/NQ=F`) to the `EnsembleAdapter.score()` call in replay mode would
restore this agent's full range.

Implementation: in `BacktestEngine`, load ES/NQ history alongside SPY and pass
`es_bars=` and `nq_bars=` to `ensemble_score()` the same way the live system does.

#### 🔴 Fix 2: Fix VoteLogger duplicate logging
The VWAP computation in the VoteLogger drifts across 3 calls per bar (VWAP at 711.99,
712.19, 712.28), meaning `vote_log.log()` is called 3 times per bar. Find and remove the
duplicate call site in the bar loop.

#### 🟡 Fix 3: Volume+Momentum — add gap/momentum scoring without vol spike
Currently the agent scores 0 on 96% of bars. The gap detection (+/-1) barely fires.
Adding a price momentum component (e.g., score +1 if 5-bar return > +0.15% and not already counted)
would give this agent a voice on trending bars even without a volume spike.

#### 🟡 Fix 4: Volume ratio filter in config — lower to 0.7x for backtest
The live 1.0x volume ratio makes sense when real-time Schwab streaming shows the current
bar's volume vs the day's running average. In replay using yfinance daily/5-min bars,
volume ratios are structurally lower (5-min bars vs full-day volume). Consider setting
`min_volume_ratio=0.7` in the `normal` profile or adding a `replay` profile.

#### 🟢 Fix 5: EMA+RSI overbought fade — verify score accounting
At RSI=83.58, agent2 should return `+2 (ema cross) -1 (RSI>70 fade) = +1` (below bull threshold).
But it logged score=+2, which means the bar fired with EMA+RSI effectively getting a free pass.
Audit the actual return value to confirm the agent is computing correctly.

---

## 6. Expected State After Fixes

| Fix Applied | Expected 3-vote bars (5-day window) | Expected signals |
|---|---|---|
| Current (broken) | 1 unique bar | 1 |
| Fix 1 (ES/NQ data) | ~100–130 bars | 3–8 |
| Fix 1 + Fix 4 (vol filter) | ~130–160 bars | 5–12 |
| Fix 1 + Fix 3 (vol momentum) | ~110–140 bars | 4–10 |
| All fixes | ~150–180 bars | 8–15 |

A 5-day window on SPY should realistically produce 5–15 signals (1–3/day) given a BULL regime.
That is the calibration target.

---

## 7. Files

| File | Location |
|---|---|
| debug_votes.csv | `state/backtests/replay/SPY_.../debug_votes.csv` |
| calibration_report.json | `state/backtests/replay/SPY_.../calibration_report.json` |
| trades.csv | `state/backtests/replay/SPY_.../trades.csv` |
| backtest_report.md | `state/backtests/replay/SPY_.../backtest_report.md` |
| Ensemble scorer | `services/strategy-engine/ensemble_scorer.py` |
| Replay engine | `services/backtest/point_in_time_replay.py` |
| Threshold config | `config/backtest_thresholds.json` |

---

_MiroFish ensemble calibration report — generated 2026-04-26_
