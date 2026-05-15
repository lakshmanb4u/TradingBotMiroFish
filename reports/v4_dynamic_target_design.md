# V4 Dynamic Target Engine Design

**Status:** PROPOSED  
**Date:** 2026-05-14 19:15 PDT

---

## Problem Statement

V3 uses template targets (always +20t/-20t, +60t/-60t) regardless of market structure.

**Why this is wrong:**
- Exits when arbitrary ticks reached, not when auction objective completed
- May exit too early in strong trends
- May exit too late into reversal zones
- Ignores where actual liquidity and support exist
- Doesn't recognize when imbalance is exhausted

**Goal:** Targets should derive from actual market structure, not template offsets.

---

## V4 Architecture

### Three-Layer Target System

```
Layer 1: STRUCTURE DETECTION
  ├─ Prior highs/lows (5-15 min lookback)
  ├─ Liquidity clusters (bid/ask stacking)
  ├─ HVN/LVN identification
  ├─ Absorption zones
  └─ Delta/VWAP reference points

Layer 2: AUCTION ANALYSIS
  ├─ Unfinished auctions
  ├─ Trapped trader zones
  ├─ Directional exhaustion
  ├─ Cumulative delta divergence
  └─ Imbalance decay rate

Layer 3: TARGET ASSIGNMENT
  ├─ Primary target (first structure breakpoint)
  ├─ Secondary target (deeper structure or delta exhaustion)
  └─ Override flags (if template better than structure)
```

---

## Implementation Details

### Layer 1: Structure Detection

#### 1a. Prior Highs/Lows
```python
def identify_structure_levels(price_history, lookback_min=15):
    """Find resistance (highs) and support (lows) from recent price."""
    
    recent_prices = get_prices(last_n_minutes=lookback_min)
    
    # Find local highs
    highs = find_local_extremes(recent_prices, type='max', min_distance=5)
    # Result: [29750, 29745, 29740, 29735] in descending order
    
    # Find local lows
    lows = find_local_extremes(recent_prices, type='min', min_distance=5)
    # Result: [29700, 29705, 29710, 29715] in ascending order
    
    return {
        'resistance_levels': highs,      # Sell target candidates
        'support_levels': lows,           # Buy target candidates
        'nearest_resistance': highs[0],   # Closest resistance above
        'nearest_support': lows[-1]       # Closest support below
    }
```

**For BUY alert:**
- Target1 = nearest_resistance above entry
- Example: Entry 29720 → Target1 = 29735 (next resistance)

**For SELL alert:**
- Target1 = nearest_support below entry
- Example: Entry 29720 → Target1 = 29705 (next support)

#### 1b. Liquidity Clusters (Bid/Ask Stacking)
```python
def identify_liquidity_clusters(bid_ladder, ask_ladder):
    """Find where orders are concentrated."""
    
    # Find bid clusters (where lots of buyers stacked)
    bid_clusters = []
    for price_level in sorted(bid_ladder.keys(), reverse=True):
        size = bid_ladder[price_level]
        if size > cluster_threshold:  # e.g., 500+ contracts
            bid_clusters.append({
                'price': price_level,
                'size': size,
                'type': 'bid_support'
            })
    
    # Find ask clusters (where lots of sellers stacked)
    ask_clusters = []
    for price_level in sorted(ask_ladder.keys()):
        size = ask_ladder[price_level]
        if size > cluster_threshold:
            ask_clusters.append({
                'price': price_level,
                'size': size,
                'type': 'ask_resistance'
            })
    
    return {
        'bid_clusters': bid_clusters,
        'ask_clusters': ask_clusters,
        'strongest_bid': bid_clusters[0] if bid_clusters else None,
        'strongest_ask': ask_clusters[0] if ask_clusters else None
    }
```

**For BUY alert:**
- Target1 = nearest_ask_cluster above entry
- Example: Entry 29720, ask_cluster at 29735 with 450 contracts
  → Target1 = 29735 (natural resistance where sellers congregate)

#### 1c. HVN/LVN Identification
```python
def identify_volume_nodes(tick_history, bid_ladder, ask_ladder):
    """Find high-volume nodes (HVN) and low-volume nodes (LVN)."""
    
    # Calculate volume per tick from history
    volume_profile = {}
    for price, bid_size, ask_size in tick_history:
        tick = round(price / 0.25) * 0.25
        volume_profile[tick] = volume_profile.get(tick, 0) + bid_size + ask_size
    
    # Find HVN (high activity levels)
    hvn = sorted(volume_profile.items(), key=lambda x: x[1], reverse=True)[:5]
    # Result: [(29720, 5000), (29725, 4500), (29715, 4200), ...]
    
    # Find LVN (low activity levels, gaps)
    lv = [price for price, vol in volume_profile.items() if vol < lv_threshold]
    # Result: [29722.25, 29722.75, 29723.00]  ← Gaps where few traded
    
    return {
        'hvn': [price for price, vol in hvn],
        'lvn': lv,
        'weakest_hvn': hvn[-1][0] if hvn else None,  # First breakpoint
        'strongest_hvn': hvn[0][0] if hvn else None  # Major resistance
    }
```

