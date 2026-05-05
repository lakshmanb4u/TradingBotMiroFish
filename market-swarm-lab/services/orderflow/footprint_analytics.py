#!/usr/bin/env python3
"""
footprint_analytics.py
Win-rate analytics engine for footprint entry outcomes.

Reads footprint_outcome_tracking.csv and computes:
  - win_rate by confidence bucket (50-60, 61-70, 71-80, 81-90, 91-100)
  - win_rate by setup_type (absorption_long, absorption_short, divergence_long, divergence_short)
  - win_rate by session (open/10-12/12-14/close)
  - avg MAE and MFE by setup
  - best confidence threshold (max EXPECTED_VALUE)

Writes brief: state/orderflow/live/footprint_analytics.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any


def parse_ts(ts_str: str) -> datetime:
    try:
        s = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def get_session_label(ts_str: str) -> str:
    """Map UTC timestamp to ET session bucket."""
    dt = parse_ts(ts_str)
    # Convert to ET (UTC-4)
    et = dt - timedelta(hours=4)
    hour = et.hour
    minute = et.minute
    time_val = hour * 100 + minute
    if time_val < 1000:
        return "open"
    elif 1000 <= time_val < 1200:
        return "10-12"
    elif 1200 <= time_val < 1400:
        return "12-14"
    else:
        return "close"


def compute_win_rate(rows: List[Dict], condition_fn) -> Dict[str, Any]:
    """Compute win rate for rows matching condition."""
    matches = [r for r in rows if condition_fn(r)]
    if not matches:
        return {"count": 0, "win_rate": None, "avg_rr": None}
    # Win = target1 hit before stop at any lookahead
    wins = 0
    rrs = []
    for r in matches:
        # Check across all lookaheads
        win = False
        rr = 0.0
        for m in [1, 5, 15, 30]:
            t1_hit = r.get(f"target1_hit_{m}m", "NO") == "YES"
            stop_hit = r.get(f"stop_hit_{m}m", "NO") == "YES"
            if t1_hit and not stop_hit:
                win = True
            try:
                rr_val = float(r.get(f"rr_{m}m", 0))
            except Exception:
                rr_val = 0.0
            if rr_val > rr:
                rr = rr_val
        if win:
            wins += 1
        rrs.append(rr)
    return {
        "count": len(matches),
        "win_rate": round(wins / len(matches) * 100, 1),
        "avg_rr": round(sum(rrs) / len(rrs), 2) if rrs else 0.0,
    }


def run_analytics(outcome_csv: str, output_json: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print(f"  Footprint Analytics Engine")
    print(f"{'='*60}")
    print(f"  Input:  {outcome_csv}")
    print(f"  Output: {output_json}")
    print(f"{'='*60}\n")

    rows = []
    with open(outcome_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Loaded {len(rows)} outcome rows")
    if not rows:
        return {"error": "No outcome data"}

    # --- Confidence bucket analysis ---
    conf_buckets = {
        "50-60": lambda r: 50 <= float(r.get("confidence", 0)) <= 60,
        "61-70": lambda r: 61 <= float(r.get("confidence", 0)) <= 70,
        "71-80": lambda r: 71 <= float(r.get("confidence", 0)) <= 80,
        "81-90": lambda r: 81 <= float(r.get("confidence", 0)) <= 90,
        "91-100": lambda r: 91 <= float(r.get("confidence", 0)) <= 100,
    }
    win_by_conf = {}
    for bucket, fn in conf_buckets.items():
        win_by_conf[bucket] = compute_win_rate(rows, fn)

    # --- Setup type analysis ---
    setup_types = ["absorption_long", "absorption_short", "divergence_long", "divergence_short"]
    win_by_setup = {}
    for st in setup_types:
        def _make_fn(s):
            return lambda r: s in r.get("setup_type", "")
        win_by_setup[st] = compute_win_rate(rows, _make_fn(st))

    # --- Session analysis ---
    session_buckets = ["open", "10-12", "12-14", "close"]
    win_by_session = {}
    for sess in session_buckets:
        def _make_sess_fn(s):
            return lambda r: get_session_label(r.get("ts_event", "")) == s
        win_by_session[sess] = compute_win_rate(rows, _make_sess_fn(sess))

    # --- MAE / MFE by setup ---
    mae_mfe_by_setup: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mae_sum": 0.0, "mfe_sum": 0.0, "count": 0})
    for r in rows:
        st = r.get("setup_type", "")
        # Use best (30m) lookahaead
        try:
            mae = float(r.get("mae_30m", 0))
            mfe = float(r.get("mfe_30m", 0))
        except Exception:
            mae = 0.0
            mfe = 0.0
        if mae > 0 or mfe > 0:
            mae_mfe_by_setup[st]["mae_sum"] += mae
            mae_mfe_by_setup[st]["mfe_sum"] += mfe
            mae_mfe_by_setup[st]["count"] += 1

    avg_mae_mfe = {}
    for st, vals in mae_mfe_by_setup.items():
        c = vals["count"]
        if c > 0:
            avg_mae_mfe[st] = {
                "avg_mae": round(vals["mae_sum"] / c, 2),
                "avg_mfe": round(vals["mfe_sum"] / c, 2),
                "count": c,
            }

    # --- Best confidence threshold via EXPECTED_VALUE ---
    # EV = P(win) * avg_rr - P(loss) * avg_mae
    best_threshold = {"threshold": 0, "expected_value": -999.0, "win_rate": 0.0, "count": 0}
    for threshold in range(50, 100, 5):
        subset = [r for r in rows if float(r.get("confidence", 0)) >= threshold]
        if not subset:
            continue
        wins = 0
        rr_sum = 0.0
        mae_sum = 0.0
        for r in subset:
            win = False
            rr = 0.0
            mae = 0.0
            for m in [1, 5, 15, 30]:
                t1_hit = r.get(f"target1_hit_{m}m", "NO") == "YES"
                stop_hit = r.get(f"stop_hit_{m}m", "NO") == "YES"
                if t1_hit and not stop_hit:
                    win = True
                try:
                    rr_val = float(r.get(f"rr_{m}m", 0))
                except Exception:
                    rr_val = 0.0
                if rr_val > rr:
                    rr = rr_val
                try:
                    mae_val = float(r.get(f"mae_{m}m", 0))
                except Exception:
                    mae_val = 0.0
                if mae_val > mae:
                    mae = mae_val
            if win:
                wins += 1
            rr_sum += rr
            mae_sum += mae
        p_win = wins / len(subset)
        p_loss = 1 - p_win
        avg_rr = rr_sum / len(subset) if subset else 0.0
        avg_mae_loss = mae_sum / len(subset) if subset else 0.0
        ev = p_win * avg_rr - p_loss * avg_mae_loss
        if ev > best_threshold["expected_value"]:
            best_threshold = {
                "threshold": threshold,
                "expected_value": round(ev, 3),
                "win_rate": round(p_win * 100, 1),
                "count": len(subset),
                "avg_rr": round(avg_rr, 2),
                "avg_mae": round(avg_mae_loss, 2),
            }

    # --- Assemble report ---
    report = {
        "ts_generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_candidates": len(rows),
        "win_rate_by_confidence_bucket": win_by_conf,
        "win_rate_by_setup_type": win_by_setup,
        "win_rate_by_session": win_by_session,
        "avg_mae_mfe_by_setup": avg_mae_mfe,
        "best_confidence_threshold": best_threshold,
    }

    # Write JSON
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nAnalytics written to {output_json}")
    print(f"  Total candidates: {len(rows)}")
    print(f"  Best threshold:   {best_threshold['threshold']}+ (EV={best_threshold['expected_value']})")
    print(f"  Best win rate:    {best_threshold['win_rate']}%")
    return report


def main():
    parser = argparse.ArgumentParser(description="Footprint Analytics Engine")
    parser.add_argument("--outcome-csv", default="state/orderflow/live/footprint_outcome_tracking.csv")
    parser.add_argument("--output-json", default="state/orderflow/live/footprint_analytics.json")
    args = parser.parse_args()
    run_analytics(args.outcome_csv, args.output_json)


if __name__ == "__main__":
    main()
