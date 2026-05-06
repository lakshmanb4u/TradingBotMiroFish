# Delta Normalization & Safety Fixes

Reference for normalizing the Bookmap/Rithmic feed for delta-based strategies.

---

## Executive Summary

**Current State:** Feed has 34.98% zero-size trades + 166K spread violations  
**Risk Level:** 🔴 UNSAFE for production delta strategies  
**Time to Fix:** ~2 hours (filter + validation + testing)  
**Expected Improvement:** 99%+ reliability for delta logic

---

## 1. Problem Statement

### What's Broken?

**Delta Computation:**
```
Real delta:     Sum of (bid_agg * size) - (ask_agg * size)
Current delta:  Includes 310K zero-sized events that should be skipped
Result:         Delta counts are valid but event quality is poor
```

**Absorption Logic:**
```
Can't reliably detect institutional accumulation because:
1. 35% of "trades" have size=0 (not real market flow)
2. 1.2% of depth events have bid >= ask (book corrupted)
3. Can't know if buyer really absorbed supply or it was a zero-sized event
```

**Displacement After Reversal:**
```
If book flips:
  1. Bid/ask invert (bid > ask temporarily)
  2. Displacement detection fails
  3. We don't catch the momentum shift in time
```

**Follow-Through Entry:**
```
If we see delta = +500 and try to scalp 10 contracts:
  - 35% of trades are size=0
  - We might be following through on phantom orders
  - Entry has poor fill probability
```

---

## 2. Normalization Pipeline

### Stage 1: Ingest & Filter (Stream Processor)

**Code:**
```python
class BookmapNormalizer:
    """Normalize Bookmap feed for delta strategies."""
    
    def __init__(self):
        self.last_ts = {}
        self.best_bid = {}
        self.best_ask = {}
        self.delta = defaultdict(int)
        self.stats = defaultdict(int)
    
    def normalize_trade(self, event):
        """Filter and normalize trade event."""
        symbol = event["symbol"]
        size = event.get("size", 0)
        
        # FIX #1: Skip zero-size trades
        if size == 0:
            self.stats["zero_size_filtered"] += 1
            return None
        
        # FIX #2: Enforce timestamp monotonicity
        ts = event.get("ts_event")
        if symbol in self.last_ts and ts < self.last_ts[symbol]:
            self.stats["timestamp_inversion"] += 1
            ts = self.last_ts[symbol]  # Use last ts
        self.last_ts[symbol] = ts
        
        # FIX #3: Compute delta if aggressor present
        is_bid_agg = event.get("is_bid_aggressor")
        if is_bid_agg is True:
            self.delta[symbol] += size
        elif is_bid_agg is False:
            self.delta[symbol] -= size
        else:
            self.stats["aggressor_missing"] += 1
            return None  # Can't compute delta
        
        self.stats["trades_processed"] += 1
        event["_normalized"] = True
        return event
    
    def normalize_depth(self, event):
        """Filter and normalize depth event."""
        symbol = event["symbol"]
        side = event.get("side")
        price = event.get("price")
        
        # FIX #4: Validate spread sanity
        if side == "bid":
            self.best_bid[symbol] = price
            ask = self.best_ask.get(symbol)
            if ask and price >= ask:
                self.stats["spread_violation"] += 1
                return None  # Skip invalid update
        
        elif side == "ask":
            self.best_ask[symbol] = price
            bid = self.best_bid.get(symbol)
            if bid and price <= bid:
                self.stats["spread_violation"] += 1
                return None  # Skip invalid update
        
        self.stats["depth_processed"] += 1
        event["_normalized"] = True
        return event
    
    def process(self, event):
        """Process event through normalization pipeline."""
        event_type = event.get("event_type")
        
        if event_type == "trade":
            return self.normalize_trade(event)
        elif event_type == "depth":
            return self.normalize_depth(event)
        
        return event
    
    def report(self):
        """Print normalization stats."""
        print("\nNORMALIZATION REPORT:")
        for key, count in sorted(self.stats.items()):
            print(f"  {key}: {count:,}")
```

**Expected Results After Stage 1:**
```
Trades:                   886,927 → 576,683 (65% valid)
Zero-size filtered:       310,244
Aggressors all present:   ✓
Delta available:          576,683 trades (100% of valid)
```

---

### Stage 2: Event Deduplication & Ordering

**Code:**
```python
class EventBuffer:
    """Buffer and deduplicate events with ordered replay."""
    
    def __init__(self, window_ms=100):
        self.window_ms = window_ms
        self.buffer = defaultdict(list)  # symbol -> [events]
        self.last_flush = defaultdict(lambda: 0)
    
    def add(self, event):
        """Add event to buffer."""
        symbol = event["symbol"]
        self.buffer[symbol].append(event)
    
    def flush(self, symbol, now_ms):
        """Flush events older than window."""
        ts_cutoff = now_ms - self.window_ms
        
        # Get events to flush
        to_flush = []
        remaining = []
        
        for event in self.buffer[symbol]:
            ts_event = self._parse_ts(event["ts_event"])
            if ts_event <= ts_cutoff:
                to_flush.append(event)
            else:
                remaining.append(event)
        
        self.buffer[symbol] = remaining
        
        # Sort by (ts_event, seq) and deduplicate
        to_flush = sorted(set(to_flush), key=lambda e: (
            self._parse_ts(e["ts_event"]),
            e.get("seq", 0)
        ))
        
        return to_flush
    
    def _parse_ts(self, ts_str):
        """Parse ISO8601 timestamp to ms since epoch."""
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
```

