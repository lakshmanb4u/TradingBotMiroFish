#!/usr/bin/env python3
"""
Automated Bookmap Replay Pipeline

Runs continuously during a Bookmap replay session:
1. Detects and monitors active JSONL capture files
2. Health check: growth rate, timestamp gaps, symbol consistency
3. Backtest replay with footprint, sweep, imbalance detection
4. Generates reports and artifacts in timestamped run folder
5. WhatsApp notifications every --interval-minutes

Usage:
    python run_bookmap_replay_pipeline.py \
        --input "state/orderflow/bookmap_api/*.jsonl" \
        --output-dir state/orderflow/backtests/latest \
        --notify whatsapp \
        --interval-minutes 10
"""

import sys, os, json, csv, hashlib, time, argparse, signal, subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# Allow importing replay engine from sibling directory
sys.path.insert(0, str(Path(__file__).parent))
from replay_orderflow_jsonl import (
    OrderflowReplay, _sym_cfg, STOP_TICKS, TARGET_TICKS,
    DepthEvent, TradeEvent
)

# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_WATCH = "state/orderflow/bookmap_api/*.jsonl"
DEFAULT_OUTPUT = "state/orderflow/backtests/latest"
HEALTH_MIN_EVTS_PER_SEC = 10       # minimum event rate for "healthy"
HEALTH_MAX_GAP_SECS = 10.0         # warn on gaps >10s
MIN_EVENTS_FOR_BACKTEST = 500  # overridden at runtime in main()

# ─── Logging ────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().isoformat()}] {msg}", flush=True)

def warn(msg: str):
    log(f"⚠️  {msg}")

def error(msg: str):
    log(f"❌ {msg}")

def ok(msg: str):
    log(f"✅ {msg}")

# ─── File Monitoring ────────────────────────────────────────────────────────

class CaptureMonitor:
    def __init__(self, pattern: str):
        import glob
        self.pattern = pattern
        self.files = {}  # path -> {size, mtime, events}
        self._scan()
    
    def _scan(self):
        import glob
        paths = glob.glob(self.pattern)
        for p in paths:
            p = Path(p)
            stat = p.stat()
            if str(p) not in self.files:
                self.files[str(p)] = {
                    'path': p,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                    'events': 0,
                    'last_check': time.time(),
                }
    
    def get_active_files(self) -> List[Path]:
        """Return files modified in last 60s (active captures)."""
        self._scan()
        now = time.time()
        active = []
        for path_str, info in self.files.items():
            if now - info['mtime'] < 60:
                active.append(info['path'])
        return active
    
    def get_file_stats(self, path: Path) -> dict:
        stat = path.stat()
        return {
            'size_mb': stat.st_size / 1024 / 1024,
            'mtime': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'age_seconds': time.time() - stat.st_mtime,
        }

# ─── Health Check ───────────────────────────────────────────────────────────

def run_health_check(filepath: Path) -> dict:
    """Stream-read JSONL and compute health metrics."""
    import gzip
    
    result = {
        'filepath': str(filepath),
        'filename': filepath.name,
        'total_events': 0,
        'trade_events': 0,
        'depth_events': 0,
        'symbols': set(),
        'start_ts': None,
        'end_ts': None,
        'duration_seconds': 0.0,
        'events_per_sec': 0.0,
        'max_gap_seconds': 0.0,
        'gap_count': 0,
        'errors': [],
        'is_healthy': False,
    }
    
    opener = gzip.open if str(filepath).endswith('.gz') else open
    prev_ts = None
    
    try:
        with opener(filepath, 'rt') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                result['total_events'] += 1
                
                etype = rec.get('event_type')
                if etype == 'trade':
                    result['trade_events'] += 1
                elif etype == 'depth':
                    result['depth_events'] += 1
                
                sym = rec.get('symbol', '')
                if sym:
                    result['symbols'].add(sym)
                
                ts_str = rec.get('ts_event') or rec.get('ts_recv')
                if ts_str:
                    ts = None
                    try:
                        if ts_str.endswith('Z'):
                            ts_str = ts_str[:-1] + '+00:00'
                        ts = datetime.fromisoformat(ts_str)
                    except:
                        pass
                    
                    if ts:
                        if result['start_ts'] is None:
                            result['start_ts'] = ts
                        result['end_ts'] = ts
                        
                        if prev_ts:
                            gap = (ts - prev_ts).total_seconds()
                            if gap > HEALTH_MAX_GAP_SECS:
                                result['gap_count'] += 1
                                result['max_gap_seconds'] = max(result['max_gap_seconds'], gap)
                        prev_ts = ts
    except Exception as e:
        result['errors'].append(str(e))
        return result
    
    # Compute rates
    if result['start_ts'] and result['end_ts']:
        result['duration_seconds'] = (result['end_ts'] - result['start_ts']).total_seconds()
        if result['duration_seconds'] > 0:
            result['events_per_sec'] = result['total_events'] / result['duration_seconds']
    
    # Health determination
    result['is_healthy'] = (
        result['total_events'] >= MIN_EVENTS_FOR_BACKTEST and
        result['events_per_sec'] >= HEALTH_MIN_EVTS_PER_SEC and
        result['gap_count'] <= 10  # some gap tolerance
    )
    
    # Convert set to list for JSON serialization
    result['symbols'] = sorted(list(result['symbols']))
    
    return result

