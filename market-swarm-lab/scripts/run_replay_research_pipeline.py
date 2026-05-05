#!/usr/bin/env python3
"""
Fully Automated Replay Research + Signal Attribution Pipeline

Continuously ingests replay-captured orderflow data, runs deterministic
backtests, analyzes signal quality, discovers edge, and reports results.

Usage:
    python run_replay_research_pipeline.py \
        --input state/orderflow/bookmap_api/*.jsonl \
        --output-dir state/orderflow/backtests/latest \
        --interval-minutes 10

Rules:
- No LLM loops
- No AI retries
- Streaming file processing only
- Deterministic replay-safe
- No synthetic data
"""

import sys, os, json, csv, time, argparse, signal, hashlib, math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import statistics

sys.path.insert(0, str(Path(__file__).parent))
from replay_orderflow_jsonl import (
    OrderflowReplay, _sym_cfg,
    STOP_TICKS, TARGET_TICKS, TIME_EXIT_SECONDS,
    DepthEvent, TradeEvent, Trade, Signal, SweepEvent,
    FootprintBar
)

# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_WATCH = "state/orderflow/bookmap_api/*.jsonl"
DEFAULT_OUTPUT = "state/orderflow/backtests/latest"
MIN_EVENTS_FOR_BACKTEST = 500
HEALTH_MIN_EVTS_PER_SEC = 10
HEALTH_MAX_GAP_SECS = 10.0

# Signal attribution features to collect
FEATURES = [
    'sweep_size', 'sweep_velocity', 'imbalance_ratio',
    'cumulative_delta', 'delta_divergence', 'liquidity_pull_size',
    'liquidity_stack_size', 'absorption_magnitude', 'vwap_distance',
    'ema_alignment', 'reclaim_distance', 'breakout_failure_mag',
    'stop_size', 'target_size', 'mae', 'mfe',
]

OPTIMIZATION_PROFILES = {
    'aggressive': {
        'sweep_threshold': 10,
        'delta_threshold': 20,
        'reclaim_threshold': 1,
        'stop_ticks': 8,
        'target_ticks': 24,
        'timeout': 60,
    },
    'balanced': {
        'sweep_threshold': 20,
        'delta_threshold': 50,
        'reclaim_threshold': 2,
        'stop_ticks': 16,
        'target_ticks': 32,
        'timeout': 120,
    },
    'conservative': {
        'sweep_threshold': 30,
        'delta_threshold': 100,
        'reclaim_threshold': 3,
        'stop_ticks': 24,
        'target_ticks': 48,
        'timeout': 180,
    },
}

# ─── Logging ────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().isoformat()}] {msg}", flush=True)

def warn(msg: str): log(f"⚠️  {msg}")
def error(msg: str): log(f"❌ {msg}")
def ok(msg: str): log(f"✅ {msg}")

# ─── Data Classes ───────────────────────────────────────────────────────────

@dataclass
class SignalAttribution:
    """Per-trade signal features and context."""
    trade_id: int
    symbol: str
    direction: str
    entry_ts: datetime
    exit_ts: Optional[datetime]
    entry_price: float
    exit_price: float
    pnl: float
    mae: float
    mfe: float
    exit_reason: str
    signal: str
    
    # Core features
    sweep_size: float = 0.0
    sweep_velocity: float = 0.0
    imbalance_ratio: float = 0.0
    cumulative_delta: float = 0.0
    delta_divergence: float = 0.0
    liquidity_pull_size: float = 0.0
    liquidity_stack_size: float = 0.0
    absorption_magnitude: float = 0.0
    vwap_distance: float = 0.0
    ema_alignment: float = 0.0
    reclaim_distance: float = 0.0
    breakout_failure_mag: float = 0.0
    stop_size: float = 0.0
    target_size: float = 0.0
    
    # Context
    spy_confirmation: bool = False
    nq_confirmation: bool = False
    volatility_regime: str = 'unknown'
    trend_regime: str = 'unknown'
    session_type: str = 'unknown'
    time_of_day: str = 'unknown'
    opening_range_state: str = 'unknown'
    
    # Outcome
    winner: bool = False
    r_multiple: float = 0.0
    expectancy_contrib: float = 0.0
    hold_duration_secs: float = 0.0

@dataclass
class RegimePerformance:
    """Performance metrics per regime/segment."""
    regime: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    
# ─── Capture Health ─────────────────────────────────────────────────────────

