#!/usr/bin/env python3
"""
validate_orderflow_export.py
Validate a raw Bookmap (or generic) orderflow CSV/JSON/JSONL export for replay safety.

Usage:
    python scripts/validate_orderflow_export.py \
        --input state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl \
        --source-type real_bookmap_api \
        --output state/orderflow/audit/validation_report.json
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def detect_format(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    if ext in (".csv", ".txt"):
        return "csv"
    if ext == ".jsonl":
        return "jsonl"
    if ext in (".json",):
        return "json"
    if ext in (".bmf", ".bml"):
        return "bookmap_binary"
    raise ValueError(f"Unsupported format: {ext}")


def load_csv_rows(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def load_jsonl_rows(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARNING: Skip malformed JSONL line {i}: {e}")


def detect_timestamp_precision(timestamps_ns: list) -> str:
    if not timestamps_ns:
        return "unknown"
    sample = timestamps_ns[0]
    s = str(int(sample))
    length = len(s)
    if length == 10:
        return "seconds"
    if length == 13:
        return "milliseconds"
    if length == 16:
        return "microseconds"
    if length >= 19:
        return "nanoseconds"
    return "unknown"


def validate(filepath: str, source_type: str = "synthetic_test_fixture") -> dict:
    fmt = detect_format(filepath)
    report = {
        "file": filepath,
        "format": fmt,
        "source_type": source_type,
        "supported": fmt not in ("bookmap_binary",),
        "row_count": 0,
        "columns": [],
        "inferred_schema": {},
        "timestamp_precision": None,
        "timestamp_sort": None,
        "duplicates": 0,
        "nulls": {},
        "malformed_rows": 0,
        "replay_safe": False,
        "liquidity_fields": [],
        "volume_fields": [],
        "warnings": [],
        "errors": [],
    }

    if not report["supported"]:
        report["errors"].append(
            f"Format '{fmt}' not supported. Use official Bookmap CSV export or API workflow."
        )
        return report

    if fmt == "csv":
        rows = list(load_csv_rows(filepath))
    elif fmt in ("json", "jsonl"):
        rows = list(load_jsonl_rows(filepath))
    else:
        report["errors"].append(f"Format '{fmt}' not implemented.")
        return report

    report["row_count"] = len(rows)
    if not rows:
        report["errors"].append("No rows found in file.")
        return report

    report["columns"] = list(rows[0].keys())
    report["inferred_schema"] = {k: type(v).__name__ for k, v in rows[0].items()}

    # Normalize column names
    remap = {}
    for col in report["columns"]:
        if isinstance(col, str):
            remap[col] = col.lower().replace(" ", "_")
        else:
            remap[col] = str(col).lower()

    ts_col = None
    for col in report["columns"]:
        lc = remap[col]
        if any(k in lc for k in ("time", "timestamp", "ts", "epoch", "date")):
            ts_col = col
            break

    price_col = None
    for col in report["columns"]:
        lc = remap[col]
        if any(k == lc for k in ("price", "last", "trade_price", "close")):
            price_col = col
            break

    size_col = None
    for col in report["columns"]:
        lc = remap[col]
        if any(k in lc for k in ("size", "volume", "qty", "quantity", "amount")):
            size_col = col
            break

    if ts_col is None:
        report["errors"].append("No timestamp column detected.")

    # Extract timestamps
    timestamps = []
    if ts_col:
        for i, row in enumerate(rows):
            val = row.get(ts_col)
            if val is None:
                val = ""
            val = str(val).strip()
            try:
                ts = float(val)
            except (ValueError, TypeError):
                # ISO8601?
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    ts = None
            if ts is not None:
                timestamps.append(ts)
            else:
                report["malformed_rows"] += 1
                report["warnings"].append(f"Row {i}: invalid timestamp '{val}'")

    report["timestamp_precision"] = detect_timestamp_precision(timestamps)

    # Sort check
    if len(timestamps) > 1:
        report["timestamp_sort"] = "ascending" if all(
            timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1)
        ) else "non-monotonic"
        report["duplicates"] = sum(
            1 for i in range(len(timestamps) - 1) if timestamps[i] == timestamps[i + 1]
        )

    # Null check
    for col in report["columns"]:
        null_count = sum(1 for row in rows if row.get(col) is None or str(row.get(col)).strip() == "")
        if null_count:
            report["nulls"][col] = null_count

    # Liquidity / depth field detection
    depth_keywords = ["bid", "ask", "bid_price", "ask_price", "bid_size", "ask_size", "depth", "level"]
    for col in report["columns"]:
        lc = remap[col]
        if any(k in lc for k in depth_keywords):
            report["liquidity_fields"].append(col)

    vol_keywords = ["volume", "delta", "trade_size", "tick_volume", "cum_vol"]
    for col in report["columns"]:
        lc = remap[col]
        if any(k in lc for k in vol_keywords):
            report["volume_fields"].append(col)

    # Replay safety verdict
    report["replay_safe"] = bool(
        ts_col is not None
        and price_col is not None
        and size_col is not None
        and report["timestamp_sort"] == "ascending"
        and report["duplicates"] == 0
        and report["malformed_rows"] == 0
    )

    return report


def main():
    parser = argparse.ArgumentParser(description="Validate orderflow export for replay safety.")
    parser.add_argument("--input", required=True, help="Path to CSV/JSON/JSONL export")
    parser.add_argument("--source-type", default="synthetic_test_fixture",
                        choices=["real_bookmap_export", "real_bookmap_api", "synthetic_test_fixture"],
                        help="Source type label for audit.")
    parser.add_argument("--output", default=None, help="Path to write validation report JSON")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    report = validate(args.input, args.source_type)

    print("=" * 60)
    print("ORDERFLOW EXPORT VALIDATION REPORT")
    print("=" * 60)
    print(f"File:      {report['file']}")
    print(f"Format:    {report['format']}")
    print(f"Supported: {report['supported']}")
    print(f"Source:    {report['source_type']}")
    print(f"Rows:      {report['row_count']}")
    print(f"Columns:   {', '.join(report['columns'])}")
    print(f"Timestamp: {report['timestamp_precision']} ({'OK' if report['timestamp_sort'] == 'ascending' else 'FAIL - ' + str(report['timestamp_sort'])})")
    print(f"Liquidity: {', '.join(report['liquidity_fields']) or 'none'}")
    print(f"Volume:    {', '.join(report['volume_fields']) or 'none'}")
    print(f"Duplicates:{report['duplicates']}")
    print(f"Malformed: {report['malformed_rows']}")
    print(f"Nulls:     {report['nulls']}")
    print(f"REPLAY SAFE: {'YES' if report['replay_safe'] else 'NO'}")
    print(f"BACKTEST VALID: {'YES' if report['source_type'] in ('real_bookmap_export', 'real_bookmap_api') else 'NO (requires real_bookmap_export or real_bookmap_api)'}")
    if report["warnings"]:
        print("\nWarnings:")
        for w in report["warnings"][:10]:
            print(f"  - {w}")
    if report["errors"]:
        print("\nErrors:")
        for e in report["errors"]:
            print(f"  - {e}")
    print("=" * 60)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to: {args.output}")

    sys.exit(0 if report["replay_safe"] else 1)


if __name__ == "__main__":
    main()