def write_health_report(health: dict, output_dir: Path):
    """Write capture_health.json and capture_health.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # JSON
    json_path = output_dir / "capture_health.json"
    json_path.write_text(json.dumps(health, indent=2, default=str))
    
    # Markdown
    md = f"""# Capture Health Report
**File:** {health['filename']}
**Checked:** {datetime.now().isoformat()}

## Status
{'✅ HEALTHY' if health['is_healthy'] else '❌ UNHEALTHY'}

## Event Statistics
- Total events: {health['total_events']:,}
- Trade events: {health['trade_events']:,}
- Depth events: {health['depth_events']:,}
- Events/sec: {health['events_per_sec']:.1f}

## Timing
- Start: {health['start_ts']}
- End: {health['end_ts']}
- Duration: {health['duration_seconds']/60:.1f} minutes
- Max gap: {health['max_gap_seconds']:.1f}s
- Gap count (>10s): {health['gap_count']}

## Symbols
{chr(10).join(f"- {s}" for s in health['symbols'])}

## Errors
{chr(10).join(f"- {e}" for e in health['errors']) if health['errors'] else "None"}
"""
    (output_dir / "capture_health.md").write_text(md)

# ─── Backtest Pipeline ─────────────────────────────────────────────────────-

def run_backtest(filepath: Path, output_dir: Path) -> dict:
    """Run full replay backtest and export artifacts."""
    log(f"Running backtest on {filepath.name}...")
    
    eng = OrderflowReplay(bar_secs=5)
    eng.load_jsonl(filepath)
    eng.replay()
    
    # Export artifacts
    eng.export(output_dir)
    
    # Compute metrics
    trades = eng.trades
    n = len(trades)
    
    if n == 0:
        return {"error": "No trades generated"}
    
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    wn, ln = len(winners), len(losers)
    
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = sum(t.pnl for t in losers)
    
    metrics = {
        "total_trades": n,
        "win_rate": wn / n * 100 if n else 0,
        "profit_factor": abs(gross_profit / gross_loss) if gross_loss else float('inf'),
        "expectancy": sum(t.pnl for t in trades) / n if n else 0,
        "gross_pnl": sum(t.pnl for t in trades),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "max_drawdown": compute_max_drawdown(trades),
        "avg_r": compute_avg_r(trades),
        "best_trade": max(t.pnl for t in trades) if trades else 0,
        "worst_trade": min(t.pnl for t in trades) if trades else 0,
        "avg_mae": sum(t.mae for t in trades) / n if n else 0,
        "avg_mfe": sum(t.mfe for t in trades) / n if n else 0,
        "false_breakout_rate": sum(1 for t in trades if 'failed_break' in t.signal) / n * 100,
        "symbols_tested": list(set(t.symbol for t in trades)),
        "trade_count": sum(1 for t in trades),
        "depth_events": sum(1 for e in eng.events if hasattr(e, 'side')),  # DepthEvent has 'side'
        "sweep_events": len(eng.sweeps),
        "signal_count": len(eng.signals),
    }
    
    # Execution breakdown
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason] += 1
    metrics['execution_breakdown'] = dict(reasons)
    
    return metrics

def compute_max_drawdown(trades) -> float:
    """Compute max drawdown from equity curve."""
    peak = running = 0.0
    max_dd = 0.0
    for t in trades:
        running += t.pnl
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    return max_dd

def compute_avg_r(trades) -> float:
    """Compute average R (risk units)."""
    r_values = []
    for t in trades:
        cfg = _sym_cfg(t.symbol)
        risk = STOP_TICKS * cfg['tick_value']
        if risk > 0:
            r_values.append(t.pnl / risk)
    return sum(r_values) / len(r_values) if r_values else 0

def write_summary_json(metrics: dict, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.json"
    path.write_text(json.dumps(metrics, indent=2, default=str))

def write_summary_md(metrics: dict, output_dir: Path):
    """Write human-readable replay_report.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    md = f"""# Automated Backtest Report
Generated: {datetime.now().isoformat()}

## Performance Metrics
| Metric | Value |
|--------|-------|
| Total Trades | {metrics['total_trades']} |
| Win Rate | {metrics['win_rate']:.1f}% |
| Profit Factor | {metrics['profit_factor']:.2f} |
| Expectancy | ${metrics['expectancy']:.2f} |
| Gross PnL | ${metrics['gross_pnl']:.2f} |
| Gross Profit | ${metrics['gross_profit']:.2f} |
| Gross Loss | ${metrics['gross_loss']:.2f} |
| Max Drawdown | ${metrics['max_drawdown']:.2f} |
| Avg R | {metrics['avg_r']:.2f}R |
| Best Trade | ${metrics['best_trade']:.2f} |
| Worst Trade | ${metrics['worst_trade']:.2f} |
| Avg MAE | {metrics['avg_mae']:.1f} ticks |
| Avg MFE | {metrics['avg_mfe']:.1f} ticks |
| False Breakout Rate | {metrics['false_breakout_rate']:.1f}% |
| Symbols Tested | {', '.join(metrics['symbols_tested'])} |
| Trade Events | {metrics['trade_count']} |
| Depth Events | {metrics['depth_events']} |
| Sweep Events | {metrics['sweep_events']} |
| Signal Count | {metrics['signal_count']} |

## Execution Breakdown
"""
    for reason, count in metrics.get('execution_breakdown', {}).items():
        pct = count / metrics['total_trades'] * 100 if metrics['total_trades'] else 0
        md += f"- **{reason}**: {count} ({pct:.0f}%)\n"
    
    (output_dir / "replay_report.md").write_text(md)

