#!/usr/bin/env python3
"""
outcome_tracker.py
Forward-looking outcome tracker for footprint entry candidates.

Evaluates each footprint candidate at +1, +5, +15, +30 minutes by reading
JSONL continuation. Computes MAE, MFE, RR achieved, stop/target hits, exit price.

Usage:
  python services/orderflow/outcome_tracker.py \
      --jsonl state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl \
      --candidates state/orderflow/live/footprint_entry_candidates.csv \
      --output state/orderflow/live/footprint_outcome_tracking.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any

ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


LOOKAHEAD_MINUTES = [1, 5, 15, 30]


def parse_ts(ts_str: str) -> Optional[datetime]:
    try:
        s = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_jsonl_events(jsonl_path: str, after_ts: datetime, before_ts: datetime, symbol_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load trade events from JSONL within a timestamp window."""
    events = []
    path = Path(jsonl_path)
    if not path.exists():
        return events
    # Fast path: skip far earlier events by seeking from end for large files
    import os
    file_size = path.stat().st_size
    if file_size < 50 * 1024 * 1024:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") != "trade":
                    continue
                if symbol_filter and not ev.get("symbol", "").startswith(symbol_filter):
                    continue
                ts = parse_ts(ev.get("ts_event", ""))
                if ts is None:
                    continue
                if after_ts <= ts <= before_ts:
                    events.append(ev)
                elif ts > before_ts:
                    break
        return events
    # Large file: seek from end using binary block reads
    block_size = 8 * 1024 * 1024
    with open(path, "rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        buf = b""
        while remaining > 0:
            sz = min(block_size, remaining)
            remaining -= sz
            f.seek(remaining)
            chunk = f.read(sz)
            buf = chunk + buf
            lines = buf.split(b"\n")
            buf = lines.pop(0) if remaining > 0 else b""
            # Process lines in reverse order
            processed_any = False
            for lb in reversed(lines):
                line = lb.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") != "trade":
                    continue
                if symbol_filter and not ev.get("symbol", "").startswith(symbol_filter):
                    continue
                ts = parse_ts(ev.get("ts_event", ""))
                if ts is None:
                    continue
                if ts > before_ts:
                    continue
                if ts < after_ts:
                    processed_any = True
                    break
                events.append(ev)
            if processed_any:
                break
    events.reverse()
    return events


def infer_tick_size(price: float) -> float:
    if price > 10000:
        return NQ_TICK_SIZE
    return ES_TICK_SIZE


def evaluate_candidate(
    candidate: Dict[str, Any],
    jsonl_path: str,
    symbol_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate a single footprint candidate forward in time.
    Returns a row dict suitable for the outcome CSV.
    """
    ts_event = parse_ts(candidate.get("ts_event", ""))
    if ts_event is None:
        return {}

    direction = candidate.get("direction", "")
    entry = float(candidate.get("entry_price", 0))
    stop = float(candidate.get("stop", 0))
    # stop is not in footprint CSV, compute approx from level
    trigger_level = float(candidate.get("trigger_level", 0))
    tick_size = infer_tick_size(entry)

    # Derive targets if not present
    t1 = float(candidate.get("target_1", 0))
    t2 = float(candidate.get("target_2", 0))
    if t1 == 0 and direction == "LONG":
        t1 = entry + abs(entry - trigger_level) * 1.0
        t2 = entry + abs(entry - trigger_level) * 2.0
    elif t1 == 0 and direction == "SHORT":
        t1 = entry - abs(trigger_level - entry) * 1.0
        t2 = entry - abs(trigger_level - entry) * 2.0

    # Compute approximate stop from trigger level (footprint signals don't have explicit stop)
    if stop == 0:
        if direction == "LONG":
            stop = trigger_level - tick_size * 2
        else:
            stop = trigger_level + tick_size * 2

    setup_type = candidate.get("setup_type", "")
    confidence = float(candidate.get("confidence", 0))

    result = {
        "ts_event": candidate.get("ts_event", ""),
        "direction": direction,
        "entry_price": entry,
        "stop": stop,
        "target_1": t1,
        "target_2": t2,
        "trigger_level": trigger_level,
        "setup_type": setup_type,
        "confidence": confidence,
    }

    for minutes in LOOKAHEAD_MINUTES:
        until = ts_event + timedelta(minutes=minutes)
        events = load_jsonl_events(jsonl_path, ts_event, until, symbol_filter=symbol_filter)

        if not events:
            result[f"exit_price_{minutes}m"] = ""
            result[f"mfe_{minutes}m"] = ""
            result[f"mae_{minutes}m"] = ""
            result[f"rr_{minutes}m"] = ""
            result[f"stop_hit_{minutes}m"] = ""
            result[f"target1_hit_{minutes}m"] = ""
            result[f"target2_hit_{minutes}m"] = ""
            continue

        prices = [ev.get("price", 0.0) for ev in events if ev.get("price")]
        if not prices:
            # Try closing from the candidate candle
            exit_price = entry
        else:
            exit_price = prices[-1]

        if direction == "LONG":
            mfe = max(max(prices) - entry, 0.0) if prices else 0.0
            mae = max(entry - min(prices), 0.0) if prices else 0.0
            stop_hit = any(p <= stop for p in prices)
            target1_hit = any(p >= t1 for p in prices)
            target2_hit = any(p >= t2 for p in prices)
        else:  # SHORT
            mfe = max(entry - min(prices), 0.0) if prices else 0.0
            mae = max(max(prices) - entry, 0.0) if prices else 0.0
            stop_hit = any(p >= stop for p in prices)
            target1_hit = any(p <= t1 for p in prices)
            target2_hit = any(p <= t2 for p in prices)

        rr = mfe / mae if mae > 0 else (mfe / tick_size if tick_size > 0 else 0.0)

        result[f"exit_price_{minutes}m"] = round(exit_price, 2)
        result[f"mfe_{minutes}m"] = round(mfe, 2)
        result[f"mae_{minutes}m"] = round(mae, 2)
        result[f"rr_{minutes}m"] = round(rr, 2)
        result[f"stop_hit_{minutes}m"] = "YES" if stop_hit else "NO"
        result[f"target1_hit_{minutes}m"] = "YES" if target1_hit else "NO"
        result[f"target2_hit_{minutes}m"] = "YES" if target2_hit else "NO"

    return result


def run_outcome_tracker(
    jsonl_path: str,
    candidates_csv: str,
    output_csv: str,
    symbol_filter: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> None:
    """Run the full outcome tracker pipeline."""
    print(f"\n{'='*60}")
    print(f"  Footprint Outcome Tracker")
    print(f"{'='*60}")
    print(f"  JSONL: {jsonl_path}")
    print(f"  Candidates: {candidates_csv}")
    print(f"  Output: {output_csv}")
    print(f"{'='*60}\n")

    # Load candidates
    candidates = []
    with open(candidates_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candidates.append(row)

    if max_candidates:
        candidates = candidates[:max_candidates]

    print(f"Loaded {len(candidates)} candidates")

    # Evaluate each
    results = []
    for i, cand in enumerate(candidates, 1):
        if i % 50 == 0:
            print(f"  Evaluated {i}/{len(candidates)}...")
        out = evaluate_candidate(cand, jsonl_path, symbol_filter=symbol_filter)
        if out:
            results.append(out)

    # Write CSV
    if not results:
        print("No results to write.")
        return

    fieldnames = list(results[0].keys())
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    print(f"\nWrote {len(results)} rows to {output_csv}")


def main():
    parser = argparse.ArgumentParser(description="Footprint Outcome Tracker")
    parser.add_argument("--jsonl", default="state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")
    parser.add_argument("--candidates", default="state/orderflow/live/footprint_entry_candidates.csv")
    parser.add_argument("--output", default="state/orderflow/live/footprint_outcome_tracking.csv")
    parser.add_argument("--symbol-filter", default="ES")
    parser.add_argument("--max-candidates", type=int, default=None)
    args = parser.parse_args()

    run_outcome_tracker(
        jsonl_path=args.jsonl,
        candidates_csv=args.candidates,
        output_csv=args.output,
        symbol_filter=args.symbol_filter,
        max_candidates=args.max_candidates,
    )


if __name__ == "__main__":
    main()
