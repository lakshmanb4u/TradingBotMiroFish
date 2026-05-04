#!/usr/bin/env python3
"""
Ingest Bookmap JSONL replay captures into research datasets.

Canonical ingestion path:
1. Operator loads .bmf into Bookmap replay
2. OrderflowRecorder captures JSONL automatically
3. This script detects new JSONL, validates, splits, compresses, catalogs

Usage:
    python ingest_bookmap_replay_dataset.py \
        --watch-dir state/orderflow/bookmap_api \
        --output-dir state/orderflow/datasets \
        [--run-replay] [--run-backtest]

Output structure:
    state/orderflow/datasets/
    ├── ES/
    │   ├── 2026-04-18_FOMC/
    │   │   ├── bookmap_capture.parquet
    │   │   ├── manifest.json
    │   │   └── quality_report.md
    │   └── ...
    ├── NQ/
    ├── BTC/
    └── dataset_manifest.json  (global index)
"""

import json, os, re, sys, gzip, argparse, hashlib
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import csv

# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_WATCH = "state/orderflow/bookmap_api"
DEFAULT_OUTPUT = "state/orderflow/datasets"
VALID_SYMBOLS = {"ES", "MES", "NQ", "MNQ", "GC", "MGC", "BTC", "ETH", "CL", "MCL", "SPY", "SPX"}
SESSION_TYPES = {"FOMC", "TREND", "CHOP", "RALLY", "CRASH", "OP_EX", "EARLY", "LATE", "UNKNOWN"}

MIN_EVENTS = 1000           # reject obviously truncated captures
MAX_GAP_SECS = 5.0          # warn on >5s between events (suggests data loss)
EXPECTED_SYMBOLS_PER_FILE = 1  # ideally single-symbol captures going forward

# ─── Logging ────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().isoformat()}] {msg}", flush=True)

def warn(msg: str):
    log(f"⚠️  {msg}")

def error(msg: str):
    log(f"❌ {msg}")

def ok(msg: str):
    log(f"✅ {msg}")


# ─── Safe Converters ────────────────────────────────────────────────────────
def safe_float(v, default=0.0):
    if v is None or v == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def safe_int(v, default=0):
    if v is None or v == '':
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default

# ─── Core Classes ───────────────────────────────────────────────────────────

class IngestionReport:
    """Tracks validation and ingestion results for a single capture."""
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.filename = filepath.name
        self.session_date = None
        self.session_type = "UNKNOWN"
        self.primary_symbol = None
        self.total_events = 0
        self.trade_events = 0
        self.depth_events = 0
        self.start_ts = None
        self.end_ts = None
        self.duration_secs = 0.0
        self.symbols_found = set()
        self.max_gap_secs = 0.0
        self.gap_count = 0
        self.validation_errors = []
        self.warnings = []
        self.output_dir = None
        self.parquet_path = None
        self.replay_results = {}

    def error(self, msg: str):
        self.validation_errors.append(msg)

    def warning(self, msg: str):
        self.warnings.append(msg)

    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0 and self.total_events >= MIN_EVENTS

# ─── Parser ─────────────────────────────────────────────────────────────────

def parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO8601 timestamp from Bookmap JSONL."""
    if not ts_str:
        return None
    s = ts_str.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None

def detect_session_type(filepath: Path, events: List[dict]) -> str:
    """Infer session type from filename or event characteristics."""
    name = filepath.stem.lower()
    
    # Filename pattern detection
    patterns = {
        'fomc': 'FOMC',
        'nfp': 'FOMC',
        'cpi': 'FOMC',
        'trend': 'TREND',
        'chop': 'CHOP',
        'rally': 'RALLY',
        'crash': 'CRASH',
        'opex': 'OP_EX',
        'early': 'EARLY',
        'late': 'LATE',
    }
    for pat, stype in patterns.items():
        if pat in name:
            return stype
    
    # Event-based detection (future enhancement)
    # - High volatility → RALLY/CRASH
    # - Low volatility + high volume → CHOP
    # - Scheduled releases → FOMC
    
    return "UNKNOWN"

def detect_primary_symbol(events: List[dict]) -> Optional[str]:
    """Determine primary symbol by trade event frequency."""
    counts = defaultdict(int)
    for ev in events:
        sym = ev.get('symbol', '')
        if sym and ev.get('event_type') == 'trade':
            counts[sym] += 1
    if not counts:
        return None
    primary = max(counts, key=counts.get)
    # Normalize: extract base symbol (e.g., ESU1.CME@RITHMIC → ES)
    base = primary.split('.')[0].split('_')[0]
    return base[:2] if len(base) >= 2 else base

def extract_date_from_events(events: List[dict]) -> Optional[str]:
    """Extract session date from first event timestamp."""
    if not events:
        return None
    ts_str = events[0].get('ts_event') or events[0].get('ts_recv')
    ts = parse_ts(ts_str)
    if ts:
        return ts.strftime('%Y-%m-%d')
    return None

# ─── Validation ─────────────────────────────────────────────────────────────

def validate_capture(filepath: Path) -> IngestionReport:
    """Full validation of a JSONL capture file."""
    report = IngestionReport(filepath)
    
    if not filepath.exists():
        report.error(f"File not found: {filepath}")
        return report
    
    opener = gzip.open if str(filepath).endswith('.gz') else open
    
    events = []
    prev_ts = None
    line_num = 0
    
    log(f"Validating {filepath.name}...")
    
    try:
        with opener(filepath, 'rt') as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    report.error(f"Line {line_num}: invalid JSON: {e}")
                    continue
                
                events.append(rec)
                report.total_events += 1
                
                # Count event types
                etype = rec.get('event_type')
                if etype == 'trade':
                    report.trade_events += 1
                elif etype == 'depth':
                    report.depth_events += 1
                
                # Track symbols
                sym = rec.get('symbol', '')
                if sym:
                    report.symbols_found.add(sym)
                
                # Timestamp validation
                ts_str = rec.get('ts_event') or rec.get('ts_recv')
                ts = parse_ts(ts_str)
                if ts:
                    if report.start_ts is None:
                        report.start_ts = ts
                        report.session_date = ts.strftime('%Y-%m-%d')
                    report.end_ts = ts
                    
                    if prev_ts:
                        gap = (ts - prev_ts).total_seconds()
                        if gap > MAX_GAP_SECS:
                            report.gap_count += 1
                            report.max_gap_secs = max(report.max_gap_secs, gap)
                    prev_ts = ts
                else:
                    report.error(f"Line {line_num}: invalid timestamp: {ts_str}")
    except Exception as e:
        report.error(f"Read error: {e}")
        return report
    
    # Post-validation checks
    if report.total_events < MIN_EVENTS:
        report.error(f"Too few events: {report.total_events} < {MIN_EVENTS}")
    
    if report.start_ts and report.end_ts:
        report.duration_secs = (report.end_ts - report.start_ts).total_seconds()
        if report.duration_secs < 60:
            report.warning(f"Very short session: {report.duration_secs:.0f}s")
    
    if len(report.symbols_found) > EXPECTED_SYMBOLS_PER_FILE:
        report.warning(f"Multiple symbols in single file: {report.symbols_found}")
    
    if report.gap_count > 0:
        report.warning(f"{report.gap_count} gaps > {MAX_GAP_SECS}s detected (max={report.max_gap_secs:.1f}s)")
    
    if report.trade_events == 0:
        report.error("No trade events found")
    
    # Detect session metadata
    report.session_type = detect_session_type(filepath, events)
    report.primary_symbol = detect_primary_symbol(events)
    
    ok(f"Validated {report.filename}: {report.total_events:,} events, "
       f"{len(report.symbols_found)} symbols, {report.duration_secs/60:.1f}min")
    
    return report

# ─── Dataset Writer ─────────────────────────────────────────────────────────

def write_parquet_split(report: IngestionReport, events: List[dict], output_dir: Path) -> Path:
    """Write events to Parquet, optionally split by symbol."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    
    # Build rows grouped by symbol
    symbol_rows = defaultdict(list)
    for ev in events:
        sym = ev.get('symbol', 'UNKNOWN')
        symbol_rows[sym].append({
            'ts_event': parse_ts(ev.get('ts_event') or ev.get('ts_recv')),
            'event_type': ev.get('event_type'),
            'symbol': sym,
            'price': float(ev.get('price', 0) or 0),
            'size': int(ev.get('size', 0) or 0),
            'side': ev.get('side', ''),
            'is_bid_aggressor': bool(ev.get('is_bid_aggressor', False)),
        })
    
    # Write unified parquet (all symbols)
    all_rows = []
    for sym_rows in symbol_rows.values():
        all_rows.extend(sym_rows)
    
    all_rows.sort(key=lambda r: r['ts_event'] or datetime.min)
    
    # Convert to pyarrow table
    if not all_rows:
        return None
    
    df_data = {
        'ts_event': [r['ts_event'] for r in all_rows],
        'event_type': [r['event_type'] for r in all_rows],
        'symbol': [r['symbol'] for r in all_rows],
        'price': [r['price'] for r in all_rows],
        'size': [r['size'] for r in all_rows],
        'side': [r['side'] for r in all_rows],
    }
    
    table = pa.Table.from_pydict(df_data)
    parquet_path = output_dir / "bookmap_capture.parquet"
    pq.write_table(table, parquet_path, compression='zstd')
    
    return parquet_path