# ─── Notification ───────────────────────────────────────────────────────────

def send_whatsapp_notification(metrics: dict, health: Optional[dict]):
    """Send short WhatsApp status update."""
    # Build message
    status = "🟢 HEALTHY" if (health and health.get('is_healthy')) else "🔴 UNHEALTHY"
    
    lines = [
        f"📊 Bookmap Replay Update",
        f"Status: {status}",
        f"Events: {health.get('total_events', 0):,}" if health else "",
        f"Trades: {metrics.get('total_trades', 0)}",
        f"Win Rate: {metrics.get('win_rate', 0):.1f}%",
        f"PnL: ${metrics.get('gross_pnl', 0):+.2f}",
        f"Expectancy: ${metrics.get('expectancy', 0):+.2f}",
    ]
    
    if health and health.get('errors'):
        lines.append(f"⚠️ Errors: {len(health['errors'])}")
        for e in health['errors'][:3]:
            lines.append(f"  - {e}")
    
    msg = '\n'.join(l for l in lines if l)
    
    # Write to notification file for OpenClaw to pick up
    # In a real implementation, this would use OpenClaw's messaging system
    log(f"[NOTIFY] {msg.replace(chr(10), ' | ')}")
    
    # Try to use openclaw command for WhatsApp
    try:
        subprocess.run(
            ["openclaw", "send", "whatsapp", "+15515747457", msg],
            capture_output=True, timeout=10
        )
    except:
        pass  # Fallback: just log

