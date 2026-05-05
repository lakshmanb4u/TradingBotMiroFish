#!/usr/bin/env python3
"""
replay_sweep_detector.py
Replay orderflow Parquet through the simple sweep detector and emit events.

Usage:
    python scripts/replay_sweep_detector.py \
        --input state/orderflow/2026-05-02/es_orderflow.parquet \
        --lookback 10 \
        --output-sweeps state/orderflow/2026-05-02/sweep_events.csv \
        --output-summary state/orderflow/2026-05-02/replay_summary.md
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))
from simple_sweep_detector import SimpleSweepDetector


def summarize(events_df: pd.DataFrame, input_path: str, lookback: int) -> str:
    lines = [
        "# Sweep Detector Replay Summary",
        "",
        f"**Input:** `{input_path}`",
        f"**Lookback bars:** {lookback}",
        f"**Total sweep events:** {len(events_df)}",
        "",
    ]

    if events_df.empty:
        lines.append("No sweep events detected.")
        return "\n".join(lines)

    bullish = events_df[events_df["direction"] == "bullish"]
    bearish = events_df[events_df["direction"] == "bearish"]

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Bullish sweeps | {len(bullish)} |")
    lines.append(f"| Bearish sweeps | {len(bearish)} |")
    lines.append(f"| Avg sweep distance | {events_df['sweep_distance'].mean():.4f} |")
    lines.append(f"| Max sweep distance | {events_df['sweep_distance'].max():.4f} |")
    lines.append(f"| Liquidity behaviors | {events_df['liquidity_behavior'].value_counts().to_dict()} |")
    lines.append(f"| Confidence distribution | {events_df['confidence'].value_counts().to_dict()} |")
    lines.append("")

    lines.append("## Events")
    lines.append("")
    lines.append("| timestamp_ns | level | direction | sweep_distance | reclaim_delay_ns | liquidity_behavior | confidence |")
    lines.append("|-------------|-------|-----------|---------------|------------------|--------------------|------------|")
    for _, row in events_df.head(50).iterrows():
        lines.append(
            f"| {int(row['timestamp_ns'])} | {row['level']:.4f} | {row['direction']} | "
            f"{row['sweep_distance']:.4f} | {int(row['reclaim_delay_ns'])} | "
            f"{row['liquidity_behavior']} | {row['confidence']} |"
        )
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--lookback", type=int, default=10)
    parser.add_argument("--output-sweeps", default=None)
    parser.add_argument("--output-summary", default=None)
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_parquet(args.input)
    print(f"Loaded {len(df)} rows from {args.input}")

    detector = SimpleSweepDetector(lookback_bars=args.lookback)
    events = detector.run(df)
    events_df = detector.to_dataframe()

    print(f"Detected {len(events_df)} sweep events.")

    if args.output_sweeps:
        os.makedirs(os.path.dirname(args.output_sweeps) or ".", exist_ok=True)
        events_df.to_csv(args.output_sweeps, index=False)
        print(f"Sweep events written: {args.output_sweeps}")

    if args.output_summary:
        os.makedirs(os.path.dirname(args.output_summary) or ".", exist_ok=True)
        summary = summarize(events_df, args.input, args.lookback)
        with open(args.output_summary, "w") as f:
            f.write(summary)
        print(f"Summary written: {args.output_summary}")


if __name__ == "__main__":
    main()
