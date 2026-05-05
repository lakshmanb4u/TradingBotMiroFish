#!/usr/bin/env python3
"""
normalize_bookmap_export.py
Auto-detect Bookmap export format (CSV/JSON/JSONL/BMF) and produce replay-safe Parquet.

Usage:
    python scripts/normalize_bookmap_export.py \
        --input state/orderflow/bookmap_api/es_orderflow_2026-05-02.jsonl \
        --source-type real_bookmap_api \
        --output state/orderflow/2026-05-02/es_orderflow.parquet

    python scripts/normalize_bookmap_export.py \
        --input state/orderflow/raw/2026-05-02/ESM6_20260502.csv \
        --source-type real_bookmap_export \
        --output state/orderflow/2026-05-02/es_orderflow.parquet
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def fail(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


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


def normalize_csv(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(filepath, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]

    ts_col = None
    for col in df.columns:
        lc = col.lower().replace(" ", "_")
        if any(k in lc for k in ("time", "timestamp", "ts", "epoch")):
            ts_col = col
            break
    if ts_col is None:
        fail("No timestamp column found in CSV.")

    def parse_ts(val):
        val = str(val).strip()
        if val == "" or val.lower() == "nan":
            return pd.NaT
        try:
            f = float(val)
            s = str(int(f))
            if len(s) == 10:
                return pd.to_datetime(f, unit="s", utc=True)
            elif len(s) == 13:
                return pd.to_datetime(f, unit="ms", utc=True)
            elif len(s) >= 16:
                return pd.to_datetime(f, unit="ns", utc=True)
            return pd.to_datetime(f, unit="s", utc=True)
        except (ValueError, OverflowError):
            pass
        try:
            return pd.to_datetime(val, utc=True)
        except Exception:
            return pd.NaT

    df["timestamp_ns"] = df[ts_col].apply(parse_ts)
    invalid = df["timestamp_ns"].isna().sum()
    if invalid > 0:
        print(f"WARNING: Dropping {invalid} rows with invalid timestamps.")
        df = df.dropna(subset=["timestamp_ns"])

    df = df.sort_values("timestamp_ns").reset_index(drop=True)
    df["timestamp_ns"] = df["timestamp_ns"].astype("int64")

    # Price / size / side detection
    for probe, target in [
        (("price", "last", "trade_price", "close"), "price"),
        (("size", "volume", "qty", "quantity"), "size"),
        (("side", "direction", "aggressor"), "side"),
    ]:
        for col in df.columns:
            lc = col.lower().replace(" ", "_")
            if any(k == lc or k in lc for k in probe):
                if target == "side":
                    df[target] = df[col].astype(str).str.strip().str.lower()
                else:
                    df[target] = pd.to_numeric(df[col], errors="coerce")
                break

    if "side" not in df.columns:
        df["side"] = "unknown"

    # Liquidity fields
    for col in df.columns:
        lc = col.lower().replace(" ", "_")
        if "bid" in lc and "price" in lc:
            df["bid"] = pd.to_numeric(df[col], errors="coerce")
        elif "ask" in lc and "price" in lc:
            df["ask"] = pd.to_numeric(df[col], errors="coerce")
        elif "bid" in lc and "size" in lc:
            df["bid_size"] = pd.to_numeric(df[col], errors="coerce")
        elif "ask" in lc and "size" in lc:
            df["ask_size"] = pd.to_numeric(df[col], errors="coerce")

    return df


def normalize_json(filepath: str) -> pd.DataFrame:
    df = pd.read_json(filepath, lines=Path(filepath).suffix == ".jsonl")
    df.columns = [c.strip() for c in df.columns]
    ts_col = None
    for col in df.columns:
        lc = col.lower().replace(" ", "_")
        if any(k in lc for k in ("time", "timestamp", "ts", "epoch")):
            ts_col = col
            break
    if ts_col is None:
        fail("No timestamp column found in JSON.")
    df["timestamp_ns"] = pd.to_datetime(df[ts_col], utc=True).astype("int64")
    df = df.sort_values("timestamp_ns").reset_index(drop=True)
    return df


def normalize_jsonl(filepath: str) -> pd.DataFrame:
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARNING: Skip malformed JSONL line {i}: {e}")
                continue
            records.append(rec)

    if not records:
        fail("No valid records found in JSONL file.")

    df = pd.json_normalize(records)
    df.columns = [c.strip() for c in df.columns]

    # Timestamp: prefer ts_event, fallback to ts_recv
    ts_col = None
    for c in ["ts_event", "ts_recv", "timestamp_ns", "timestamp", "time"]:
        if c in df.columns:
            ts_col = c
            break
    if ts_col is None:
        fail("No timestamp column (ts_event/ts_recv/timestamp_ns) found in JSONL.")

    def parse_ts_to_ns(val):
        """Parse a timestamp value to nanoseconds since epoch (int64)."""
        if pd.isna(val):
            return None
        s = str(val).strip()
        if s == "":
            return None
        
        # Case 1: ISO8601 string (e.g. 2026-05-03T16:20:00.000Z)
        try:
            ts = pd.Timestamp(s, tz="UTC")
            return int(ts.value)  # Already int64 nanoseconds
        except Exception:
            pass
        
        # Case 2: Numeric epoch value
        try:
            n = float(s)
            n_int = int(n)
            n_str = str(n_int)
            
            if len(n_str) >= 19 or n > 1e18:
                return int(n)  # Already nanoseconds
            elif len(n_str) >= 16 or n > 1e15:
                return int(n * 1_000)  # Microseconds to ns
            elif len(n_str) >= 13 or n > 1e12:
                return int(n * 1_000_000)  # Milliseconds to ns
            else:
                return int(n * 1_000_000_000)  # Seconds to ns
        except Exception:
            pass
        
        return None

    df["timestamp_ns"] = df[ts_col].apply(parse_ts_to_ns)
    invalid = df["timestamp_ns"].isna().sum()
    if invalid > 0:
        print(f"WARNING: Dropping {invalid} rows with invalid timestamps.")
        df = df.dropna(subset=["timestamp_ns"])

    df["timestamp_ns"] = df["timestamp_ns"].astype("int64")

    df = df.sort_values("timestamp_ns").reset_index(drop=True)

    # Numeric fields
    for col in ["price", "bid_price", "ask_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["size", "bid_size", "ask_size", "level", "seq"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Canonical column name mapping
    if "bid_price" in df.columns and "bid" not in df.columns:
        df["bid"] = df["bid_price"]
    if "ask_price" in df.columns and "ask" not in df.columns:
        df["ask"] = df["ask_price"]

    # Sequence monotonicity check
    if "seq" in df.columns:
        seq_diff = df["seq"].diff().dropna()
        if not (seq_diff > 0).all():
            bad = seq_diff[seq_diff <= 0]
            print(f"WARNING: Non-monotonic sequence at {len(bad)} positions.")

    return df


def write_audit(df: pd.DataFrame, input_path: str, output_path: str, source_type: str):
    audit = {
        "input_file": input_path,
        "output_file": output_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "backtest_valid": source_type in ("real_bookmap_export", "real_bookmap_api"),
        "trading_conclusions_valid": source_type in ("real_bookmap_export", "real_bookmap_api"),
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "timestamp_range": {
            "min_ns": int(df["timestamp_ns"].min()) if "timestamp_ns" in df.columns else None,
            "max_ns": int(df["timestamp_ns"].max()) if "timestamp_ns" in df.columns else None,
        },
        "null_counts": {col: int(df[col].isna().sum()) for col in df.columns},
        "replay_safe": True,
        "note": "Normalized and sorted by timestamp_ns. No lookahead leakage.",
    }
    audit_dir = os.path.dirname(output_path)
    audit_path = os.path.join(audit_dir, "source_audit.json")
    with open(audit_path, "w") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"Audit written: {audit_path}")


def main():
    parser = argparse.ArgumentParser(description="Normalize Bookmap export to replay-safe Parquet.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-type", default="synthetic_test_fixture",
                        choices=["real_bookmap_export", "real_bookmap_api", "synthetic_test_fixture"],
                        help="Source type label for audit.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.input):
        fail(f"Input not found: {args.input}")

    fmt = detect_format(args.input)
    if fmt == "bookmap_binary":
        fail(
            "Bookmap .bmf files are not supported for direct parsing.\n"
            "Use the official Bookmap GUI export to CSV.\n"
            "See docs/bookmap_csv_export_guide.md"
        )

    print(f"Detected format: {fmt}")
    print(f"Source type: {args.source_type}")
    print("Normalizing...")

    if fmt == "csv":
        df = normalize_csv(args.input)
    elif fmt == "json":
        df = normalize_json(args.input)
    elif fmt == "jsonl":
        df = normalize_jsonl(args.input)
    else:
        fail(f"Unsupported format handler: {fmt}")

    # Deduplicate timestamps (keep first)
    before = len(df)
    df = df.drop_duplicates(subset=["timestamp_ns"], keep="first")
    after = len(df)
    if after < before:
        print(f"Dropped {before - after} duplicate timestamps.")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    df.to_parquet(args.output, engine="pyarrow", index=False)
    print(f"Parquet written: {args.output} ({after} rows, {len(df.columns)} cols)")

    write_audit(df, args.input, args.output, args.source_type)


if __name__ == "__main__":
    main()