# ─── Main Pipeline ──────────────────────────────────────────────────────────

def run_single_pass(args) -> Tuple[Optional[dict], Optional[dict]]:
    """Run one pass of detect → health → backtest → report."""
    import glob
    
    files = sorted(glob.glob(args.input))
    if not files:
        log("No JSONL files found")
        return None, None
    
    # Pick most recently modified NON-EMPTY file
    files_with_mtime = [
        (Path(f), Path(f).stat().st_mtime, Path(f).stat().st_size)
        for f in files
        if Path(f).stat().st_size > 100  # skip empty/trivial files
    ]
    if not files_with_mtime:
        log("No non-empty JSONL files found")
        return None, None
    
    files_with_mtime.sort(key=lambda x: x[1], reverse=True)
    filepath = files_with_mtime[0][0]
    
    log(f"Processing: {filepath.name}")
    
    # Health check
    health = run_health_check(filepath)
    write_health_report(health, Path(args.output_dir))
    
    if not health['is_healthy']:
        warn(f"Health check failed for {filepath.name}: {health.get('errors', [])}")
        return None, health
    
    ok(f"Health check passed: {health['total_events']:,} events, "
       f"{health['events_per_sec']:.1f} evt/s")
    
    # Backtest
    metrics = run_backtest(filepath, Path(args.output_dir))
    
    if 'error' in metrics:
        warn(f"Backtest failed: {metrics['error']}")
        return metrics, health
    
    # Write reports
    write_summary_json(metrics, Path(args.output_dir))
    write_summary_md(metrics, Path(args.output_dir))
    
    ok(f"Backtest complete: {metrics['total_trades']} trades, "
       f"{metrics['win_rate']:.1f}% WR, ${metrics['gross_pnl']:+.2f}")
    
    return metrics, health

def main():
    ap = argparse.ArgumentParser(description='Automated Bookmap Replay Pipeline')
    ap.add_argument('-i', '--input', default=DEFAULT_WATCH, help='JSONL glob pattern')
    ap.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT, help='Output directory')
    ap.add_argument('--notify', choices=['whatsapp', 'none'], default='none', help='Notification method')
    ap.add_argument('--interval-minutes', type=int, default=10, help='Notification interval')
    ap.add_argument('--once', action='store_true', help='Run once and exit (no loop)')
    ap.add_argument('--min-events', type=int, default=MIN_EVENTS_FOR_BACKTEST, help='Min events for backtest')
    args = ap.parse_args()
    
    # Update global config (must be module-level assignment, not local redefinition)
    import run_bookmap_replay_pipeline as mod
    mod.MIN_EVENTS_FOR_BACKTEST = args.min_events
    log(f"Minimum events threshold: {mod.MIN_EVENTS_FOR_BACKTEST}")
    
    log(f"Starting Bookmap Replay Pipeline")
    log(f"  Input: {args.input}")
    log(f"  Output: {args.output_dir}")
    log(f"  Interval: {args.interval_minutes} minutes")
    log(f"  Notify: {args.notify}")
    
    # Handle graceful shutdown
    running = True
    def on_sigint(signum, frame):
        nonlocal running
        log("Shutting down...")
        running = False
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)
    
    if args.once:
        metrics, health = run_single_pass(args)
        if args.notify == 'whatsapp' and metrics:
            send_whatsapp_notification(metrics, health)
        return
    
    # Loop mode
    last_notify = 0
    last_metrics = None
    last_health = None
    
    while running:
        try:
            metrics, health = run_single_pass(args)
            if metrics:
                last_metrics = metrics
            if health:
                last_health = health
            
            # Send notification on interval
            now = time.time()
            if args.notify == 'whatsapp' and (now - last_notify) >= args.interval_minutes * 60:
                if last_metrics:
                    send_whatsapp_notification(last_metrics, last_health)
                    last_notify = now
            
        except Exception as e:
            error(f"Pipeline error: {e}")
            import traceback
            traceback.print_exc()
        
        # Sleep before next check
        for _ in range(30):  # Check every 30s, but allow early exit
            if not running:
                break
            time.sleep(1)
    
    log("Pipeline stopped")

if __name__ == '__main__':
    main()
