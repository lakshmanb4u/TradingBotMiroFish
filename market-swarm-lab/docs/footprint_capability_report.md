# Bookmap Footprint Capability Report

## Source of Truth

Bookmap L1 Core API provides these raw events via `Layer1ApiDataAdapter`:

| Event | Callback | Data Available |
|-------|----------|---------------|
| Trade | `onTrade(alias, price, size, TradeInfo)` | Price, Size, isBidAggressor |
| Depth | `onDepth(alias, isBid, price, size)` | Side, Price, Size |
| BBO | `onBbo(...)` | Best Bid/Ask (derived from depth) |
| Instrument | `onInstrumentAdded(...)` | Tick size, exchange, multiplier |

## What Bookmap Provides (Confirmed)

### 1. Aggressor Identification
- `TradeInfo.isBidAggressor: boolean`
- **TRUE** → Seller hit the bid (aggressive selling, delta -)
- **FALSE** → Buyer lifted the offer (aggressive buying, delta +)
- **This is sufficient for footprint delta calculation**

### 2. Depth Updates
- `onDepth(alias, isBid, price, size)`
- Current size at each price level
- Bid/ask distinction
- **Sufficient for orderbook imbalance tracking**

### 3. What Bookmap Does NOT Provide (Limitations)

| Missing | Workaround |
|---------|-----------|
| Individual order IDs | Not needed for footprint |
| Order type (iceberg, etc.) | Not available via L1 API |
| Historical depth snapshots | Only real-time updates |
| Time-in-force data | Not available |
| Volume profile per candle | Must aggregate ourselves |

## Available Footprint Metrics (Programmatic)

### Implemented
- [x] Candle delta (net aggressive volume)
- [x] Bid/ask imbalance ratio
- [x] Aggressive buy/sell volume per candle
- [x] Absorption detection (large size, minimal price move)
- [x] Pullback delta analysis
- [x] Delta divergence detection
- [x] Delta flip detection

### Possible with Bookmap API + Code
- [ ] Cumulative delta (running total)
- [ ] Volume profile per level
- [ ] VWAP with delta weighting
- [ ] POC (Point of Control) identification
- [ ] Value area calculation

## Reddit Strategy Alignment

The Reddit orderflow strategy focuses on:

1. **Candle Delta**: ✅ Available via isBidAggressor
2. **Bid/Ask Imbalance**: ✅ Computable from trades + depth
3. **Aggressive Volume**: ✅ Direct from TradeInfo
4. **Pullback Delta**: ✅ Computable from candle sequences
5. **Absorption**: ✅ Detectable (large size @ level, minimal move)
6. **Failed Auction**: ⚠️ Requires pattern detection (multi-test + reversal)

## Recommendation

**Bookmap L1 API IS sufficient** for implementing programmatic footprint metrics without ATAS. The key insight:

- `isBidAggressor` gives us the delta signature
- `onDepth` gives us liquidity context
- Code aggregates these into candle-level metrics

## Files

| File | Purpose |
|------|---------|
| `footprint_builder.py` | Build candle footprints from raw events |
| `delta_profile.py` | Delta divergence and flip analysis |
| `imbalance_detector.py` | Orderbook imbalance scoring |
| `sweep_detector.py` | Sweep/reclaim with footprint confirmation |
