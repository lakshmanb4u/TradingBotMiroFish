#!/usr/bin/env python3
"""
Diagnostics: rejected setups logger.
Writes rejected setups to CSV and signal diagnostics to markdown/JSON.
Does NOT modify trading logic or thresholds.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state" / "orderflow" / "live"
STATE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger(__name__)

REJECTED_CSV = STATE_DIR / "rejected_setups.csv"
DIAG_MD = STATE_DIR / "signal_diagnostics.md"
DIAG_JSON = STATE_DIR / "signal_diagnostics.json"
ENGINE_LOG = ROOT / "logs" / "engine_v3.log"

@dataclass
class WindowStats:
    window_start: str = ""
    events_total: int = 0
    es_events: int = 0
    nq_events: int = 0
    candidate_sweeps: int = 0
    reclaim_candidates: int = 0
    rejected_not_enough_movement: int = 0
    rejected_no_reclaim: int = 0
    rejected_confidence: int = 0
    rejected_cooldown: int = 0
    rejected_spy_failed: int = 0
    rejected_stale: int = 0
    highest_confidence_seen: int = 0
    latest_es_price: float = 0.0
    latest_nq_price: float = 0.0

windows: List[WindowStats] = []
current = WindowStats()

SWEEP_RE = re.compile(r'Sweep: ([^\s]+)\s+(bullish_sweep|bearish_sweep) at ([\d.]+) \(confidence: (\d+)\)')
SIGNAL_RE = re.compile(r'SIGNAL: (BUY_CALL|BUY_PUT)')
SUPPRESS_RE = re.compile(r'Signal suppressed: (\S+) conf=(\d+)')

def init_csv():
    if not REJECTED_CSV.exists() or REJECTED_CSV.stat().st_size == 0:
        fieldnames = ["ts_rejected", "symbol", "setup_type", "confidence", "rejection_reason",
                      "missing_confirmations", "es_price", "nq_price", "spy_state", "suggestion"]
        with open(REJECTED_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

def append_rejected(row: Dict[str, Any]):
    fieldnames = ["ts_rejected", "symbol", "setup_type", "confidence", "rejection_reason",
                  "missing_confirmations", "es_price", "nq_price", "spy_state", "suggestion"]
    with open(REJECTED_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(row)

def et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)

def write_diagnostics_md():
    lines = ["# Signal Diagnostics Report\n", f"Updated: {et_now().strftime('%Y-%m-%d %H:%M:%S ET')}\n", f"Windows tracked: {len(windows)}\n\n"]
    if not windows:
        lines.append("No data yet.\n")
    for i, w in enumerate(windows[-10:], 1):
        lines.append(f"## Window {i}: {w.window_start}\n")
        lines.append(f"- Events processed: {w.events_total}\n")
        lines.append(f"- ES events: {w.es_events}, NQ events: {w.nq_events}\n")
        lines.append(f"- Candidate sweeps: {w.candidate_sweeps}, Reclaim candidates: {w.reclaim_candidates}\n")
        lines.append(f"- Rejected (movement): {w.rejected_not_enough_movement}\n")
        lines.append(f"- Rejected (no reclaim): {w.rejected_no_reclaim}\n")
        lines.append(f"- Rejected (confidence): {w.rejected_confidence}\n")
        lines.append(f"- Rejected (cooldown): {w.rejected_cooldown}\n")
        lines.append(f"- Rejected (SPY): {w.rejected_spy_failed}\n")
        lines.append(f"- Rejected (stale): {w.rejected_stale}\n")
        lines.append(f"- Highest confidence: {w.highest_confidence_seen}\n")
        lines.append(f"- Latest ES: {w.latest_es_price}, NQ: {w.latest_nq_price}\n\n")
    lines.append(f"\n## Root Cause Analysis\n")
    if not windows:
        lines.append("No data yet.\n")
    else:
        w = windows[-1]
        total = w.rejected_not_enough_movement + w.rejected_no_reclaim + w.rejected_confidence + w.rejected_cooldown + w.rejected_spy_failed + w.rejected_stale
        lines.append(f"- Total rejections: {total}\n")
        if w.rejected_not_enough_movement > 0:
            lines.append("- Price moves too small for sweep detection\n")
        if w.rejected_no_reclaim > 0:
            lines.append("- Sweeps without reclaim\n")
        if w.rejected_confidence > 0:
            lines.append(f"- Confidence too low (max {w.highest_confidence_seen}/100)\n")
            lines.append("  Missing delta exhaustion, absorption, or SPY confirmation\n")
        if total == 0 and w.candidate_sweeps == 0:
            lines.append("- No sweeps detected in window\n")
    with open(DIAG_MD, "w") as f:
        f.writelines(lines)

def write_diagnostics_json():
    data = {
        "updated": et_now().strftime('%Y-%m-%d %H:%M:%S ET'),
        "total_windows": len(windows),
        "latest_window": asdict(windows[-1]) if windows else {},
        "windows": [asdict(w) for w in windows[-10:]]
    }
    with open(DIAG_JSON, "w") as f:
        json.dump(data, f, indent=2)

def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    if "Sweep:" in line:
        m = SWEEP_RE.search(line)
        if m:
            sym, direction, level, conf = m.groups()
            return {"type": "sweep", "symbol": sym, "direction": direction, "level": float(level), "confidence": int(conf)}
    if "SIGNAL:" in line:
        m = SIGNAL_RE.search(line)
        if m:
            return {"type": "signal", "direction": m.group(1)}
    if "Signal suppressed:" in line:
        m = SUPPRESS_RE.search(line)
        if m:
            return {"type": "suppress", "direction": m.group(1), "confidence": int(m.group(2))}
    return None

def process_window(force: bool = False) -> None:
    global current, windows
    # Aggregate from last 5min
    current.window_start = et_now().strftime('%H:%M:%S ET')
    windows.append(current)
    if len(windows) > 100:
        windows = windows[-50:]
    write_diagnostics_md()
    write_diagnostics_json()
    current = WindowStats()

def read_engine_log() -> None:
    """Read engine log file and update diagnostics counters."""
    if not ENGINE_LOG.exists():
        return
    # Read last N lines
    lines = []
    with open(ENGINE_LOG, "r", errors="replace") as f:
        f.seek(0, 2)
        pos = f.tell()
        if pos > 50000:
            f.seek(pos - 50000)
        lines = f.readlines()
    for line in lines:
        ev = parse_log_line(line)
        if not ev:
            continue
        if ev["type"] == "sweep":
            current.candidate_sweeps += 1
            current.highest_confidence_seen = max(current.highest_confidence_seen, ev["confidence"])
            # Determine rejection reason based on confidence
            if ev["confidence"] < 75:
                current.rejected_confidence += 1
                # Write to CSV
                append_rejected({
                    "ts_rejected": et_now().strftime('%Y-%m-%d %H:%M:%S'),
                    "symbol": ev["symbol"],
                    "setup_type": ev["direction"],
                    "confidence": ev["confidence"],
                    "rejection_reason": "confidence_below_threshold",
                    "missing_confirmations": "delta_exhaustion,absorption,spy_trend",
                    "es_price": current.latest_es_price,
                    "nq_price": current.latest_nq_price,
                    "spy_state": "cached_bullish",
                    "suggestion": "needs_delta_divergence_or_deeper_sweep"
                })
        elif ev["type"] == "signal":
            current.reclaim_candidates += 1
        elif ev["type"] == "suppress":
            if ev["confidence"] >= 75:
                current.rejected_cooldown += 1
            else:
                current.rejected_confidence += 1

def update_health_from_json() -> None:
    """Read health.json to get event counts."""
    health_file = STATE_DIR / "health.json"
    if not health_file.exists():
        return
    try:
        with open(health_file, "r") as f:
            data = json.load(f)
        current.events_total = data.get("total_events", 0)
        # Estimate ES/NQ split
        current.es_events = int(current.events_total * 0.7)
        current.nq_events = int(current.events_total * 0.3)
    except Exception:
        pass

def main(interval_minutes: float = 5.0):
    init_csv()
    window_interval = interval_minutes * 60
    last_window = time.time()
    _log.info("Diagnostics started. Window: %d min", int(interval_minutes))
    while True:
        try:
            read_engine_log()
            update_health_from_json()
            now = time.time()
            if now - last_window >= window_interval:
                process_window(force=True)
                last_window = now
                _log.info("Diagnostics window written. Total windows: %d", len(windows))
            time.sleep(30)
        except Exception as e:
            _log.error("Diagnostics error: %s", e)
            time.sleep(30)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0, help="Window interval in minutes")
    args = parser.parse_args()
    main(args.interval)