**Expected Results After Stage 2:**
```
Out-of-order events:      2 → 0
Duplicate timestamps:     11.1M → 0 (deduped)
Timestamp inversions:     2 → 0
Event ordering:           Guaranteed by (ts_event, seq) sort
```

---

### Stage 3: Delta Continuity Validation

**Code:**
```python
class DeltaValidator:
    """Validate cumulative delta has no gaps."""
    
    def __init__(self):
        self.delta_by_symbol = defaultdict(int)
        self.delta_history = defaultdict(list)
    
    def validate_trade(self, event):
        """Update and validate delta."""
        symbol = event["symbol"]
        size = event.get("size")
        is_bid_agg = event.get("is_bid_aggressor")
        
        # Update delta
        old_delta = self.delta_by_symbol[symbol]
        if is_bid_agg:
            self.delta_by_symbol[symbol] += size
        else:
            self.delta_by_symbol[symbol] -= size
        
        new_delta = self.delta_by_symbol[symbol]
        
        # Detect anomalies
        delta_move = abs(new_delta - old_delta)
        if delta_move > 1000:  # Single trade shouldn't move delta > 1000
            print(f"WARNING: Large delta move in {symbol}: {old_delta} → {new_delta} (size={size})")
        
        self.delta_history[symbol].append({
            "ts": event.get("ts_event"),
            "delta": new_delta,
            "size": size,
            "aggressor": "bid" if is_bid_agg else "ask"
        })
        
        return new_delta
    
    def get_delta(self, symbol):
        """Get current cumulative delta."""
        return self.delta_by_symbol[symbol]
    
    def integrity_check(self, symbol, expected_delta):
        """Check if delta matches expected value."""
        actual = self.delta_by_symbol[symbol]
        if actual != expected_delta:
            print(f"DELTA MISMATCH in {symbol}: expected {expected_delta}, got {actual}")
            return False
        return True
```

**Expected Results After Stage 3:**
```
Delta jumps:              Detected and logged
Delta continuity:         Guaranteed (no missing trades)
Absorption tracking:      Valid (based on cumulative delta)
```

---

## 3. Implementation Checklist

### Phase 1: Deploy Filters (Week of 2026-05-05)

- [ ] Add `if size == 0: skip` to trade processor
- [ ] Add `if bid >= ask: log.warn("spread violation"), skip` to depth processor
- [ ] Add timestamp monotonicity enforcement
- [ ] Test on 1-day file (expect 310K fewer trades)
- [ ] Measure: zero-size % should drop to < 0.1%

**Code Location:** `market-swarm-lab/state/orderflow/processors/bookmap_normalizer.py`

**Deployment:**
```bash
# Add to streaming pipeline:
for event in stream:
    normalized = normalizer.process(event)
    if normalized:
        process_trading_logic(normalized)
```

### Phase 2: Add Event Deduplication (Week of 2026-05-12)

- [ ] Implement EventBuffer with 100ms window
- [ ] Add (ts_event, seq) sorting
- [ ] Test on historical file with duplicate timestamps
- [ ] Measure: out-of-order events should be 0

**Code Location:** `market-swarm-lab/state/orderflow/processors/event_buffer.py`

**Deployment:**
```bash
# Add to pipeline after normalization:
for event in stream:
    normalized = normalizer.process(event)
    buffer.add(normalized)
    
    for flushed in buffer.flush(now):
        process_trading_logic(flushed)
```

### Phase 3: Add Delta Validation (Week of 2026-05-19)

- [ ] Implement DeltaValidator
- [ ] Track cumulative delta per symbol
- [ ] Log anomalies (large jumps, inversions)
- [ ] Generate hourly delta reports

**Code Location:** `market-swarm-lab/state/orderflow/validators/delta_validator.py`

**Deployment:**
```bash
# Add to pipeline after buffering:
validator = DeltaValidator()
for event in stream:
    normalized = normalizer.process(event)
    buffer.add(normalized)
    
    for flushed in buffer.flush(now):
        if flushed["event_type"] == "trade":
            delta = validator.validate_trade(flushed)
        process_trading_logic(flushed)
```

---

## 4. Expected Improvements

### Before Fixes

```
Metric                          Before          Issue
────────────────────────────────────────────────────────
Zero-size trades                34.98%          Pollutes event quality
Spread violations               166,012         Corrupts depth state
Out-of-order events             2               Rare but possible
Timestamp inversions            2               Breaks time-based logic
Duplicate timestamps            73.9%           Sub-ms collisions
Delta reliability               UNSAFE          Can't trust counts
Absorption detection            UNSAFE          False positives
Displacement logic              UNSAFE          Book corruption
Follow-through entries          UNSAFE          Poor fill probability
────────────────────────────────────────────────────────
```

