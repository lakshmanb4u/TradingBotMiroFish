#!/bin/bash
set -e
cd "$(dirname "$0")"
INPUT="state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl"
OUTDIR="state/orderflow/2026-05-03"
mkdir -p "$OUTDIR" state/orderflow/audit

echo "=== 1. Validate JSONL ==="
python3 scripts/validate_orderflow_export.py \
  --input "$INPUT" \
  --source-type real_bookmap_api \
  --output state/orderflow/audit/api_validation_report.json

echo "=== 2. Normalize → Parquet ==="
python3 scripts/normalize_bookmap_export.py \
  --input "$INPUT" \
  --source-type real_bookmap_api \
  --output "$OUTDIR/es_orderflow.parquet"

echo "=== 3. Inspect Parquet ==="
python3 scripts/inspect_orderflow_parquet.py \
  --input "$OUTDIR/es_orderflow.parquet" \
  --output state/orderflow/audit/api_parquet_summary.json

echo "=== 4. Replay Smoke Test ==="
python3 scripts/replay_smoke_test.py \
  --input "$OUTDIR/es_orderflow.parquet" \
  --output state/orderflow/audit/api_replay_safety_report.json

echo "=== Done ==="