def write_manifest(report: IngestionReport, output_dir: Path):
    """Write dataset manifest JSON."""
    manifest = {
        "filename": report.filename,
        "session_date": report.session_date,
        "session_type": report.session_type,
        "primary_symbol": report.primary_symbol,
        "total_events": report.total_events,
        "trade_events": report.trade_events,
        "depth_events": report.depth_events,
        "duration_seconds": report.duration_secs,
        "symbols_found": sorted(list(report.symbols_found)),
        "max_gap_seconds": report.max_gap_secs,
        "gap_count": report.gap_count,
        "validation_errors": report.validation_errors,
        "warnings": report.warnings,
        "is_valid": report.is_valid(),
        "ingested_at": datetime.now().isoformat(),
    }
    
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

def write_quality_report(report: IngestionReport, output_dir: Path):
    """Write human-readable quality report."""
    lines = [
        f"# Dataset Quality Report: {report.filename}",
        f"",
        f"**Date:** {report.session_date}",
        f"**Session Type:** {report.session_type}",
        f"**Primary Symbol:** {report.primary_symbol}",
        f"**Duration:** {report.duration_secs/60:.1f} minutes",
        f"",
        f"## Event Statistics",
        f"- Total events: {report.total_events:,}",
        f"- Trade events: {report.trade_events:,}",
        f"- Depth events: {report.depth_events:,}",
        f"- Symbols found: {len(report.symbols_found)}",
        f"  - {', '.join(sorted(report.symbols_found))}",
        f"",
        f"## Timing",
        f"- Start: {report.start_ts}",
        f"- End: {report.end_ts}",
        f"- Max gap: {report.max_gap_secs:.2f}s ({report.gap_count} gaps > {MAX_GAP_SECS}s)",
        f"",
        f"## Validation",
    ]
    
    if report.is_valid():
        lines.append("- ✅ PASSED")
    else:
        lines.append("- ❌ FAILED")
    
    if report.validation_errors:
        lines.append("\n### Errors")
        for e in report.validation_errors:
            lines.append(f"- {e}")
    
    if report.warnings:
        lines.append("\n### Warnings")
        for w in report.warnings:
            lines.append(f"- {w}")
    
    lines.append("")
    
    report_path = output_dir / "quality_report.md"
    report_path.write_text('\n'.join(lines))

# ─── Replay Pipeline ────────────────────────────────────────────────────────

def run_replay_pipeline(report: IngestionReport, events: List[dict], output_dir: Path):
    """Run replay engine on ingested dataset."""
    from replay_orderflow_jsonl import OrderflowReplay
    
    log("Running replay pipeline...")
    
    eng = OrderflowReplay(bar_secs=5)
    
    # Feed events directly (no JSONL reading needed)
    for rec in events:
        from datetime import timedelta
        ts = parse_ts(rec.get('ts_event') or rec.get('ts_recv'))
        if not ts:
            continue
        
        sym = str(rec.get('symbol', ''))
        price = rec.get('price')
        size = int(rec.get('size', 0) or 0)
        
        if rec.get('event_type') == 'depth' and price is not None:
            from replay_orderflow_jsonl import DepthEvent
            eng.events.append(DepthEvent(
                ts=ts, symbol=sym, price=float(price),
                size=size, side=rec.get('side', 'bid')))
        elif rec.get('event_type') == 'trade' and price is not None:
            from replay_orderflow_jsonl import TradeEvent
            agg = 'sell' if (rec.get('side') == 'sell' or rec.get('is_bid_aggressor')) else 'buy'
            eng.events.append(TradeEvent(
                ts=ts, symbol=sym, price=float(price),
                size=size, aggressor=agg))
    
    eng.events.sort(key=lambda e: e.ts)
    
    # Run replay
    eng.replay()
    
    # Export results
    replay_dir = output_dir / "backtests"
    replay_dir.mkdir(parents=True, exist_ok=True)
    
    eng.export(replay_dir)
    
    report.replay_results = {
        "trades": len(eng.trades),
        "signals": len(eng.signals),
        "sweeps": len(eng.sweeps),
        "win_rate": sum(1 for t in eng.trades if t.pnl > 0) / len(eng.trades) * 100 if eng.trades else 0,
        "total_pnl": sum(t.pnl for t in eng.trades),
    }
    
    ok(f"Replay complete: {report.replay_results['trades']} trades, "
       f"${report.replay_results['total_pnl']:.2f} PnL")