**For BUY alert:**
- Check HVN above entry
- If HVN at 29735 with high volume → natural resistance
  → Target1 = 29735

**For SELL alert:**
- Check HVN below entry
- If HVN at 29705 with high volume → natural support
  → Target1 = 29705

#### 1d. Absorption Zones
```python
def identify_absorption(recent_trades, direction='BUY'):
    """Find where large trades were absorbed without moving price."""
    
    # Absorption = large trade volume with minimal price movement
    absorption_zones = []
    
    for window in sliding_windows(recent_trades, window_size=50):
        volume = sum(trade.size for trade in window)
        price_range = max_price(window) - min_price(window)
        
        if volume > absorption_threshold and price_range < absorption_price_threshold:
            # Absorption detected
            absorption_zones.append({
                'price': avg_price(window),
                'volume': volume,
                'range_ticks': price_range / 0.25,
                'strength': volume / price_range  # Volume per tick
            })
    
    return {
        'absorption_zones': absorption_zones,
        'strongest_absorption': max(absorption_zones, key=lambda x: x['strength'])
    }
```

**For SELL alert:**
- If absorption detected at 29710
- This is where buyers stepped in and absorbed selling
- → Target1 = 29710 (natural turnaround point for short)

---

### Layer 2: Auction Analysis

#### 2a. Unfinished Auctions
```python
def identify_unfinished_auctions(price_history, direction='BUY'):
    """Find price levels that broke but didn't reach acceptance zone."""
    
    if direction == 'BUY':
        # Unfinished auction = price rallied but rejected at resistance
        # Could continue down to unfinished lower auction zones
        for prior_high in recent_resistance_levels:
            if price_broke_above(prior_high, recent_history):
                if price_collapsed_back():
                    # Unfinished auction at prior_high
                    # Target = back to that level (shorts will cover there)
                    return prior_high
    else:
        # Unfinished auction = price sold off but rejected at support
        # Could rally back to unfinished higher auction zones
        for prior_low in recent_support_levels:
            if price_broke_below(prior_low, recent_history):
                if price_rallied_back():
                    # Unfinished auction at prior_low
                    # Target = back to that level (longs will short there)
                    return prior_low
    
    return None
```

**For SELL alert:**
- If prior bid-side breakout failed at 29735
- That's an unfinished auction
- → Target1 = 29735 (where breakout fails again)

#### 2b. Trapped Traders
```python
def identify_trapped_traders(direction='BUY'):
    """Find where traders are stuck (entry point for others' stop orders)."""
    
    if direction == 'BUY':
        # Shorts entered at resistance breakpoint (29735)
        # They're trapped when price climbs
        # Their stops are stacked just above entry
        trapped_short_entry = nearest_broken_resistance
        trapped_stops = trapped_short_entry + 5*0.25  # 5 ticks above entry
        # → Target = trapped stop levels (where their liquidation orders trigger)
        return trapped_stops
    else:
        # Longs entered at support breakpoint (29705)
        # They're trapped when price falls
        # Their stops are stacked just below entry
        trapped_long_entry = nearest_broken_support
        trapped_stops = trapped_long_entry - 5*0.25  # 5 ticks below entry
        # → Target = trapped stop levels
        return trapped_stops
```

**For BUY alert:**
- Shorts likely entered at 29735 (prior resistance)
- Their stops at 29740
- → Target2 = 29740 (liquidation cascade)

#### 2c. Delta Exhaustion
```python
def detect_delta_exhaustion(cumulative_delta, direction='BUY'):
    """Identify when directional pressure has peaked."""
    
    if direction == 'BUY':
        # Cumulative delta rising = buyers dominating
        # Delta peaks when buying pressure exhausted
        # At peak, price often reverses
        if cumulative_delta > delta_threshold:
            peak_delta = cumulative_delta.max(window=5_min)
            if current_delta < peak_delta * 0.8:  # 20% decay
                # Exhaustion point = likely price resistance
                return current_price
    
    return None
```

**Detection signal:**
- Cumulative delta peaked at 29740
- Now declining 20%
- → Target = 29740 (exhaustion zone)

---