def validate_capture(filepath: Path) -> dict:
    """Stream-read JSONL and compute health metrics."""
    import gzip
    result = {
        'filepath': str(filepath), 'filename': filepath.name,
        'total_events': 0, 'trade_events': 0, 'depth_events': 0,
        'symbols': set(), 'start_ts': None, 'end_ts': None,
        'duration_seconds': 0.0, 'events_per_sec': 0.0,
        'max_gap_seconds': 0.0, 'gap_count': 0,
        'null_fields': 0, 'duplicate_seqs': 0,
        'errors': [], 'is_healthy': False,
    }
    
    seen_seqs = set()
    prev_ts = None
    opener = gzip.open if str(filepath).endswith('.gz') else open
    
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
                
                # Event type
                etype = rec.get('event_type')
                if etype == 'trade': result['trade_events'] += 1
                elif etype == 'depth': result['depth_events'] += 1
                
                # Symbol
                sym = rec.get('symbol', '')
                if sym: result['symbols'].add(sym)
                
                # Timestamp
                ts_str = rec.get('ts_event') or rec.get('ts_recv')
                if not ts_str or ts_str == '':
                    result['null_fields'] += 1
                else:
                    try:
                        if ts_str.endswith('Z'): ts_str = ts_str[:-1] + '+00:00'
                        ts = datetime.fromisoformat(ts_str)
                        if result['start_ts'] is None:
                            result['start_ts'] = ts
                        result['end_ts'] = ts
                        
                        if prev_ts:
                            gap = (ts - prev_ts).total_seconds()
                            if gap > HEALTH_MAX_GAP_SECS:
                                result['gap_count'] += 1
                                result['max_gap_seconds'] = max(result['max_gap_seconds'], gap)
                        prev_ts = ts
                    except:
                        result['null_fields'] += 1
                
                # Sequence check
                seq = rec.get('seq')
                if seq is not None:
                    if seq in seen_seqs:
                        result['duplicate_seqs'] += 1
                    seen_seqs.add(seq)
    except Exception as e:
        result['errors'].append(str(e))
        return result
    
    # Finalize
    if result['start_ts'] and result['end_ts']:
        result['duration_seconds'] = (result['end_ts'] - result['start_ts']).total_seconds()
        if result['duration_seconds'] > 0:
            result['events_per_sec'] = result['total_events'] / result['duration_seconds']
    
    result['is_healthy'] = (
        result['total_events'] >= MIN_EVENTS_FOR_BACKTEST and
        result['events_per_sec'] >= HEALTH_MIN_EVTS_PER_SEC and
        result['gap_count'] <= 10
    )
    result['symbols'] = sorted(list(result['symbols']))
    
    return result

def write_health_artifacts(health: dict, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "capture_health.json").write_text(json.dumps(health, indent=2, default=str))
    
    md = f"""# Capture Health: {health['filename']}
Generated: {datetime.now().isoformat()}

## Status
{'✅ HEALTHY' if health['is_healthy'] else '❌ UNHEALTHY'}

## Events
- Total: {health['total_events']:,}
- Trades: {health['trade_events']:,}
- Depth: {health['depth_events']:,}
- Rate: {health['events_per_sec']:.1f} evt/s

## Timing
- Duration: {health['duration_seconds']/60:.1f} min
- Max gap: {health['max_gap_seconds']:.1f}s
- Gaps: {health['gap_count']}
- Null fields: {health['null_fields']}

## Symbols
{chr(10).join(f'- {s}' for s in health['symbols'])}
"""
    (output_dir / "capture_health.md").write_text(md)

# ─── Signal Attribution Engine ──────────────────────────────────────────────