# ─── Global Manifest ────────────────────────────────────────────────────────

def update_global_manifest(output_dir: Path, reports: List[IngestionReport]):
    """Update the global dataset_manifest.json."""
    manifest_path = output_dir / "dataset_manifest.json"
    
    # Load existing
    datasets = {}
    if manifest_path.exists():
        try:
            datasets = json.loads(manifest_path.read_text())
        except:
            pass
    
    for report in reports:
        if not report.is_valid():
            continue
        
        sym = report.primary_symbol or "UNKNOWN"
        date = report.session_date or "unknown"
        stype = report.session_type
        
        key = f"{date}_{stype}"
        
        datasets[key] = {
            "symbol": sym,
            "date": date,
            "session_type": stype,
            "total_events": report.total_events,
            "duration_seconds": report.duration_secs,
            "trades": report.replay_results.get("trades", 0),
            "signals": report.replay_results.get("signals", 0),
            "total_pnl": report.replay_results.get("total_pnl", 0),
            "path": str(report.output_dir.relative_to(output_dir) if report.output_dir else ""),
        }
    
    manifest_path.write_text(json.dumps(datasets, indent=2, default=str))
    ok(f"Updated global manifest: {len(datasets)} datasets")

# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Ingest Bookmap JSONL replays into research datasets')
    ap.add_argument('-w', '--watch-dir', default=DEFAULT_WATCH, help='Directory to watch for JSONL files')
    ap.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT, help='Output dataset directory')
    ap.add_argument('--run-replay', action='store_true', help='Run replay engine on each dataset')
    ap.add_argument('--run-backtest', action='store_true', help='Run backtest optimization suite')
    ap.add_argument('--file', help='Process single file instead of watching directory')
    args = ap.parse_args()
    
    watch_dir = Path(args.watch_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find files to process
    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(watch_dir.glob('*.jsonl*'))
    
    if not files:
        log(f"No JSONL files found in {watch_dir}")
        return
    
    log(f"Found {len(files)} capture file(s)")
    
    reports = []
    
    for filepath in files:
        # Validate
        report = validate_capture(filepath)
        reports.append(report)
        
        if not report.is_valid():
            error(f"Validation failed for {filepath.name} — skipping ingestion")
            continue
        
        # Determine output structure: SYMBOL/YYYY-MM-DD_SESSIONTYPE/
        sym = report.primary_symbol or "UNKNOWN"
        date = report.session_date or "unknown"
        stype = report.session_type
        
        dataset_dir = output_dir / sym / f"{date}_{stype}"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        report.output_dir = dataset_dir
        
        log(f"Ingesting to {dataset_dir}...")
        
        # Read events for writing
        opener = gzip.open if str(filepath).endswith('.gz') else open
        events = []
        with opener(filepath, 'rt') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except:
                    pass
        
        # Write outputs
        parquet_path = write_parquet_split(report, events, dataset_dir)
        if parquet_path:
            report.parquet_path = parquet_path
            ok(f"Wrote {parquet_path} ({parquet_path.stat().st_size / 1024 / 1024:.1f} MB)")
        
        write_manifest(report, dataset_dir)
        write_quality_report(report, dataset_dir)
        
        # Run replay if requested
        if args.run_replay:
            run_replay_pipeline(report, events, dataset_dir)
        
        ok(f"Ingested {filepath.name} → {dataset_dir}")
    
    # Update global manifest
    update_global_manifest(output_dir, reports)
    
    # Write shell-style symbol stats
    sym_stats = defaultdict(lambda: {"captures": 0, "total_events": 0, "total_trades": 0})
    for r in reports:
        if r.is_valid():
            sym = r.primary_symbol or "UNKNOWN"
            sym_stats[sym]["captures"] += 1
            sym_stats[sym]["total_events"] += r.total_events
            sym_stats[sym]["total_trades"] += r.trade_events
    
    stats_path = output_dir / "symbol_stats.json"
    stats_path.write_text(json.dumps(dict(sym_stats), indent=2))
    
    # Summary
    log(f"\n{'='*50}")
    log(f"Ingestion complete")
    log(f"Files processed: {len(files)}")
    log(f"Valid: {sum(1 for r in reports if r.is_valid())}")
    log(f"Invalid: {sum(1 for r in reports if not r.is_valid())}")
    for sym, stats in sorted(sym_stats.items()):
        log(f"  {sym}: {stats['captures']} captures, {stats['total_events']:,} events")

if __name__ == '__main__':
    main()
