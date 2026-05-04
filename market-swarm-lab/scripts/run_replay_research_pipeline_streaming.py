#!/usr/bin/env python3
"""
Streaming Incremental Replay Research Pipeline

Processes Bookmap JSONL captures incrementally with checkpointing.
- Never loads entire file into memory
- Resumable from checkpoints
- Rolling aggregations
- Configurable batch sizes
- Performance benchmarking

Usage:
    python run_replay_research_pipeline_streaming.py \
        --input state/orderflow/bookmap_api/*.jsonl \
        --output-dir state/orderflow/backtests/latest \
        --batch-size 50000 \
        --checkpoint-dir state/orderflow/checkpoints
"""

import sys, os, json, csv, time, argparse, signal, gc
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    psutil = None
from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Iterator, Callable
import gzip

sys.path.insert(0, str(Path(__file__).parent))
from replay_orderflow_jsonl import (
    OrderflowReplay, _sym_cfg, DepthEvent, TradeEvent,
    STOP_TICKS, TARGET_TICKS, TIME_EXIT_SECONDS,
    SWEEP_SIZE_THRESHOLD, Signal, MAX_RISK_PER_TRADE, Trade
)

# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 50000
DEFAULT_CHECKPOINT_DIR = "state/orderflow/checkpoints"
CHECKPOINT_INTERVAL = 100000  # events between checkpoints
MAX_LIVE_EVENTS = 2000000     # keep only last N events in memory

# ─── Logging ────────────────────────────────────────────────────────────────

def log(msg: str): print(f"[{datetime.now().isoformat()}] {msg}", flush=True)
def warn(msg: str): log(f"⚠️  {msg}")
def ok(msg: str): log(f"✅ {msg}")

# ─── Performance Monitoring ─────────────────────────────────────────────────

@dataclass
class PerformanceMetrics:
    start_time: float = 0.0
    events_processed: int = 0
    bytes_processed: int = 0
    batches_completed: int = 0
    peak_memory_mb: float = 0.0
    avg_events_per_sec: float = 0.0
    avg_batch_latency_ms: float = 0.0
    replay_lag_seconds: float = 0.0
    queue_depth: int = 0
    
    def snapshot(self):
        mem = psutil.Process().memory_info().rss / 1024 / 1024 if HAS_PSUTIL else 0.0
        self.peak_memory_mb = max(self.peak_memory_mb, mem)
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            self.avg_events_per_sec = self.events_processed / elapsed
        return mem