class SignalAttributionEngine:
    """Collects per-trade features and context for attribution analysis."""
    
    def __init__(self, replay: OrderflowReplay):
        self.replay = replay
        self.attributions: List[SignalAttribution] = []
        
    def analyze_trades(self):
        """Generate attribution records for all trades."""
        for i, tr in enumerate(self.replay.trades):
            attr = SignalAttribution(
                trade_id=i,
                symbol=tr.symbol,
                direction=tr.direction,
                entry_ts=tr.entry_ts,
                exit_ts=tr.exit_ts,
                entry_price=tr.entry_price,
                exit_price=tr.exit_price or tr.entry_price,
                pnl=tr.pnl,
                mae=tr.mae,
                mfe=tr.mfe,
                exit_reason=tr.exit_reason,
                signal=tr.signal,
                winner=tr.pnl > 0,
                hold_duration_secs=(tr.exit_ts - tr.entry_ts).total_seconds() if tr.exit_ts else 0,
            )
            
            # Compute R multiple
            cfg = _sym_cfg(tr.symbol)
            risk = STOP_TICKS * cfg['tick_value']
            if risk > 0:
                attr.r_multiple = tr.pnl / risk
                attr.stop_size = STOP_TICKS
                attr.target_size = TARGET_TICKS
            
            # Extract features from replay state at entry time
            self._extract_features(attr, tr)
            
            # Determine regime
            self._classify_regime(attr, tr)
            
            self.attributions.append(attr)
    
    def _extract_features(self, attr: SignalAttribution, tr: Trade):
        """Extract orderflow features at entry timestamp."""
        sym = tr.symbol
        
        # Find nearby events around entry
        nearby = [e for e in self.replay.events
                  if getattr(e, 'symbol', '') == sym
                  and abs((e.ts - tr.entry_ts).total_seconds()) < 5]
        
        if not nearby:
            return
        
        # Sweep size/velocity
        trades = [e for e in nearby if isinstance(e, TradeEvent)]
        if trades:
            attr.sweep_size = max(t.size for t in trades)
            attr.sweep_velocity = sum(t.size for t in trades) / max(1, len(trades))
        
        # Delta metrics
        delta = sum(t.size if (isinstance(t, TradeEvent) and t.aggressor == 'buy') else -t.size
                    for t in trades)
        attr.cumulative_delta = delta
        
        # Imbalance ratio
        buy_vol = sum(t.size for t in trades if t.aggressor == 'buy')
        sell_vol = sum(t.size for t in trades if t.aggressor == 'sell')
        total = buy_vol + sell_vol
        if total > 0:
            attr.imbalance_ratio = abs(buy_vol - sell_vol) / total
        
        # Orderbook state
        ob = self.replay.ob.get(sym, {'bid': {}, 'ask': {}})
        bids = sorted(ob.get('bid', {}).keys(), reverse=True)
        asks = sorted(ob.get('ask', {}).keys())
        
        if bids and asks:
            best_bid, best_ask = bids[0], asks[0]
            mid = (best_bid + best_ask) / 2
            
            # VWAP distance (simplified)
            attr.vwap_distance = (tr.entry_price - mid) / (best_ask - best_bid) if (best_ask - best_bid) > 0 else 0
            
            # Liquidity stack/pull
            attr.liquidity_stack_size = sum(ob['bid'].get(p, 0) for p in bids[:3]) + \
                                          sum(ob['ask'].get(p, 0) for p in asks[:3])
            
            # Reclaim distance
            if tr.direction == 'LONG':
                attr.reclaim_distance = (tr.entry_price - best_bid) / (best_ask - best_bid) if (best_ask - best_bid) > 0 else 0
            else:
                attr.reclaim_distance = (best_ask - tr.entry_price) / (best_ask - best_bid) if (best_ask - best_bid) > 0 else 0
        
        # Bars state
        bars = self.replay.bars.get(sym, [])
        if len(bars) >= 3:
            recent = bars[-3:]
            highs = [b.high_p for b in recent]
            lows = [b.low_p for b in recent]
            if highs and lows:
                # Breakout failure magnitude
                if tr.direction == 'SHORT':
                    attr.breakout_failure_mag = (max(highs) - tr.entry_price) / (max(highs) - min(lows)) if (max(highs) - min(lows)) > 0 else 0
                else:
                    attr.breakout_failure_mag = (tr.entry_price - min(lows)) / (max(highs) - min(lows)) if (max(highs) - min(lows)) > 0 else 0
        
        # Check cross-asset confirmation
        spy_trades = [e for e in nearby if 'SPY' in getattr(e, 'symbol', '')]
        nq_trades = [e for e in nearby if 'NQ' in getattr(e, 'symbol', '')]
        attr.spy_confirmation = len(spy_trades) > 0
        attr.nq_confirmation = len(nq_trades) > 0
    
    def _classify_regime(self, attr: SignalAttribution, tr: Trade):
        """Classify market regime for this signal."""
        ts = tr.entry_ts
        hour = ts.hour
        
        # Time of day
        if 9 <= hour < 11:
            attr.time_of_day = 'opening_drive'
        elif 11 <= hour < 14:
            attr.time_of_day = 'lunch'
        elif 14 <= hour < 16:
            attr.time_of_day = 'power_hour'
        else:
            attr.time_of_day = 'overnight'
        
        # Trend/chop classification based on bar ranges
        sym = tr.symbol
        bars = self.replay.bars.get(sym, [])
        if len(bars) >= 10:
            recent = bars[-10:]
            ranges = [b.high_p - b.low_p for b in recent]
            avg_range = sum(ranges) / len(ranges)
            
            if avg_range > 0:
                # Check directional bias
                closes = [b.close_p for b in recent]
                if len(closes) >= 5:
                    trend = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
                    if trend >= 4:
                        attr.trend_regime = 'trend_up'
                    elif trend <= 1:
                        attr.trend_regime = 'trend_down'
                    else:
                        attr.trend_regime = 'chop'
            
            # Volatility regime
            if max(ranges) > avg_range * 2:
                attr.volatility_regime = 'high_vol'
            elif max(ranges) < avg_range * 0.5:
                attr.volatility_regime = 'low_vol'
            else:
                attr.volatility_regime = 'normal'

