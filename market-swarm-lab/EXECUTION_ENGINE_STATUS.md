# Execution Engine Status
Generated: 2026-05-03 6:30 PM PT

## What Is Validated

1. **✅ Stop/target evaluation frequency** — `_evaluate_exit()` runs on every DepthEvent + TradeEvent, not just trades
2. **✅ Symbol-isolated trade routing** — guard clause `if ev.symbol != open_trade.symbol: continue` prevents cross-symbol contamination
3. **✅ Bid/ask-aware fills** — stop fills use `max(trade_price, best_bid)`; target uses `min(trade_price, best_ask)`; time exit uses mid
4. **✅ Trailing stop logic** — breakeven activation at 1:1, then trails 0.5R behind favorable price. Only evaluates on TradeEvent (not depth levels)
5. **✅ MAE/MFE tracking** — computes on TradeEvent only; uses correct per-symbol tick size
6. **✅ Execution reason tracking** — every trade has `exit_reason`: stop | target | time | trailing_stop | replay_end
7. **✅ Sanity check** — deterministic output: 30 trades, 63.3% WR, $348.75 across reruns

## What Remains Unvalidated

1. **❌ Stop execution in live market** — 0/30 trades hit stop. Needs ES futures with 16-tick ($50) moves within 120s. Sunday simulation data too slow.
2. **❌ Target execution in live market** — 0/30 trades hit target. Same reason.
3. **❌ Trailing stop in live market** — 0/30 trades hit trailing stop. Needs 1:1 profit then pullback.
4. **❌ Sweep detection** — 0 sweeps detected on this dataset (threshold=20 contracts, simulation had small sizes).
5. **❌ Signal quality** — 100% false breakout rate. Need to tune signal thresholds on market-hours data.
6. **❌ Multi-contract sizing** — all trades use 1 contract. `MAX_RISK_PER_TRADE / (STOP_TICKS * tick_value)` yields 1 for BTC with $5/tick.
7. **❌ Partial fills / slippage** — fills assume full size at quoted price. Real execution has slippage.

## Known Limitations of Sunday Replay Data

- **Time-exit dominant**: 29/30 trades (97%) exit on 120s timeout. In a real futures market, stops/targets would hit within seconds.
- **Mixed symbols**: BTC, ES, NQ, GC interleaved. Symbol isolation works but reduces signal density per instrument.
- **Low liquidity**: Sunday simulation = wide spreads, few large sweeps.
- **Static prices**: ESU1 fixed at ~4505, BTC at ~48300, NQ at ~15430. No trending makes stops/targets unreachable.
- **No orderbook depth**: Bookmap replay data has trade events but limited depth updates. Best bid/ask reconstruction is incomplete.
- **Missing candles**: Some symbols get no footprint bars due to sparse data.

## Monday Live Validation Checklist

- [ ] Start Bookmap before 9:30 AM ET, load ESM6.CME@BMD
- [ ] Confirm JAR strategy loads and `Layer1ApiDataAdapter` initializes
- [ ] Verify JSONL output in `state/orderflow/bookmap_api/es_orderflow_YYYY-MM-DD.jsonl`
- [ ] Run replay on live data (`--bar-secs 5`)
- [ ] Confirm non-zero stop_hit and target_hit counts
- [ ] Confirm trailing_stop fires on profitable trades
- [ ] Verify MAE < STOP_TICKS (16) for most trades
- [ ] Verify MFE > STOP_TICKS for winning trades
- [ ] Confirm symbol isolation (no ES exits on BTC events)
- [ ] Check sweep detection rates (should see >0)
- [ ] Compare signal count: should see 50-200 trades in first hour of volatility
- [ ] Run with `TIME_EXIT_SECONDS = 30` for faster feedback
- [ ] Generate comparison report: Sunday vs Monday execution breakdown
