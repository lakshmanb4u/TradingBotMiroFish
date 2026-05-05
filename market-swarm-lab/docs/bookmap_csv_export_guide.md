# Bookmap Official Export Guide — CSV/Text Input Only

> Target: ES futures orderflow (CME E-mini S&P 500)  
> Rule: Only real Bookmap CSV/text exports are valid for trading/replay. Synthetic fixtures are parser tests only.

## Supported Bookmap Export Modes (Official GUI)

Bookmap supports these official export paths (v7.7.0+ macOS, similar paths on Windows):

- **Chart CSV export** — `Right-click chart → Export → CSV`
- **Tape/CSV export** — `File → Export → Text / CSV`
- **Depth snapshot** — `Data → Export Depth → CSV`
- **Trades export** — `Trades panel → Gear icon → Export → CSV`

**Recommended for replay-safe orderflow:** Tape/CSV export (covers trades + depth).

## Recommended Settings (Tape/CSV Export)

Menu path: `File → Export → Text / CSV` (`⌘ + E` on macOS)

| Setting | Recommended value |
|---------|-----------------|
| **Format** | CSV |
| **Delimiter** | Comma (`,`) |
| **Timestamp** | Nanoseconds since epoch (UTC) |
| **Include headers** | ✅ Checked |
| **Include depth** | ✅ Checked (for sweep detection) |
| **Include trades** | ✅ Checked |
| **Timezone** | UTC |
| **Decimal precision** | ≥ 2 places (futures tick sizes) |

Before exporting, go to `Preferences → Time` and set:
- Timezone: `UTC`
- Timestamp format: `Nanoseconds since Unix epoch`
- Display: `YYYY-MM-DD HH:MM:SS.nnnnnnnnn` (for visual verification)

## Required Columns (check ALL in export dialog)

Replay-safe ingestion needs at least these columns:

- `timestamp_ns` — event timestamp, nanosecond epoch, strictly UTC
- `price` — trade price or quote mid
- `size` — trade size or quote size
- `side` — `bid` / `ask` / `unknown`
- `type` — `trade` / `quote` / `depth`

Liquidity / depth (for orderflow analysis):
- `bid_price_1`, `bid_size_1` — best bid
- `ask_price_1`, `ask_size_1` — best ask
- `bid_price_2`–`bid_price_5` / `ask_price_2`–`ask_price_5` — depth ladder

Optional:
- `volume` — cumulative volume (if available)
- `delta` — bid/ask delta (if available in your tier)
- `aggressor` — who initiated (buyer/seller)
- `instrument` — symbol (e.g. `ESM6`)

## Save Convention After Export

Place the exported file in:
```
state/orderflow/raw/YYYY-MM-DD/
```

Example filename:
```
es_orderflow_YYYYMMDD.csv
```

## What NOT to Do

- ❌ Do not export `.bmf` for ingestion — it is proprietary and unsupported for parsing.
- ❌ Do not mix multiple symbols/instruments in one file.
- ❌ Do not use local timestamps — always export UTC.
- ❌ Do not omit depth columns if you need sweep/reclaim detection.
- ❌ Do not process the file before verifying timestamps are monotonically increasing.

## Replay Safety Checks After Export

Open the CSV and confirm:
- Timestamps are 17-digit epoch nanosecond integers (e.g. `1777752000000000000`).
- Timestamps are strictly increasing (no backward jumps).
- First/last timestamp matches the intended session.
- No empty leading rows (Bookmap sometimes adds header padding with `0` timestamps).
- One symbol only.

Then run the automated pipeline:
```bash
python scripts/validate_orderflow_export.py \
  --input state/orderflow/raw/YYYYMMDD/es_orderflow_YYYYMMDD.csv \
  --source-type real_bookmap_export \
  --output state/orderflow/audit/validation_report.json

python scripts/normalize_bookmap_export.py \
  --input state/orderflow/raw/YYYYMMDD/es_orderflow_YYYYMMDD.csv \
  --source-type real_bookmap_export \
  --output state/orderflow/YYYYMMDD/es_orderflow.parquet

python scripts/inspect_orderflow_parquet.py \
  --input state/orderflow/YYYYMMDD/es_orderflow.parquet

python scripts/replay_smoke_test.py \
  --input state/orderflow/YYYYMMDD/es_orderflow.parquet
```

## What `source_type` Means

Every script accepts `--source-type`:
- `real_bookmap_export` — actual GUI CSV/text export. Pipeline is valid for trading/replay.
- `synthetic_test_fixture` — fake data for parser tests only. Does **not** validate backtest logic or strategy claims.

If you run with `synthetic_test_fixture`, the pipeline can still be executed to prove parsers work, but audit reports will be labeled with:
```json
{
  "source_type": "synthetic_test_fixture",
  "backtest_valid": false,
  "trading_conclusions_valid": false
}
```