def write_perf_report(metrics: PerformanceMetrics, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    elapsed = time.time() - metrics.start_time
    report = {
        "timestamp": datetime.now().isoformat(),
        "events_processed": metrics.events_processed,
        "bytes_processed_mb": metrics.bytes_processed / 1024 / 1024,
        "batches_completed": metrics.batches_completed,
        "peak_memory_mb": metrics.peak_memory_mb,
        "avg_events_per_sec": metrics.avg_events_per_sec,
        "avg_batch_latency_ms": metrics.avg_batch_latency_ms,
        "replay_lag_seconds": metrics.replay_lag_seconds,
        "queue_depth": metrics.queue_depth,
        "elapsed_seconds": elapsed,
        "throughput_million_per_hour": (metrics.events_processed / elapsed * 3600 / 1_000_000) if elapsed > 0 else 0,
    }
    (output_dir / "performance_report.json").write_text(json.dumps(report, indent=2))
    
    md = f"""# Performance Report
- Events: {metrics.events_processed:,}
- Batches: {metrics.batches_completed}
- Peak Memory: {metrics.peak_memory_mb:.1f} MB
- Throughput: {metrics.avg_events_per_sec:,.0f} evt/s
- Lag: {metrics.replay_lag_seconds:.1f}s
- Elapsed: {elapsed:.1f}s
"""
    (output_dir / "performance_report.md").write_text(md)

# ─── Checkpointing ──────────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    file_path: str
    file_offset: int        # byte offset in gzip/jsonl
    seq_last: int
    ts_last: str
    events_total: int
    trades_completed: List[dict]
    equity_running: float
    bar_state: dict
    ob_state: dict
    
class CheckpointManager:
    def __init__(self, checkpoint_dir: Path):
        self.dir = checkpoint_dir
        self.dir.mkdir(parents=True, exist_ok=True)
    
    def save(self, cp: Checkpoint, label: str = "latest"):
        path = self.dir / f"checkpoint_{label}.json"
        path.write_text(json.dumps(asdict(cp), indent=2, default=str))
    
    def load(self, label: str = "latest") -> Optional[Checkpoint]:
        path = self.dir / f"checkpoint_{label}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Checkpoint(**data)
    
    def get_file_offset(self, label: str = "latest") -> int:
        cp = self.load(label)
        return cp.file_offset if cp else 0

# ─── Streaming JSONL Reader ─────────────────────────────────────────────────

def stream_jsonl_events(filepath: Path, start_offset: int = 0):
    """Yield parsed JSON records with byte offset info. Streaming only."""
    opener = gzip.open if str(filepath).endswith('.gz') else open
    
    with opener(filepath, 'rt') as f:
        if start_offset > 0:
            if hasattr(f, 'seek'):
                f.seek(start_offset)
            else:
                # gz doesn't support seek, skip lines
                for _ in range(start_offset):
                    next(f, None)
        
        offset = start_offset
        for line in f:
            offset += len(line.encode('utf-8'))
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rec['_file_offset'] = offset
                yield rec
            except json.JSONDecodeError:
                continue

# ─── Incremental Replay Engine ──────────────────────────────────────────────

class IncrementalReplay:
    """Stateful replay engine that processes events incrementally."""
    
    def __init__(self, bar_secs: int = 5):
        self.bar_secs = bar_secs
        self.events = deque(maxlen=MAX_LIVE_EVENTS)
        self.bars: Dict[str, List] = defaultdict(list)
        self.cur_bar: Dict[str, Optional] = {}
        self.ob: Dict[str, Dict] = defaultdict(lambda: {"bid": {}, "ask": {}})
        self.sweeps = []
        self.signals = []
        self.trades = []
        self.equity = []
        self.trade_hist = defaultdict(lambda: deque(maxlen=200))
        
        # Running state
        self.open_trade = None
        self.equity_running = 0.0
        self.event_count = 0
        
    def process_events(self, events: Iterator[dict]) -> Iterator[dict]:
        """Process events incrementally. Yields back status."""
        for rec in events:
            self.event_count += 1
            ev = self._to_event(rec)
            if not ev:
                continue
            
            self.events.append(ev)
            
            if isinstance(ev, DepthEvent):
                self._on_depth(ev)
            elif isinstance(ev, TradeEvent):
                self._on_trade(ev)
                # Check signals/exits on trade events only
                self._check_position_management(ev)
            
            # Periodic yield for checkpointing
            if self.event_count % CHECKPOINT_INTERVAL == 0:
                yield {"type": "checkpoint", "events": self.event_count}
        
        yield {"type": "complete", "events": self.event_count}
    
    def _to_event(self, rec: dict):
        """Convert JSON record to event object."""
        etype = rec.get('event_type')
        ts_str = rec.get('ts_event') or rec.get('ts_recv')
        if not ts_str:
            return None
        
        try:
            if ts_str.endswith('Z'):
                ts_str = ts_str[:-1] + '+00:00'
            from datetime import datetime
            ts = datetime.fromisoformat(ts_str)
        except:
            return None
        
        sym = str(rec.get('symbol', ''))
        price = rec.get('price')
        if price is None:
            return None
        try:
            price = float(price)
        except:
            return None
        
        size = int(rec.get('size', 0) or 0)
        
        if etype == 'depth':
            return DepthEvent(ts=ts, symbol=sym, price=price,
                            size=size, side=rec.get('side', 'bid'))
        elif etype == 'trade':
            agg = 'sell' if (rec.get('side') == 'sell' or rec.get('is_bid_aggressor')) else 'buy'
            return TradeEvent(ts=ts, symbol=sym, price=price,
                            size=size, aggressor=agg)
        return None
    
    def _on_depth(self, ev: DepthEvent):
        side = self.ob[ev.symbol][ev.side]
        if ev.size == 0:
            side.pop(ev.price, None)
        else:
            side[ev.price] = ev.size
        bar = self._bar(ev.symbol, ev.ts, ev.price)
        if ev.side == 'bid':
            bar.bid_vol[ev.price] = ev.size
        else:
            bar.ask_vol[ev.price] = ev.size
    
    def _on_trade(self, ev: TradeEvent):
        bar = self._bar(ev.symbol, ev.ts, ev.price)
        if ev.aggressor == 'buy':
            bar.ask_vol[ev.price] = bar.ask_vol.get(ev.price, 0) + ev.size
            bar.delta += ev.size
        else:
            bar.bid_vol[ev.price] = bar.bid_vol.get(ev.price, 0) + ev.size
            bar.delta -= ev.size
        bar.total_vol += ev.size
        bar.close_p = ev.price
        bar.high_p = max(bar.high_p, ev.price)
        bar.low_p = min(bar.low_p, ev.price)
        self.trade_hist[ev.symbol].append(ev)
    
    def _bar(self, sym, ts, price):
        from datetime import timedelta
        bar_ts = ts.replace(second=(ts.second // self.bar_secs) * self.bar_secs, microsecond=0)
        b = self.cur_bar.get(sym)
        if b is None or bar_ts >= b.close_ts:
            b = type('FootprintBar', (), {
                'symbol': sym, 'open_ts': bar_ts,
                'close_ts': bar_ts + timedelta(seconds=self.bar_secs),
                'open_p': price, 'high_p': price, 'low_p': price, 'close_p': price,
                'bid_vol': {}, 'ask_vol': {}, 'delta': 0, 'total_vol': 0
            })()
            self.cur_bar[sym] = b
            self.bars[sym].append(b)
        return b
    
    def _check_position_management(self, ev: TradeEvent):
        """Minimal position management for speed."""
        from replay_orderflow_jsonl import Trade, Signal, MAX_RISK_PER_TRADE, SWEEP_SIZE_THRESHOLD
        
        # Manage open position
        if self.open_trade:
            if ev.symbol == self.open_trade.symbol:
                self._update_mae_mfe(self.open_trade, ev)
                exit_result = self._evaluate_exit(self.open_trade, ev)
                if exit_result:
                    self.open_trade.exit_ts = ev.ts
                    self.open_trade.exit_price = exit_result['fill_price']
                    self.open_trade.exit_reason = exit_result['reason']
                    self.open_trade.pnl = self._pnl(self.open_trade)
                    self.trades.append(self.open_trade)
                    self.equity_running += self.open_trade.pnl
                    self.equity.append((ev.ts, self.equity_running))
                    self.open_trade = None
        
        # New signal
        if not self.open_trade and ev.symbol:
            sig = self._check_signal(ev)
            if sig:
                cfg = _sym_cfg(ev.symbol)
                contracts = max(1, int(MAX_RISK_PER_TRADE / (STOP_TICKS * cfg['tick_value'])))
                self.open_trade = Trade(
                    entry_ts=ev.ts, exit_ts=None, symbol=ev.symbol,
                    direction=sig.direction, entry_price=ev.price,
                    exit_price=None, contracts=contracts,
                    pnl=0.0, mae=0.0, mfe=0.0, exit_reason='', signal=sig.reason,
                    trailing_stop=0.0)
                self.signals.append(sig)
    
    def _update_mae_mfe(self, tr, ev: TradeEvent):
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']
        if tr.direction == 'LONG':
            tr.mae = max(tr.mae, (tr.entry_price - ev.price) / tsz)
            tr.mfe = max(tr.mfe, (ev.price - tr.entry_price) / tsz)
        else:
            tr.mae = max(tr.mae, (ev.price - tr.entry_price) / tsz)
            tr.mfe = max(tr.mfe, (tr.entry_price - ev.price) / tsz)
    
    def _evaluate_exit(self, tr, ev: TradeEvent):
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']
        
        if tr.direction == 'LONG':
            stop_level = tr.entry_price - STOP_TICKS * tsz
            tgt_level = tr.entry_price + TARGET_TICKS * tsz
            if ev.price <= stop_level:
                return {'reason': 'stop', 'fill_price': ev.price}
            if ev.price >= tgt_level:
                return {'reason': 'target', 'fill_price': ev.price}
            if tr.trailing_stop > 0.0 and ev.price <= tr.trailing_stop:
                return {'reason': 'trailing_stop', 'fill_price': ev.price}
        else:
            stop_level = tr.entry_price + STOP_TICKS * tsz
            tgt_level = tr.entry_price - TARGET_TICKS * tsz
            if ev.price >= stop_level:
                return {'reason': 'stop', 'fill_price': ev.price}
            if ev.price <= tgt_level:
                return {'reason': 'target', 'fill_price': ev.price}
            if tr.trailing_stop > 0.0 and ev.price >= tr.trailing_stop:
                return {'reason': 'trailing_stop', 'fill_price': ev.price}
        
        if (ev.ts - tr.entry_ts).total_seconds() >= TIME_EXIT_SECONDS:
            return {'reason': 'time', 'fill_price': ev.price}
        return None
    
    def _pnl(self, tr):
        if tr.exit_price is None:
            return 0.0
        cfg = _sym_cfg(tr.symbol)
        tsz, tv = cfg['tick_size'], cfg['tick_value']
        if tr.direction == 'LONG':
            ticks = (tr.exit_price - tr.entry_price) / tsz
        else:
            ticks = (tr.entry_price - tr.exit_price) / tsz
        return ticks * tv * tr.contracts - cfg['comm'] * tr.contracts
    
    def _check_signal(self, ev: TradeEvent):
        from replay_orderflow_jsonl import Signal
        sym = ev.symbol
        ob = self.ob.get(sym, {})
        hist = list(self.trade_hist.get(sym, deque()))
        if len(hist) < 10:
            return None
        
        bids = sorted(ob.get('bid', {}).keys(), reverse=True)
        asks = sorted(ob.get('ask', {}).keys())
        if not bids or not asks:
            return None
        
        recent = [t for t in hist if (ev.ts - t.ts).total_seconds() <= 60]
        if not recent:
            return None
        delta = sum(t.size if t.aggressor == 'buy' else -t.size for t in recent)
        
        if (ev.aggressor == 'sell' and ev.size >= SWEEP_SIZE_THRESHOLD
                and delta < -50 and len(bids) >= 3):
            return Signal(ts=ev.ts, symbol=sym, direction='LONG', price=ev.price,
                         reason='sweep_support_div_')
        
        if (ev.aggressor == 'buy' and delta > 30
                and len(self.bars.get(sym, [])) >= 3):
            lows = [b.low_p for b in self.bars[sym][-3:]]
            if len(lows) >= 2 and ev.price > lows[-2]:
                return Signal(ts=ev.ts, symbol=sym, direction='LONG', price=ev.price,
                             reason='reclaim')
        
        if (ev.aggressor == 'buy' and ev.size >= SWEEP_SIZE_THRESHOLD
                and delta > 50 and len(asks) >= 3):
            return Signal(ts=ev.ts, symbol=sym, direction='SHORT', price=ev.price,
                         reason='sweep_resist_exhaust_')
        
        if (ev.aggressor == 'sell' and delta < -30
                and len(self.bars.get(sym, [])) >= 3):
            highs = [b.high_p for b in self.bars[sym][-3:]]
            if len(highs) >= 2 and ev.price < highs[-2]:
                return Signal(ts=ev.ts, symbol=sym, direction='SHORT', price=ev.price,
                             reason='failed_break')
        
        return None
    
    def export(self, output_dir: Path):
        """Export all artifacts."""
        output_dir.mkdir(parents=True, exist_ok=True)
        self._csv_trades(output_dir / 'trades.csv')
        self._csv_equity(output_dir / 'equity_curve.csv')
        self._csv_sweeps(output_dir / 'sweep_events.csv')
        self._csv_footprint(output_dir / 'footprint_events.csv')
    
    def _csv_trades(self, p):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['entry_ts', 'exit_ts', 'symbol', 'direction', 'entry_price',
                       'exit_price', 'contracts', 'pnl', 'mae', 'mfe', 'exit_reason', 'signal'])
            for t in self.trades:
                w.writerow([t.entry_ts.isoformat(), t.exit_ts.isoformat() if t.exit_ts else '',
                           t.symbol, t.direction, f'{t.entry_price:.2f}',
                           f'{t.exit_price:.2f}' if t.exit_price else '', t.contracts,
                           f'{t.pnl:.2f}', f'{t.mae:.2f}', f'{t.mfe:.2f}',
                           t.exit_reason, t.signal])
    
    def _csv_equity(self, p):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['timestamp', 'equity'])
            for ts, eq in self.equity:
                w.writerow([ts.isoformat(), f'{eq:.2f}'])
    
    def _csv_sweeps(self, p):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['ts', 'symbol', 'price', 'size', 'sweep_type', 'context'])
            for s in self.sweeps:
                w.writerow([s.ts.isoformat(), s.symbol, f'{s.price:.2f}',
                           s.size, s.sweep_type, s.context])
    
    def _csv_footprint(self, p):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['open_ts', 'symbol', 'open', 'high', 'low', 'close', 'delta', 'total_vol'])
            for sym, bars in self.bars.items():
                for b in bars:
                    w.writerow([b.open_ts.isoformat(), sym, f'{b.open_p:.2f}',
                               f'{b.high_p:.2f}', f'{b.low_p:.2f}', f'{b.close_p:.2f}',
                               b.delta, b.total_vol])

