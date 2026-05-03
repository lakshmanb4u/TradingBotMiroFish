# Bookmap L1 API Recorder Setup

## Overview

Instead of manual CSV export or proprietary `.bmf` files, use the official Bookmap L1 API to record ES orderflow events directly to replay-safe JSONL files.

## Components

| Component | Purpose |
|-----------|---------|
| `BookmapOrderflowRecorder.java` | Java add-on using Core L1 API |
| `build_bookmap_recorder.sh` | Compiles add-on into `.jar` |
| `install_bookmap_recorder.sh` | Installs jar into Bookmap strategies dir |
| `normalize_bookmap_export.py` | Converts JSONL → Parquet with audit |
| `sweep_detector.py` | Detects sweep/reclaim from JSONL |
| `spy_confirmation.py` | Confirms with SPY VWAP/EMA/volume |
| `mirofish_signal.py backtest-orderflow` | CLI for end-to-end backtest |

## Bookmap API Reference

- Repository: `https://github.com/BookmapAPI/DemoStrategies`
- Key interfaces used:
  - `Layer1ApiDataAdapter` → `onDepth()`, `onTrade()`
  - `Layer1ApiInstrumentAdapter` → `onInstrumentAdded()`
  - `Layer1ApiFinishable` → cleanup on shutdown

## Build & Install

```bash
# 1. Clone Bookmap DemoStrategies (already done at ../bookmap-api-reference)
git clone https://github.com/BookmapAPI/DemoStrategies.git bookmap-api-reference

# 2. Find BookmapAPI.jar (install Bookmap first)
cp "/Applications/Bookmap.app/Contents/Java/BookmapAPI.jar" \
   market-swarm-lab/bookmap-recorder/lib/

# 3. Build
bash market-swarm-lab/scripts/build_bookmap_recorder.sh

# 4. Install
bash market-swarm-lab/scripts/install_bookmap_recorder.sh

# 5. Start Bookmap → Add indicator → "OrderflowRecorder"
```

## Output Format

Each line in `es_orderflow_YYYY-MM-DD.jsonl`:

```json
{
  "seq": 1,
  "ts_event": "2026-05-03T09:30:00.123Z",
  "ts_recv": "2026-05-03T09:30:00.124Z",
  "symbol": "ESM6",
  "event_type": "trade | depth | instrument_added",
  "price": 5300.00,
  "size": 5,
  "side": "bid | ask | buy | sell",
  "bid_price": 5299.75,
  "ask_price": 5300.25,
  "bid_size": 100,
  "ask_size": 80,
  "level": 1,
  "source": "bookmap_l1_api"
}
```

## Ingestion Pipeline

```bash
# Validate JSONL
python scripts/validate_orderflow_export.py \
  --input state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl \
  --source-type real_bookmap_api \
  --output state/orderflow/audit/validation_report.json

# Normalize to Parquet
python scripts/normalize_bookmap_export.py \
  --input state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl \
  --source-type real_bookmap_api \
  --output state/orderflow/2026-05-03/es_orderflow.parquet

# Detect sweeps
python services/orderflow/sweep_detector.py \
  --input state/orderflow/2026-05-03/es_orderflow.parquet \
  --output state/orderflow/es_sweep_events.csv

# Confirm with SPY
python services/orderflow/spy_confirmation.py \
  --es-events state/orderflow/es_sweep_events.json \
  --spy-bars state/live/paper_SPY_1min.csv \
  --output state/orderflow/spy_confirmations.csv

# Full backtest
python mirofish_signal.py backtest-orderflow \
  --ticker SPY --confirm-with ES \
  --orderflow-input state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl \
  --spy-bars state/live/paper_SPY_1min.csv \
  --start 2026-05-01 --end 2026-05-03
```

## Safety

- Read-only add-on. No orders placed.
- Events written in strict order as received from Bookmap.
- `seq` field increments monotonically per session.
- No lookahead leakage in Python pipeline (events processed by `seq` then `ts_event`).