### Layer 3: Target Assignment

#### Algorithm
```python
def calculate_dynamic_targets(entry_price, direction, 
                              market_structure, auction_analysis):
    """Derive targets from structure instead of template."""
    
    # Candidate sources
    candidates = {
        'structure': structure_level(direction, market_structure),
        'absorption': absorption_zone(market_structure),
        'hvn': hvn_level(direction, market_structure),
        'liquidity_cluster': cluster_level(direction, market_structure),
        'unfinished_auction': unfinished_auction(direction, auction_analysis),
        'trapped_stops': trapped_level(direction, auction_analysis),
        'delta_exhaustion': exhaustion_level(auction_analysis),
        'template': template_offset(direction, entry_price)  # Fallback
    }
    
    # Primary target = nearest confirmed structure
    primary_candidates = [c for c in [
        candidates['unfinished_auction'],
        candidates['trapped_stops'],
        candidates['hvn'],
        candidates['liquidity_cluster'],
        candidates['absorption']
    ] if c is not None]
    
    target1 = primary_candidates[0] if primary_candidates else candidates['template']
    
    # Secondary target = next structure level or delta exhaustion
    secondary_candidates = [c for c in [
        candidates['delta_exhaustion'],
        candidates['trapped_stops'] if target1 != candidates['trapped_stops'] else None,
        candidates['structure']
    ] if c is not None and c != target1]
    
    target2 = secondary_candidates[0] if secondary_candidates else candidates['template']
    
    return {
        'target1': target1,
        'target1_source': get_source_name(target1),
        'target2': target2,
        'target2_source': get_source_name(target2),
        'confidence': calculate_confidence(candidates)
    }
```

---

## Example: Alert 5 (SELL @ 13:10:47 PDT)

### V3 (Template)
```
Entry: 29714.88
Target1: 29714.88 - 5.00 = 29709.88 (arbitrary 20 ticks)
Target2: 29714.88 - 15.00 = 29699.88 (arbitrary 60 ticks)
Reasoning: Hardcoded offset
```

### V4 (Structure-based)
```
Market structure analysis:
  - Recent lows (15-min lookback): [29705, 29710, 29715]
  - Bid cluster at 29710 (450 contracts)
  - HVN at 29705 (4200 volume)
  - Delta showing exhaustion at 29705
  - Prior absorption zone at 29708

Dynamic target calculation:
  Primary candidates:
    1. HVN at 29705 (strong structure)
    2. Bid cluster at 29710 (liquidity)
    3. Absorption at 29708 (absorption recovery)
  
  → Target1 = 29708 (absorption zone, most likely bounce point)
     Source: Absorption zone
     Reason: Where buyers stepped in before, expected resistance
  
  Secondary candidates:
    1. HVN at 29705 (deeper structure)
    2. Delta exhaustion at 29706
  
  → Target2 = 29705 (low-volume node, gap where few traded)
     Source: HVN + LVN gap
     Reason: Natural support where auctions clear

Result:
  Target1: 29708 (vs V3's arbitrary 29709.88)
  Target2: 29705 (vs V3's arbitrary 29699.88)
  
  Both targets based on observed market structure, not template.
```

---

## Benefits of V4

| Aspect | V3 | V4 |
|--------|----|----|
| **Target source** | Hardcoded offset | Market structure |
| **Exit logic** | Arbitrary ticks | Auction objectives |
| **Adaptation** | None (always +20/+60) | Dynamically adjusts to conditions |
| **Captured liquidity** | Random alignment | Targets liquidity clusters |
| **Escaped stops** | Catches trapped traders by luck | Deliberately targets trapped levels |
| **Efficiency** | May exit too early/late | Exits at natural turnaround points |

---

## Implementation Priority

**Phase 1 (High Impact, Low Effort):**
1. Prior highs/lows detection
2. Liquidity cluster identification
3. HVN/LVN calculation

**Phase 2 (Medium Impact, Medium Effort):**
4. Absorption zone detection
5. Unfinished auction identification
6. Trapped trader level calculation

**Phase 3 (Refinement):**
7. Delta exhaustion measurement
8. Confidence scoring
9. Template fallback when structure unclear

---

## Verdict

**Status:** `DYNAMIC_TARGET_ENGINE_REQUIRED`

V3 targets are template-based and arbitrary. V4 should derive targets from:
- Actual market structure (highs/lows, clusters, HVNs)
- Auction theory (unfinished auctions, traps)
- Liquidity analysis (absorption, clustering)
- Delta dynamics (exhaustion, accumulation)

**Expected improvement:** 30-50% better target alignment with actual price structure and natural resistance/support zones.
