#!/usr/bin/env python3
"""
run_footprint_entry_diagnostics.py
Main diagnostic runner for Reddit-style footprint entry system.

Flow:
  1. Read live JSONL tail (last N trade events)
  2. Build tick-footprint candles via TickFootprintBuilder
  3. Detect marked structural levels
  4. Detect absorption events
  5. Generate entry signals (LONG/SHORT)
  6. Write CSV, MD, JSON diagnostics
  7. Print validation report

Usage:
  python scripts/run_footprint_entry_diagnostics.py --jsonl state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl --tail-events 100000
"""
from __future__ import annotations

import json
import sys
import csv
import math
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any

# Ensure services/ on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "orderflow"))

from tick_footprint_builder import TickFootprintBuilder, TickFootprintCandle, FootprintLadder
from marked_levels import MarkedLevelsDetector, MarkedLevel
from absorption_detector import AbsorptionDetector, AbsorptionEvent
from footprint_entry_signal import (
    FootprintEntrySignalGenerator,
    FootprintEntrySignal,
    CSV_PATH,
    MD_PATH,
    JSON_PATH,
)

DEFAULT_TAIL_EVENTS = 100_000
DEFAULT_TICKS_PER_CANDLE = 20
DEFAULT_MIN_CONFIDENCE = 45.0


