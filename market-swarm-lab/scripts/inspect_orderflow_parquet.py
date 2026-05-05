#!/usr/bin/env python3
"""
inspect_orderflow_parquet.py
Inspect a normalized orderflow Parquet file for replay safety & data quality.

Usage:
    python scripts/inspect_orderflow_parquet.py \
        --input state/orderflow/2026-05-02/es_orderflow.parquet
"""
import argparse
import json
import os
import sys

import pandas as pd
import numpy as np


def inspect(filepath: str) -> dict:
    df = pd.read_parquet(filepath)
    
    # Try to read source_type from adjacent source_audit.json
    source_type = "unknown"
    audit_file = os.path.join(os.path.dirname(filepath), "source_audit.json")
    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r") as f:
                audit = json.load(f)
                source_type = audit.get("source_type", "unknown")
        except Exception:
            pass
    
    report = {
        "file": filepath,
        "source_type": source_type,
        "backtest_valid": source_type in ("real_bookmap_export", "real_bookmap_api"),
        "trading_conclusions_valid": source_type in ("real_bookmap_export", "real_bookmap_api"),
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "null_counts": {c: int(df[c].isna().sum()) for c in df.columns},
        "null_percentages": {},
        "timestamp_range": {},
        "duplicate_timestamps": 0,
        "symbol_consistency": "N/A",
        "replay_safe": False,
        "delta_available": False,
        "liquidity_available": False,
        "summary_stats": {},
    }

    # Null percentages
    for c in df.columns:
        pct = 100.0 * df[c].isna().sum() / max(len(df), 1)
        report["null_percentages"][c] = round(pct, 4)

    # Timestamp analysis
    if "timestamp_ns" in df.columns:
        report["timestamp_range"] = {
            "min_ns": int(df["timestamp_ns"].min()),
            "max_ns": int(df["timestamp_ns"].max()),
            "min_iso": pd.to_datetime(df["timestamp_ns"].min(), unit="ns", utc=True).isoformat(),
            "max_iso": pd.to_datetime(df["timestamp_ns"].max(), unit="ns", utc=True).isoformat(),
            "duration_sec": int((df["timestamp_ns"].max() - df["timestamp_ns"].min()) / 1e9),
        }
        report["duplicate_timestamps"] = int(df["timestamp_ns"].duplicated().sum())
        sorted_asc = df["timestamp_ns"].is_monotonic_increasing
        report["replay_safe"] = bool(
            sorted_asc and report["duplicate_timestamps"] == 0
        )
    else:
        report["errors"] = ["No timestamp_ns column found."]

    # Delta availability
    delta_cols = [c for c in df.columns if "delta" in c.lower()]
    report["delta_available"] = len(delta_cols) > 0
    report["delta_columns"] = delta_cols

    # Liquidity availability
    liq_cols = [c for c in df.columns if any(k in c.lower() for k in ("bid", "ask"))]
    report["liquidity_available"] = len(liq_cols) > 0
    report["liquidity_columns"] = liq_cols

    # Symbol consistency
    if "symbol" in df.columns:
        syms = df["symbol"].dropna().unique()
        report["symbol_consistency"] = {
            "unique_symbols": list(syms),
            "consistent": len(syms) == 1,
        }

    # Numeric summary stats
    for col in ["price", "size", "bid", "ask", "bid_size", "ask_size"]:
        if col in df.columns:
            s = df[col].describe()
            report["summary_stats"][col] = {
                "count": int(s["count"]),
                "mean": float(s["mean"]) if "mean" in s else None,
                "std": float(s["std"]) if "std" in s else None,
                "min": float(s["min"]) if "min" in s else None,
                "max": float(s["max"]) if "max" in s else None,
            }

    return report


def print_report(report: dict):
    print("=" * 60)
    print("PARQUET INSPECTION REPORT")
    print("=" * 60)
    print(f"File:       {report['file']}")
    print(f"Rows:       {report['row_count']}")
    print(f"Columns:    {', '.join(report['columns'])}")
    print(f"Source type:    {report['source_type']}")
    print(f"Backtest valid: {report['backtest_valid']} (requires real_bookmap_export)")
    print(f"Timestamp range:")
    if report.get("timestamp_range"):
        print(f"  {report['timestamp_range'].get('min_iso', 'N/A')}  →  {report['timestamp_range'].get('max_iso', 'N/A')}")
        print(f"  Duration: {report['timestamp_range'].get('duration_sec', 0)} seconds")
    print(f"Duplicate timestamps: {report['duplicate_timestamps']}")
    print(f"Delta available:      {report['delta_available']} ({', '.join(report.get('delta_columns', [])) or 'none'})")
    print(f"Liquidity available:  {report['liquidity_available']} ({', '.join(report.get('liquidity_columns', [])) or 'none'})")
    print(f"Null counts:")
    for col, cnt in report["null_counts"].items():
        pct = report["null_percentages"][col]
        print(f"  {col:20s}: {cnt:8d} ({pct:6.2f}%)")
    if report.get("summary_stats"):
        print(f"Summary stats:")
        for col, stats in report["summary_stats"].items():
            print(f"  {col}: count={stats['count']} min={stats['min']:.2f} max={stats['max']:.2f} mean={stats['mean']:.2f}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = inspect(args.input)
    print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report written: {args.output}")

    sys.exit(0 if report["replay_safe"] else 1)


if __name__ == "__main__":
    main()