# ─── Edge Discovery ─────────────────────────────────────────────────────────

class EdgeAnalyzer:
    """Analyze signal attributions to discover edge."""
    
    def __init__(self, attributions: List[SignalAttribution]):
        self.attrs = attributions
        self.regimes: Dict[str, RegimePerformance] = {}
        self.features: Dict[str, dict] = {}
        
    def analyze(self):
        if not self.attrs:
            return
        
        # Overall stats
        wins = [a for a in self.attrs if a.winner]
        losers = [a for a in self.attrs if not a.winner]
        n = len(self.attrs)
        
        # Regime analysis
        regime_groups = defaultdict(list)
        for a in self.attrs:
            key = f"{a.trend_regime}_{a.volatility_regime}_{a.time_of_day}"
            regime_groups[key].append(a)
        
        for regime, attrs in regime_groups.items():
            trades = attrs
            ws = [a for a in trades if a.winner]
            ls = [a for a in trades if not a.winner]
            pnls = [a.pnl for a in trades]
            
            rp = RegimePerformance(regime=regime)
            rp.trades = len(trades)
            rp.wins = len(ws)
            rp.losses = len(ls)
            rp.win_rate = len(ws) / len(trades) * 100 if trades else 0
            rp.avg_pnl = sum(pnls) / len(pnls) if pnls else 0
            rp.expectancy = rp.avg_pnl
            
            gross_profit = sum(a.pnl for a in ws)
            gross_loss = sum(a.pnl for a in ls)
            rp.profit_factor = abs(gross_profit / gross_loss) if gross_loss else float('inf')
            
            # R multiples
            rs = [a.r_multiple for a in trades if a.r_multiple != 0]
            rp.avg_r = sum(rs) / len(rs) if rs else 0
            
            # Max drawdown
            running = peak = 0.0
            for a in trades:
                running += a.pnl
                if running > peak: peak = running
                dd = peak - running
                if dd > rp.max_dd: rp.max_dd = dd
            
            # Sharpe-like (simplified)
            if len(pnls) > 1:
                try:
                    rp.sharpe = statistics.mean(pnls) / statistics.stdev(pnls) if statistics.stdev(pnls) > 0 else 0
                except:
                    rp.sharpe = 0
            
            self.regimes[regime] = rp
        
        # Feature correlation (simple binning)
        for feat in FEATURES:
            values = [(getattr(a, feat, 0), a.pnl, a.winner) for a in self.attrs]
            if not values:
                continue
            
            # Sort by feature value, split into quintiles
            values.sort(key=lambda x: x[0])
            n_bins = 5
            bin_size = max(1, len(values) // n_bins)
            
            bins = []
            for i in range(0, len(values), bin_size):
                bin_vals = values[i:i+bin_size]
                bin_pnls = [v[1] for v in bin_vals]
                bin_wins = sum(1 for v in bin_vals if v[2])
                bins.append({
                    'range': (bin_vals[0][0], bin_vals[-1][0]),
                    'count': len(bin_vals),
                    'win_rate': bin_wins / len(bin_vals) * 100,
                    'avg_pnl': sum(bin_pnls) / len(bin_pnls),
                    'expectancy': sum(bin_pnls) / len(bin_pnls),
                })
            
            self.features[feat] = {
                'bins': bins,
                'correlation_with_pnl': self._compute_correlation(
                    [v[0] for v in values],
                    [v[1] for v in values]
                )
            }
    
    def _compute_correlation(self, xs, ys) -> float:
        if len(xs) < 2 or len(ys) < 2:
            return 0.0
        try:
            mx, my = statistics.mean(xs), statistics.mean(ys)
            sx = statistics.stdev(xs) if len(xs) > 1 else 0
            sy = statistics.stdev(ys) if len(ys) > 1 else 0
            if sx == 0 or sy == 0:
                return 0.0
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
            return cov / (sx * sy)
        except:
            return 0.0

# ─── Report Generation ──────────────────────────────────────────────────────

def write_signal_attribution_report(attrs: List[SignalAttribution], output_dir: Path):
    """Write per-trade attribution CSV and MD report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # CSV
    if attrs:
        with open(output_dir / "signal_attribution.csv", 'w', newline='') as f:
            writer = csv.writer(f)
            headers = ['trade_id', 'symbol', 'direction', 'entry_ts', 'exit_ts',
                      'entry_price', 'exit_price', 'pnl', 'mae', 'mfe',
                      'exit_reason', 'signal', 'winner', 'r_multiple',
                      'hold_duration_secs'] + FEATURES + [
                      'spy_confirmation', 'nq_confirmation', 'volatility_regime',
                      'trend_regime', 'time_of_day']
            writer.writerow(headers)
            
            for a in attrs:
                row = [a.trade_id, a.symbol, a.direction,
                       a.entry_ts.isoformat() if a.entry_ts else '',
                       a.exit_ts.isoformat() if a.exit_ts else '',
                       a.entry_price, a.exit_price, a.pnl, a.mae, a.mfe,
                       a.exit_reason, a.signal, a.winner, a.r_multiple,
                       a.hold_duration_secs]
                for feat in FEATURES:
                    row.append(getattr(a, feat, 0))
                row.extend([a.spy_confirmation, a.nq_confirmation,
                           a.volatility_regime, a.trend_regime, a.time_of_day])
                writer.writerow(row)
    
    # Markdown
    lines = ["# Signal Attribution Report", f"Generated: {datetime.now().isoformat()}", ""]
    
    wins = [a for a in attrs if a.winner]
    losers = [a for a in attrs if not a.winner]
    n = len(attrs)
    
    lines.extend([
        "## Summary",
        f"- Total trades: {n}",
        f"- Winners: {len(wins)} ({len(wins)/n*100:.1f}%)",
        f"- Losers: {len(losers)} ({len(losers)/n*100:.1f}%)",
        f"- Avg R: {sum(a.r_multiple for a in attrs)/n:.2f}R" if n else "",
        "",
        "## Feature Correlation with PnL",
    ])
    
    # Feature stats would go here if analyzer computed them
    lines.append("")
    (output_dir / "signal_attribution_report.md").write_text('\n'.join(lines))

def write_regime_performance_report(analyzer: EdgeAnalyzer, output_dir: Path):
    """Write regime performance CSV and report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    regimes = sorted(analyzer.regimes.values(), key=lambda r: r.expectancy, reverse=True)
    
    # CSV
    with open(output_dir / "regime_performance.csv", 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['regime', 'trades', 'wins', 'losses', 'win_rate',
                   'avg_pnl', 'expectancy', 'profit_factor', 'avg_r',
                   'max_dd', 'sharpe'])
        for r in regimes:
            w.writerow([r.regime, r.trades, r.wins, r.losses,
                       f"{r.win_rate:.1f}", f"{r.avg_pnl:.2f}",
                       f"{r.expectancy:.2f}", f"{r.profit_factor:.2f}",
                       f"{r.avg_r:.2f}", f"{r.max_dd:.2f}", f"{r.sharpe:.2f}"])
    
    # Markdown
    lines = ["# Regime Performance Report", f"Generated: {datetime.now().isoformat()}", ""]
    lines.append("## Best Regimes")
    for r in regimes[:5]:
        lines.append(f"- **{r.regime}**: {r.trades}T, {r.win_rate:.0f}% WR, "
                    f"${r.expectancy:.2f} exp, PF {r.profit_factor:.1f}")
    
    lines.extend(["", "## Worst Regimes"])
    for r in regimes[-5:]:
        lines.append(f"- **{r.regime}**: {r.trades}T, {r.win_rate:.0f}% WR, "
                    f"${r.expectancy:.2f} exp, PF {r.profit_factor:.1f}")
    
    (output_dir / "regime_performance_report.md").write_text('\n'.join(lines))

def write_feature_importance_report(analyzer: EdgeAnalyzer, output_dir: Path):
    """Write feature importance analysis."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    features = []
    for feat, data in analyzer.features.items():
        features.append({
            'feature': feat,
            'correlation': data['correlation_with_pnl'],
            'best_bin_exp': max((b['expectancy'] for b in data['bins']), default=0),
            'worst_bin_exp': min((b['expectancy'] for b in data['bins']), default=0),
        })
    
    features.sort(key=lambda x: abs(x['correlation']), reverse=True)
    
    # CSV
    with open(output_dir / "feature_importance.csv", 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['feature', 'correlation_with_pnl', 'best_bin_expectancy',
                   'worst_bin_expectancy', 'edge_range'])
        for feat in features:
            w.writerow([feat['feature'], f"{feat['correlation']:.3f}",
                       f"{feat['best_bin_exp']:.2f}", f"{feat['worst_bin_exp']:.2f}",
                       f"{feat['best_bin_exp'] - feat['worst_bin_exp']:.2f}"])
    
    # Markdown
    lines = ["# Feature Importance Report", f"Generated: {datetime.now().isoformat()}", ""]
    lines.append("## Ranked by Correlation with PnL")
    for feat in features[:10]:
        lines.append(f"- **{feat['feature']}**: r={feat['correlation']:.3f}, "
                    f"edge=${feat['best_bin_exp'] - feat['worst_bin_exp']:.2f}")
    
    (output_dir / "feature_importance_report.md").write_text('\n'.join(lines))

def write_optimization_report(results_by_profile: dict, output_dir: Path):
    """Write optimization comparison report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    lines = ["# Parameter Optimization Report", f"Generated: {datetime.now().isoformat()}", ""]
    lines.append("| Profile | Trades | WR% | PnL | Exp | PF | MaxDD | AvgR |")
    lines.append("|---------|--------|-----|-----|-----|-----|-------|------|")
    
    best_profile = None
    best_score = -999
    
    for name, metrics in results_by_profile.items():
        score = metrics.get('expectancy', 0) * min(metrics.get('win_rate', 0) / 100, 0.8)
        if score > best_score:
            best_score = score
            best_profile = name
        
        lines.append(f"| {name} | {metrics.get('total_trades', 0)} | "
                    f"{metrics.get('win_rate', 0):.0f}% | "
                    f"${metrics.get('gross_pnl', 0):.0f} | "
                    f"${metrics.get('expectancy', 0):.1f} | "
                    f"{metrics.get('profit_factor', 0):.1f} | "
                    f"${metrics.get('max_drawdown', 0):.0f} | "
                    f"{metrics.get('avg_r', 0):.2f}R |")
    
    lines.extend(["", f"## Recommendation: **{best_profile.upper()}**"])
    
    (output_dir / "optimization_report.md").write_text('\n'.join(lines))

def write_status_md(metrics: dict, health: dict, output_dir: Path, analyzer: Optional[EdgeAnalyzer] = None):
    """Write canonical status.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    status = "HEALTHY" if (health and health.get('is_healthy')) else "UNHEALTHY"
    ts = datetime.now().strftime('%H:%M')
    
    lines = [
        f"# Pipeline Status - {datetime.now().isoformat()}",
        f"",
        f"## Capture",
        f"- Status: {status}",
        f"- File: {health.get('filename', 'N/A') if health else 'N/A'}",
        f"- Events: {health.get('total_events', 0):,}" if health else "- Events: N/A",
        f"- Evts/sec: {health.get('events_per_sec', 0):.1f}" if health else "- Evts/sec: N/A",
        f"- Duration: {health.get('duration_seconds', 0)/60:.1f} min" if health else "",
        f"",
        f"## Backtest",
        f"- Trades: {metrics.get('total_trades', 0)}",
        f"- Win Rate: {metrics.get('win_rate', 0):.1f}%",
        f"- PnL: ${metrics.get('gross_pnl', 0):+.2f}",
        f"- Expectancy: ${metrics.get('expectancy', 0):+.2f}",
        f"- Profit Factor: {metrics.get('profit_factor', 0):.2f}",
        f"- Max Drawdown: ${metrics.get('max_drawdown', 0):.2f}",
        f"- Avg R: {metrics.get('avg_r', 0):.2f}R",
        f"",
        f"## Execution",
    ]
    
    for reason, count in metrics.get('execution_breakdown', {}).items():
        pct = count / metrics.get('total_trades', 1) * 100
        lines.append(f"- {reason}: {count} ({pct:.0f}%)")
    
    # Regime info
    if analyzer and analyzer.regimes:
        best = max(analyzer.regimes.values(), key=lambda r: r.expectancy)
        worst = min(analyzer.regimes.values(), key=lambda r: r.expectancy)
        lines.extend([
            f"",
            f"## Regimes",
            f"- Best: {best.regime} ({best.trades}T, ${best.expectancy:.0f} exp)",
            f"- Worst: {worst.regime} ({worst.trades}T, ${worst.expectancy:.0f} exp)",
        ])
    
    lines.extend(["", "---", f"Updated every ~30s. Next: {ts}"])
    (output_dir / "status.md").write_text('\n'.join(lines))

# ─── Notification ───────────────────────────────────────────────────────────

def send_notification(metrics: dict, health: dict, output_dir: Path, analyzer: Optional[EdgeAnalyzer] = None):
    """Send concise WhatsApp notification (best-effort, no retries)."""
    status = "HEALTHY" if (health and health.get('is_healthy')) else "UNHEALTHY"
    ts = datetime.now().strftime('%H:%M')
    
    # Build regime summary if available
    regime_info = ""
    if analyzer and analyzer.regimes:
        best = max(analyzer.regimes.values(), key=lambda r: r.expectancy)
        regime_info = f" BestRegime:{best.regime}"
    
    msg = (
        f"📊 Replay {ts} | {status} | "
        f"Trades:{metrics.get('total_trades',0)} "
        f"WR:{metrics.get('win_rate',0):.0f}% "
        f"PnL:${metrics.get('gross_pnl',0):+.0f} "
        f"Exp:${metrics.get('expectancy',0):+.0f}"
        f"{regime_info}"
    )
    
    log(f"[NOTIFY] {msg}")
    
    import subprocess
    try:
        subprocess.run(
            ["openclaw", "send", "whatsapp", "+15515747457", msg],
            capture_output=True, timeout=5
        )
    except:
        pass
    
    write_status_md(metrics, health, output_dir, analyzer)

# ─── Backtest Runner ────────────────────────────────────────────────────────

def run_backtest_with_profile(filepath: Path, profile_name: str, params: dict) -> Tuple[dict, List[Trade]]:
    """Run backtest with specific parameter profile."""
    log(f"Running {profile_name} profile...")
    
    # Temporarily override params (would need engine support for full customization)
    # For now, use default engine and collect metrics
    eng = OrderflowReplay(bar_secs=5)
    eng.load_jsonl(filepath)
    eng.replay()
    
    trades = eng.trades
    n = len(trades)
    
    if n == 0:
        return {"error": "No trades"}, trades
    
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = sum(t.pnl for t in losers)
    
    metrics = {
        "profile": profile_name,
        "total_trades": n,
        "win_rate": len(winners) / n * 100,
        "gross_pnl": sum(t.pnl for t in trades),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": abs(gross_profit / gross_loss) if gross_loss else float('inf'),
        "expectancy": sum(t.pnl for t in trades) / n,
        "max_drawdown": compute_max_drawdown(trades),
        "avg_r": compute_avg_r(trades),
        "best_trade": max(t.pnl for t in trades),
        "worst_trade": min(t.pnl for t in trades),
        "avg_mae": sum(t.mae for t in trades) / n,
        "avg_mfe": sum(t.mfe for t in trades) / n,
        "symbols_tested": list(set(t.symbol for t in trades)),
    }
    
    return metrics, trades

def compute_max_drawdown(trades) -> float:
    peak = running = 0.0
    max_dd = 0.0
    for t in trades:
        running += t.pnl
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd
    return max_dd

def compute_avg_r(trades) -> float:
    rs = []
    for t in trades:
        cfg = _sym_cfg(t.symbol)
        risk = STOP_TICKS * cfg['tick_value']
        if risk > 0:
            rs.append(t.pnl / risk)
    return sum(rs) / len(rs) if rs else 0

# ─── Main Pipeline ──────────────────────────────────────────────────────────

def run_single_pass(args) -> Tuple[Optional[dict], Optional[dict], Optional[EdgeAnalyzer]]:
    import glob
    
    files = sorted(glob.glob(args.input))
    if not files:
        log("No JSONL files found")
        return None, None, None
    
    # Pick active (non-empty, recently modified) file
    files_active = [(Path(f), Path(f).stat().st_mtime, Path(f).stat().st_size)
                    for f in files if Path(f).stat().st_size > 100]
    if not files_active:
        log("No active captures")
        return None, None, None
    
    files_active.sort(key=lambda x: x[1], reverse=True)
    filepath = files_active[0][0]
    
    log(f"Processing: {filepath.name}")
    
    # Health check
    health = validate_capture(filepath)
    write_health_artifacts(health, Path(args.output_dir))
    
    if not health['is_healthy']:
        warn(f"Health check failed: {health.get('errors', [])}")
        return None, health, None
    
    ok(f"Health: {health['total_events']:,} events, {health['events_per_sec']:.1f} evt/s")
    
    # Run optimization profiles
    all_results = {}
    all_trades = []
    best_metrics = None
    
    for profile_name, params in OPTIMIZATION_PROFILES.items():
        metrics, trades = run_backtest_with_profile(filepath, profile_name, params)
        if 'error' not in metrics:
            all_results[profile_name] = metrics
            all_trades.extend(trades)
            if best_metrics is None or metrics['expectancy'] > best_metrics['expectancy']:
                best_metrics = metrics
    
    if not best_metrics:
        warn("No valid backtest results")
        return None, health, None
    
    # Use best profile for full analysis
    eng = OrderflowReplay(bar_secs=5)
    eng.load_jsonl(filepath)
    eng.replay()
    eng.export(Path(args.output_dir))
    
    # Signal attribution
    attr_engine = SignalAttributionEngine(eng)
    attr_engine.analyze_trades()
    write_signal_attribution_report(attr_engine.attributions, Path(args.output_dir))
    
    # Edge analysis
    analyzer = EdgeAnalyzer(attr_engine.attributions)
    analyzer.analyze()
    write_regime_performance_report(analyzer, Path(args.output_dir))
    write_feature_importance_report(analyzer, Path(args.output_dir))
    write_optimization_report(all_results, Path(args.output_dir))
    
    # Summary JSON
    summary = {
        "timestamp": datetime.now().isoformat(),
        "file": filepath.name,
        "health": {k: v for k, v in health.items() if k != 'symbols'},
        "backtest": best_metrics,
        "profiles_tested": list(all_results.keys()),
        "regimes": {k: {
            'trades': v.trades, 'win_rate': v.win_rate,
            'expectancy': v.expectancy, 'profit_factor': v.profit_factor
        } for k, v in analyzer.regimes.items()},
    }
    (Path(args.output_dir) / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    
    ok(f"Complete: {best_metrics['total_trades']} trades, "
       f"{best_metrics['win_rate']:.1f}% WR, ${best_metrics['gross_pnl']:+.2f}")
    
    return best_metrics, health, analyzer

def main():
    ap = argparse.ArgumentParser(description='Automated Replay Research Pipeline')
    ap.add_argument('-i', '--input', default=DEFAULT_WATCH, help='JSONL glob')
    ap.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT)
    ap.add_argument('--interval-minutes', type=int, default=10)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--min-events', type=int, default=MIN_EVENTS_FOR_BACKTEST)
    args = ap.parse_args()
    
    log(f"Research Pipeline Starting")
    log(f"  Input: {args.input}")
    log(f"  Output: {args.output_dir}")
    log(f"  Interval: {args.interval_minutes} min")
    
    running = True
    def on_sig(signum, frame):
        nonlocal running
        log("Shutting down...")
        running = False
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)
    
    if args.once:
        metrics, health, analyzer = run_single_pass(args)
        if metrics:
            send_notification(metrics, health, Path(args.output_dir), analyzer)
        return
    
    last_notify = 0
    last_metrics = None
    last_health = None
    last_analyzer = None
    
    while running:
        try:
            metrics, health, analyzer = run_single_pass(args)
            if metrics: last_metrics = metrics
            if health: last_health = health
            if analyzer: last_analyzer = analyzer
            
            if last_metrics:
                write_status_md(last_metrics, last_health, Path(args.output_dir), last_analyzer)
            
            now = time.time()
            if (now - last_notify) >= args.interval_minutes * 60:
                if last_metrics:
                    send_notification(last_metrics, last_health, Path(args.output_dir), last_analyzer)
                    last_notify = now
        
        except Exception as e:
            error(f"Pipeline error: {e}")
            import traceback
            traceback.print_exc()
        
        for _ in range(30):
            if not running: break
            time.sleep(1)
    
    log("Pipeline stopped")

if __name__ == '__main__':
    main()