def load_jsonl_tail(jsonl_path: str, tail_events: int = DEFAULT_TAIL_EVENTS, event_type: str = "trade", symbol_filter: Optional[str] = None) -> List[Dict]:
    """
    Efficiently read the last N events from a large JSONL.
    Uses a ring buffer approach to avoid loading everything into memory.
    """
    path = Path(jsonl_path)
    if not path.exists():
        print(f"ERROR: JSONL file not found: {jsonl_path}")
        return []

    # If file is small, just read all
    file_size = path.stat().st_size
    if file_size < 50 * 1024 * 1024:  # < 50 MB
        events = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") == event_type:
                    if symbol_filter is None or ev.get("symbol", "").startswith(symbol_filter):
                        events.append(ev)
        if len(events) > tail_events:
            return events[-tail_events:]
        return events

    # Large file: seek from end
    print(f"Large file detected ({file_size / 1024 / 1024:.1f} MB), tailing from end...")
    events = []

    def read_block(f, block_size=8 * 1024 * 1024):
        """Read blocks from end of file, yield lines in reverse."""
        f.seek(0, 2)
        remaining = f.tell()
        buffer = b""
        while remaining > 0 and len(events) < tail_events:
            read_size = min(block_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            chunk = f.read(read_size)
            buffer = chunk + buffer
            lines = buffer.split(b"\n")
            buffer = lines.pop(0) if remaining > 0 else b""
            for line_bytes in reversed(lines):
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") == event_type:
                    if symbol_filter is None or ev.get("symbol", "").startswith(symbol_filter):
                        yield ev
                        if len(events) >= tail_events:
                            return

    with open(path, "rb") as f:
        for ev in read_block(f):
            events.append(ev)
            if len(events) >= tail_events:
                break

    # Reverse back to chronological order
    events.reverse()
    return events


def run_diagnostics(
    jsonl_path: str,
    tail_events: int = DEFAULT_TAIL_EVENTS,
    ticks_per_candle: int = DEFAULT_TICKS_PER_CANDLE,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    opening_range_candles: int = 20,
    symbol_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the full footprint entry diagnostic pipeline.
    """
    print(f"\n{'='*60}")
    print(f"  Footprint Entry Diagnostic Runner")
    print(f"{'='*60}")
    print(f"  JSONL: {jsonl_path}")
    print(f"  Tail events: {tail_events:,}")
    print(f"  Ticks per candle: {ticks_per_candle}")
    print(f"  Min confidence: {min_confidence}")
    print(f"{'='*60}\n")

    # ── 1. Load trades ──
    print("[1/6] Loading trade events from JSONL tail...")
    trades = load_jsonl_tail(jsonl_path, tail_events=tail_events, event_type="trade", symbol_filter=symbol_filter)
    print(f"        Loaded {len(trades):,} trade events")
    if not trades:
        return {"error": "No trade events found"}

    # ── 2. Build tick-footprint candles ──
    print("[2/6] Building tick-footprint candles...")
    builder = TickFootprintBuilder(ticks_per_candle=ticks_per_candle)
    candles = builder.ingest(trades)
    print(f"        Built {len(candles):,} candles")
    if len(candles) < 20:
        return {"error": f"Not enough candles ({len(candles)}), need >= 20"}

    # ── 3. Detect marked levels ──
    print("[3/6] Detecting marked structural levels...")
    level_detector = MarkedLevelsDetector(opening_range_candles=opening_range_candles, prox_threshold_ticks=3)
    levels = level_detector.analyze(candles)
    print(f"        Found {len(levels)} marked levels")
    for lv in levels:
        print(f"          {lv.level_type:20s} @ {lv.price:>8.2f}  strength={lv.strength:.2f}")

    # ── 4. Detect absorption ──
    print("[4/6] Detecting absorption events...")
    abs_detector = AbsorptionDetector(lookback_candles=5, stall_threshold_ticks=2.0, prox_ticks=4, min_absorption_score=25.0)
    abs_events = abs_detector.analyze(candles, marked_levels=levels)
    print(f"        Found {len(abs_events)} absorption events")
    print(f"          Bullish: {len(abs_detector.get_bullish_events())}")
    print(f"          Bearish: {len(abs_detector.get_bearish_events())}")

    # ── 5. Generate entry signals ──
    print("[5/6] Generating footprint entry signals...")
    signal_gen = FootprintEntrySignalGenerator(
        min_confidence=min_confidence,
        prox_ticks=4,
        ticks_per_candle=ticks_per_candle,
        opening_range_candles=opening_range_candles,
    )
    signals = signal_gen.generate(candles)
    print(f"        Generated {len(signals)} signals above {min_confidence} confidence")

    # ── 6. Write diagnostics ──
    print("[6/6] Writing diagnostic files...")
    signal_gen.write_outputs(signals)
    print(f"        CSV: {CSV_PATH}")
    print(f"        MD:  {MD_PATH}")
    print(f"        JSON: {JSON_PATH}")

    # ── 7. Build report ──
    report = build_report(trades, candles, levels, abs_events, signals)
    return report


def build_report(
    trades: List[Dict],
    candles: List[TickFootprintCandle],
    levels: List[MarkedLevel],
    abs_events: List[AbsorptionEvent],
    signals: List[FootprintEntrySignal],
) -> Dict[str, Any]:
    """
    Build a structured report for validation and inspection.
    """
    # Price range from trades / candles
    prices = [c.close for c in candles if hasattr(c, 'close')]
    price_range = {"min": round(min(prices), 2), "max": round(max(prices), 2)} if prices else {}

    # Signal stats
    total_signals = len(signals)
    long_signals = [s for s in signals if s.direction == "LONG"]
    short_signals = [s for s in signals if s.direction == "SHORT"]

    avg_conf = round(sum(s.confidence for s in signals) / total_signals, 1) if total_signals > 0 else 0.0
    at_50 = len([s for s in signals if s.confidence >= 50])
    at_60 = len([s for s in signals if s.confidence >= 60])
    at_75 = len([s for s in signals if s.confidence >= 75])

    best_5 = []
    for rank, sig in enumerate(signals[:5], 1):
        best_5.append({
            "rank": rank,
            "direction": sig.direction,
            "confidence": sig.confidence,
            "entry_price": sig.entry_price,
            "trigger_level": sig.trigger_level,
            "level_type": sig.level_type,
            "setup_type": sig.setup_type,
            "ts_event": sig.ts_event,
            "divergence_type": sig.divergence_type,
            "absorption_score": sig.absorption_score,
            "reclaim_rejection": sig.reclaim_rejection,
            "candle": {
                "o": sig.candle_open, "h": sig.candle_high,
                "l": sig.candle_low, "c": sig.candle_close,
                "delta": sig.candle_delta, "vol": sig.candle_vol,
            },
        })

    report = {
        "ts_generated": datetime.utcnow().isoformat() + "Z",
        "price_range": price_range,
        "stats": {
            "trade_events": len(trades),
            "candles": len(candles),
            "marked_levels": len(levels),
            "absorption_events": len(abs_events),
            "signals_total": total_signals,
            "signals_long": len(long_signals),
            "signals_short": len(short_signals),
            "avg_confidence": avg_conf,
            "at_confidence_50": at_50,
            "at_confidence_60": at_60,
            "at_confidence_75": at_75,
        },
        "level_summary": [
            {"price": lv.price, "type": lv.level_type, "strength": lv.strength, "touches": lv.touches}
            for lv in levels
        ],
        "absorption_summary": [
            {"ts": e.ts, "direction": e.direction, "level": e.level_price, "score": e.score}
            for e in (abs_events[:10] if abs_events else [])
        ],
        "best_5_signals": best_5,
    }

    return report


def print_report(report: Dict[str, Any]):
    """Pretty-print the diagnostic report."""
    print(f"\n{'='*60}")
    print(f"  VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"\nPrice range: {report.get('price_range', {})}")

    stats = report.get("stats", {})
    print(f"\n--- Stats ---")
    print(f"  Trade events:       {stats.get('trade_events', 0):,}")
    print(f"  Candles built:      {stats.get('candles', 0):,}")
    print(f"  Marked levels:      {stats.get('marked_levels', 0)}")
    print(f"  Absorption events:  {stats.get('absorption_events', 0)}")
    print(f"  Signals total:      {stats.get('signals_total', 0)}")
    print(f"  LONG: {stats.get('signals_long', 0)}  |  SHORT: {stats.get('signals_short', 0)}")
    print(f"  Avg confidence:     {stats.get('avg_confidence', 0):.1f}")
    print(f"  >= 50 confidence:   {stats.get('at_confidence_50', 0)}")
    print(f"  >= 60 confidence:   {stats.get('at_confidence_60', 0)}")
    print(f"  >= 75 confidence:   {stats.get('at_confidence_75', 0)}")

    print(f"\n--- Best 5 Signals ---")
    for sig in report.get("best_5_signals", []):
        print(f"\n  #{sig['rank']}  {sig['direction']}  conf={sig['confidence']:.1f}")
        print(f"      Entry: {sig['entry_price']:.2f}  Level: {sig['trigger_level']:.2f} ({sig['level_type']})")
        print(f"      Setup: {sig['setup_type']}")
        print(f"      Divergence: {sig['divergence_type']}")
        print(f"      Absorption: {sig['absorption_score']:.1f}")
        print(f"      Reclaim/Rejection: {sig['reclaim_rejection']}")
        print(f"      Candle: O {sig['candle']['o']:.2f} H {sig['candle']['h']:.2f} L {sig['candle']['l']:.2f} C {sig['candle']['c']:.2f}")
        print(f"              delta={sig['candle']['delta']} vol={sig['candle']['vol']}")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Footprint Entry Diagnostic Runner")
    parser.add_argument("--jsonl", default="state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl", help="Path to live JSONL")
    parser.add_argument("--tail-events", type=int, default=DEFAULT_TAIL_EVENTS, help="Number of trade events to tail")
    parser.add_argument("--ticks-per-candle", type=int, default=DEFAULT_TICKS_PER_CANDLE)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--output-report", default="state/orderflow/live/footprint_entry_report.json", help="JSON report output path")
    parser.add_argument("--symbol-filter", default=None, help="Filter trades by symbol prefix (e.g. 'ES', 'NQ')")
    args = parser.parse_args()

    report = run_diagnostics(
        jsonl_path=args.jsonl,
        tail_events=args.tail_events,
        ticks_per_candle=args.ticks_per_candle,
        min_confidence=args.min_confidence,
        symbol_filter=args.symbol_filter,
    )

    # Print human-readable report
    print_report(report)

    # Write JSON report
    report_path = Path(args.output_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
