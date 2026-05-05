# Replay Window Cache Schema

## Problem With Current Design

**Old Format:** CSV with giant cells
```
signal_id, signal_ts, outcome_prices
26, 2026-05-04T19:06:30Z, 7226.25|7226.50|7226.75|...[55K elements]...
```

**Issues:**
- CSV field limit: 131KB default (55K prices exceed this)
- Parsing: O(n) string split per row
- Memory: Entire row materialized at once
- Iteration: Cannot stream events

**Result:** Parsing timeout, memory pressure, runtime kills

---

## New Format: Row-Wise Event Tables

Store each tick as a separate row:

| signal_id | event_idx | timestamp_utc | price | delta | bid_volume | ask_volume | side_imbalance | regime_marker |
|-----------|-----------|---------------|-------|-------|-----------|-----------|----------------|---------------|
| 26 | 0 | 2026-05-04T19:06:30.000Z | 7226.25 | +100 | 1234 | 987 | 0.21 | consolidation |
| 26 | 1 | 2026-05-04T19:06:30.001Z | 7226.50 | -50 | 1100 | 1050 | -0.04 | consolidation |
| 26 | 2 | 2026-05-04T19:06:30.002Z | 7226.75 | +200 | 1400 | 900 | 0.36 | consolidation |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |
| 26 | 54999 | 2026-05-04T19:36:30.000Z | 7225.50 | -150 | 800 | 1100 | -0.27 | consolidation |

---

## Schema Definition

### Events Table

**Table: `replay_events`**

```sql
CREATE TABLE replay_events (
  signal_id INT,                         -- Reference to signal (26-50)
  event_idx INT,                         -- Sequential event within window (0-55K)
  timestamp_utc TIMESTAMP,               -- Event timestamp (UTC)
  price DECIMAL(10, 2),                  -- Trade price
  delta INT,                             -- Buy delta this tick
  bid_volume INT,                        -- Cumulative bid volume
  ask_volume INT,                        -- Cumulative ask volume
  side_imbalance FLOAT,                  -- (buy_vol - sell_vol) / total_vol
  liquidity_pull INT,                    -- Large order absorption
  liquidity_stack INT,                   -- Stacked orders at level
  regime_marker VARCHAR(32),             -- 'consolidation', 'trending', 'breakout'
  
  PRIMARY KEY (signal_id, event_idx),
  INDEX idx_signal_ts (signal_id, timestamp_utc)
);
```

**Size:** ~55K rows × 25 signals = 1.4M rows
**Estimated size:** 150-200 MB (parquet) vs 10GB (CSV)

---

### Signal Metadata Table

```sql
CREATE TABLE signal_metadata (
  signal_id INT PRIMARY KEY,
  signal_ts_utc TIMESTAMP,
  direction VARCHAR(10),                 -- 'LONG', 'SHORT'
  entry_price DECIMAL(10, 2),
  candle_low DECIMAL(10, 2),
  candle_high DECIMAL(10, 2),
  absorption_confidence FLOAT,
  follow_through_detected BOOLEAN,
  
  INDEX idx_signal_ts (signal_ts_utc)
);
```

---

## Format Options

### Option A: Parquet

**Pros:**
- Columnar compression (100-150 MB for 1.4M rows)
- Fast predicate pushdown (filter by signal_id before loading)
- Native support in Python (pyarrow)
- Streaming read

**Cons:**
- Requires PyArrow library
- Less human-readable

**Load time:** ~50-100ms for single signal window

### Option B: DuckDB

**Pros:**
- In-process SQL engine
- Automatic indexing
- Extremely fast queries
- Can write/read parquet directly
- No server overhead

**Cons:**
- Larger library footprint
- Less widely supported

**Load time:** ~10-30ms for single signal window

### Option C: SQLite

**Pros:**
- Ubiquitous (included in Python)
- B-tree indexes
- ACID compliance
- SQL queries

**Cons:**
- Slower than DuckDB
- Column storage not optimal for time-series

**Load time:** ~100-200ms for single signal window

---

## Recommended: Parquet + DuckDB

**Why this combination:**
1. Write signals as parquet (parallel, compressed)
2. Load parquet into DuckDB for querying
3. DuckDB handles: filtering, aggregation, statistics
4. Fast iteration: load one signal, run experiment

**Architecture:**

```
JSONL (40GB) → Extract windows → Parquet (150MB) → DuckDB (in-memory) → Experiment
               [one-time, 71s]    [fast, 1s]      [< 100ms per signal]
```

---

## Migration Plan

**Phase 1: Build Writers**
- `cache/parquet_writer.py` - Extract JSONL windows → parquet
- `cache/duckdb_loader.py` - Load parquet → DuckDB queries

**Phase 2: Benchmark**
- Compare old CSV vs new parquet/DuckDB
- Measure: load time, memory, iteration speed

**Phase 3: Experiment #2 Integration**
- Modify `experiment2_cached.py` to use DuckDB instead of CSV
- Should run in <2 minutes

---

## Query Patterns for Experiment #2

### Get all events for a signal

```sql
SELECT * FROM replay_events
WHERE signal_id = 26
ORDER BY event_idx
LIMIT 55395;
```

**DuckDB latency:** ~10ms

### Find follow-through breakout

```sql
SELECT MIN(event_idx)
FROM replay_events
WHERE signal_id = 26
  AND event_idx > 100
  AND price < (
    SELECT MIN(price) FROM replay_events
    WHERE signal_id = 26 AND event_idx BETWEEN 0 AND 100
  ) - 0.5
```

**DuckDB latency:** ~5ms

### Aggregate MAE/MFE

```sql
SELECT 
  signal_id,
  MAX(price) as max_price,
  MIN(price) as min_price
FROM replay_events
WHERE signal_id = 26
GROUP BY signal_id;
```

**DuckDB latency:** ~2ms

---

## Expected Performance Improvement

| Metric | Old CSV | New Parquet+DuckDB | Improvement |
|--------|---------|-------------------|------------|
| Load single signal | 30+ seconds (fails) | 10-50ms | **600-3000x faster** |
| Memory per signal | 500MB+ | 10-20MB | **25-50x smaller** |
| Experiment #2 runtime | >600s (timeout) | <120s | **5x faster** |
| Iteration speed | N/A (blocked) | 25 signals/min | **Enabled** |

---

## Implementation Steps

1. **Build parquet writer** (30 min)
   - Use existing JSONL accessor + index
   - Stream events to parquet
   - Write metadata table

2. **Build DuckDB loader** (20 min)
   - Load parquet into DuckDB
   - Create indexes
   - Test query patterns

3. **Benchmark** (10 min)
   - Time old CSV parse
   - Time new parquet load
   - Measure memory

4. **Integrate with Experiment #2** (15 min)
   - Replace CSV reader with DuckDB queries
   - Verify same results

**Total ETA:** 75 minutes

---

## File Structure

```
cache/
├── schema_definition.md                (this file)
├── signals_26_50.parquet              (output, ~10MB)
├── signals_26_50_metadata.parquet     (output, ~50KB)
├── parquet_writer.py                  (new)
└── duckdb_loader.py                   (new)

reports/
└── replay_cache_redesign.md           (benchmark report)
```
