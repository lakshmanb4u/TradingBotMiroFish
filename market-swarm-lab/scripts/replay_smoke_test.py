#!/usr/bin/env python3
"""
replay_smoke_test.py
Load normalized parquet and verify replay ordering with no lookahead leakage.

Usage:
    python scripts/replay_smoke_test.py \
        --input state/orderflow/2026-05-02/es_orderflow.parquet
"""
import argparse
import sys

import pandas as pd


def smoke_test(filepath: str) -> dict:
    df = pd.read_parquet(filepath)

    report = {
        "file": filepath,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "passthroughs": [],
        "errors": [],
    }

    if "timestamp_ns" not in df.columns:
        report["errors"].append("Missing timestamp_ns column.")
        return report

    if "price" not in df.columns:
        report["errors"].append("Missing price column.")
        return report

    # Verify monotonic timestamps
    mono = df["timestamp_ns"].is_monotonic_increasing
    report["monotonic_timestamps"] = mono
    if not mono:
        diff = df["timestamp_ns"].diff()
        bad = diff[diff < 0]
        report["errors"].append(
            f"Non-monotonic timestamps at {len(bad)} positions (first bad index: {bad.index[0] if len(bad) else 'N/A'})"
        )
    else:
        report["passthroughs"].append("Timestamps are monotonically increasing.")

    # Verify no duplicate timestamps
    dups = df["timestamp_ns"].duplicated().sum()
    report["duplicate_count"] = int(dups)
    if dups > 0:
        report["errors"].append(f"{dups} duplicate timestamps found.")
    else:
        report["passthroughs"].append("No duplicate timestamps.")

    # Verify no NaNs in required columns
    for col in ["timestamp_ns", "price"]:
        nulls = df[col].isna().sum()
        if nulls > 0:
            report["errors"].append(f"{col} has {nulls} null values.")
        else:
            report["passthroughs"].append(f"{col} has no nulls.")

    # Verify forward-only iteration (no lookahead)
    # This test simulates event-by-event processing
    max_lookback = 0
    last_ts = None
    for ts in df["timestamp_ns"]:
        if last_ts is not None and ts < last_ts:
            max_lookback = max(max_lookback, last_ts - ts)
        last_ts = ts
    report["max_lookback_ns"] = int(max_lookback)
    if max_lookback == 0:
        report["passthroughs"].append("Zero lookback: strictly forward replay confirmed.")
    else:
        report["errors"].append(f"Lookback detected: {max_lookback} ns (should be 0).")

    report["replay_safe"] = len(report["errors"]) == 0
    return report


def print_report(report: dict):
    print("=" * 60)
    print("REPLAY SMOKE TEST")
    print("=" * 60)
    print(f"File:  {report['file']}")
    print(f"Rows:  {report['rows']}")
    print(f"Monotonic: {report.get('monotonic_timestamps', 'N/A')}")
    print(f"Duplicates: {report.get('duplicate_count', 'N/A')}")
    print(f"Max lookback: {report.get('max_lookback_ns', 'N/A')} ns")
    print()
    for p in report["passthroughs"]:
        print(f"  ✅ {p}")
    for e in report["errors"]:
        print(f"  ❌ {e}")
    print()
    verdict = "REPLAY SAFE" if report["replay_safe"] else "REPLAY UNSAFE"
    print(f"Verdict: {verdict}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = smoke_test(args.input)
    print_report(report)

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)

    sys.exit(0 if report["replay_safe"] else 1)


if __name__ == "__main__":
    main()