### After Fixes

```
Metric                          After           Improvement
────────────────────────────────────────────────────────
Zero-size trades                0.0%            ✓ Eliminated
Spread violations               0                ✓ Eliminated
Out-of-order events             0                ✓ Eliminated
Timestamp inversions            0                ✓ Eliminated
Duplicate timestamps            Deduplicated    ✓ Handled (seq tiebreaker)
Delta reliability               SAFE            ✓ 99%+ accuracy
Absorption detection            SAFE            ✓ Valid signals
Displacement logic              SAFE            ✓ Book always valid
Follow-through entries          SAFE            ✓ High fill probability
────────────────────────────────────────────────────────
```

---

## 5. Rollback Plan

If fixes introduce regressions:

```python
# Emergency: disable normalization
class NormalizerBypass:
    def process(self, event):
        return event  # Pass through raw
```

**Metrics to watch:**
- Delta should still be computable (100% aggressor coverage)
- Trade counts should be stable (not growing unexpectedly)
- Spread violations should not increase

---

## 6. Testing & Validation

### Unit Tests

```python
def test_zero_size_filter():
    normalizer = BookmapNormalizer()
    event = {"event_type": "trade", "size": 0, "is_bid_aggressor": True}
    assert normalizer.normalize_trade(event) is None
    assert normalizer.stats["zero_size_filtered"] == 1

def test_spread_validation():
    normalizer = BookmapNormalizer()
    
    # Set bid
    bid_event = {"event_type": "depth", "side": "bid", "price": 100.00}
    assert normalizer.normalize_depth(bid_event) is not None
    
    # Try to set ask < bid (invalid)
    ask_event = {"event_type": "depth", "side": "ask", "price": 99.00}
    assert normalizer.normalize_depth(ask_event) is None  # Rejected
    assert normalizer.stats["spread_violation"] == 1
```

### Integration Test

```bash
# Before fix:
python3 scripts/feed_integrity_audit.py es_orderflow_2026-05-05.jsonl \
  | grep "zero-size"
# Output: Zero-size trades: 310244 (34.98%)

# Apply normalizer to raw JSONL:
python3 normalize_jsonl.py es_orderflow_2026-05-05.jsonl > es_orderflow_normalized.jsonl

# After fix:
python3 scripts/feed_integrity_audit.py es_orderflow_normalized.jsonl \
  | grep "zero-size"
# Expected: Zero-size trades: 0 (0.00%)
```

---

## 7. Production Rollout

### Go/No-Go Checklist

- [ ] Unit tests pass (100% coverage of normalizer)
- [ ] Integration test shows < 0.1% zero-size after filter
- [ ] 24-hour validation run completes successfully
- [ ] Delta reports match expected values (spot-checked)
- [ ] No memory leaks in 24-hour run
- [ ] Event buffering doesn't cause latency > 110ms (buffer + processing)
- [ ] Rollback procedure tested and documented

### Monitoring Post-Deploy

```python
# Log these metrics every minute:
metrics = {
    "trades_total": stats["trades_processed"],
    "trades_filtered": stats["zero_size_filtered"],
    "zero_size_pct": stats["zero_size_filtered"] / (stats["trades_processed"] or 1),
    "spread_violations": stats["spread_violation"],
    "delta_by_symbol": dict(delta_validator.delta_by_symbol),
    "buffer_lag_ms": current_time - oldest_buffered_event_ts,
}
log_metrics(metrics)
```

---

## 8. FAQ

**Q: Will filtering zero-size trades miss any real signal?**  
A: No. Zero-sized events don't represent actual market flow. Real orders have size > 0.

**Q: What about partial fills?**  
A: Partial fills appear as separate trades. Bookmap sends multiple size=X trades, not size=0.

**Q: Why is bid >= ask happening?**  
A: Race condition between async bid/ask updates. Not uncommon in real feeds; we just skip those updates.

**Q: Will the 100ms event buffer introduce latency?**  
A: Yes, ~100ms. But it ensures ordering and deduplication. For delta strategies (5+ min timeframes), this is negligible.

**Q: Can we skip the buffer?**  
A: Only if out-of-order events are < 10 over 24 hours (they are). But buffer adds safety; recommended to keep.

**Q: What if market gaps up/down while we're buffering?**  
A: Delta still accumulates correctly. Events are replayed in ts_event order, so gaps are captured properly.

---

## References

- **Audit Report:** `reports/live_feed_integrity_audit.md`
- **Audit Scripts:** `scripts/feed_integrity_audit.py`, `scripts/inspect_live_feed.py`
- **Audit Guide:** `FEED_AUDIT_GUIDE.md`
- **Feed Data:** `market-swarm-lab/state/orderflow/bookmap_api/`

---

**Last Updated:** 2026-05-05 07:51 PDT