# ─── Batch Processor ────────────────────────────────────────────────────────

def process_batches(filepath: Path, batch_size: int, start_offset: int = 0):
    """Process file in batches, yielding results per batch."""
    eng = IncrementalReplay(bar_secs=5)
    events = stream_jsonl_events(filepath, start_offset)
    batch = []
    
    for rec in events:
        batch.append(rec)
        if len(batch) >= batch_size:
            yield from _process_batch(eng, batch)
            batch = []
            gc.collect()  # Explicit cleanup
    
    if batch:
        yield from _process_batch(eng, batch)
    
    yield {"type": "final", "engine": eng}

def _process_batch(eng: IncrementalReplay, batch: List[dict]):
    """Process one batch and yield status."""
    start = time.time()
    
    # Create generator from batch
    def gen():
        for rec in batch:
            yield rec
    
    for status in eng.process_events(gen()):
        yield status
    
    latency_ms = (time.time() - start) * 1000
    yield {"type": "batch_complete", "size": len(batch), "latency_ms": latency_ms}

# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-i', '--input', required=True)
    ap.add_argument('-o', '--output-dir', default=DEFAULT_BATCH_SIZE)
    ap.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument('--checkpoint-dir', default=DEFAULT_CHECKPOINT_DIR)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--benchmark', action='store_true')
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    
    filepath = Path(args.input)
    if not filepath.exists():
        log(f"File not found: {filepath}")
        return
    
    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cp_mgr = CheckpointManager(checkpoint_dir)
    
    perf = PerformanceMetrics(start_time=time.time())
    
    log(f"Streaming replay: {filepath.name}")
    log(f"Batch size: {args.batch_size:,}")
    
    # Determine start offset
    start_offset = 0
    if args.resume:
        cp = cp_mgr.load()
        if cp and cp.file_path == str(filepath):
            start_offset = cp.file_offset
            log(f"Resuming from offset: {start_offset:,}")
    
    # Process
    batch_count = 0
    for result in process_batches(filepath, args.batch_size, start_offset):
        if result.get('type') == 'batch_complete':
            batch_count += 1
            perf.batches_completed = batch_count
            perf.snapshot()
            
            if batch_count % 10 == 0:
                log(f"Batch {batch_count}: {result['size']:,} events, "
                   f"{result['latency_ms']:.0f}ms, "
                   f"mem={perf.peak_memory_mb:.1f}MB")
        
        elif result.get('type') == 'checkpoint':
            # Save checkpoint
            pass  # TODO
        
        elif result.get('type') == 'final':
            eng = result['engine']
            log(f"Complete: {eng.event_count:,} events, {len(eng.trades)} trades")
            
            output_dir = Path(args.output_dir)
            eng.export(output_dir)
            
            # Metrics
            perf.events_processed = eng.event_count
            mem = perf.snapshot()
            
            # Write reports
            write_perf_report(perf, output_dir)
            
            # Status
            status = {
                "events": eng.event_count,
                "trades": len(eng.trades),
                "signals": len(eng.signals),
                "equity": eng.equity_running,
                "memory_mb": mem,
                "throughput": perf.avg_events_per_sec,
            }
            (output_dir / "status.json").write_text(json.dumps(status, indent=2))
            
            ok(f"Exported to {output_dir}")

if __name__ == '__main__':
    main()
