#!/usr/bin/env python3
"""
phase2_real_backtest.py

PHASE 2: REAL REPLAY VALIDATION

Run corrected backtest using:
- Real May 4 footprint signals (from CSV)
- Real May 4 ESM6 price data (from JSONL)
- No synthetic signals
- No lookahead bias
- Realistic fill modeling (slippage, spread, commission)

This is the authoritative test for edge validation.
"""

import sys
import csv
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

# Add services to path
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor, RealSignal
from entry_exit_planner import EntryExitPlanner
from jsonl_window_accessor import JsonlWindowAccessor


class RealBacktestEngine:
    """
    Real replay-safe backtest engine.
    Uses only REAL signals and REAL data with NO lookahead.
    """
    
    def __init__(self,
                 signals_csv: Path,
                 jsonl_path: Path,
                 output_dir: Path = Path("reports")):
        """Initialize engine."""
        self.signals_csv = Path(signals_csv)
        self.jsonl_path = Path(jsonl_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.extractor = RealSignalExtractor(signals_csv)
        self.planner = EntryExitPlanner(tick_size=0.25)
        self.accessor = JsonlWindowAccessor(jsonl_path, symbol_filter="ESM6.CME@RITHMIC")
        
        self.results = []
        self.stats = {}
    
    def run_backtest(self, max_signals: Optional[int] = None) -> Dict:
        """
        Run backtest on real signals.
        
        Args:
            max_signals: Limit to first N signals (for quick tests)
        
        Returns:
            Statistics dict
        """
        print("[*] Phase 2: Real Replay Backtest")
        print("=" * 70)
        
        # Build JSONL index (one-time cost: ~72s)
        print("[*] Building JSONL index (may take 1-2 minutes)...")
        self.accessor.build_index(sample_interval=5000)
        print(f"[✓] Index built: {self.accessor.stats.total_events_indexed:,} events")
        
        # Load real signals
        print("\n[*] Loading real signals...")
        signals = self.extractor.load_signals(
            filter_date="2026-05-04",
            min_confidence=0.0,
            max_signals=max_signals
        )
        print(f"[✓] Loaded {len(signals)} real signals")
        
        # Backtest each signal
        print(f"\n[*] Backtesting {len(signals)} signals (no lookahead)...")
        for i, signal in enumerate(signals):
            if i % 50 == 0:
                print(f"  [{i+1}/{len(signals)}] Processing...")
            
            result = self._backtest_signal(signal)
            if result:
                self.results.append(result)
        
        print(f"[✓] Backtested {len(self.results)} signals")
        
        # Compute statistics
        self._compute_statistics()
        
        return self.stats
    
    def _backtest_signal(self, signal: RealSignal) -> Optional[Dict]:
        """
        Backtest a single REAL signal with NO lookahead.
        
        Returns: Result dict or None if error
        """
        try:
            signal_ts = datetime.fromisoformat(signal.signal_event_utc)
            
            # Get lookback data (for volatility context ONLY - before signal)
            lookback_start = signal_ts - timedelta(minutes=15)
            lookback_events = self.accessor.get_window(lookback_start, signal_ts)
            
            if not lookback_events:
                # Use default volatility
                lookback_vol = 0.25 * 2  # 2 ticks
            else:
                lookback_prices = [e['price'] for e in lookback_events]
                lookback_vol = self.planner.calculate_volatility(lookback_prices)
            
            # Plan entry/exit at signal time (NO FUTURE KNOWLEDGE)
            plan = self.planner.plan_entry(
                direction=signal.direction,
                entry_price=signal.entry_price,
                volatility=lookback_vol,
                absorption_low=signal.candle_low,
                absorption_high=signal.candle_high
            )
            
            # Get forward-looking price data (outcome window)
            # CRITICAL: This data comes AFTER the signal (no lookahead)
            outcome_start = signal_ts
            outcome_end = signal_ts + timedelta(minutes=30)
            
            outcome_events = self.accessor.get_window(outcome_start, outcome_end)
            
            # Validate replay-safe
            is_safe, msg = self.accessor.validate_replay_safe(outcome_start, outcome_end, outcome_events)
            if not is_safe:
                print(f"[!] Replay-unsafe signal: {msg}")
                return None
            
            # Find outcome (first stop or target hit)
            outcome = self._find_outcome(signal.direction, plan, outcome_events)
            
            # Calculate metrics
            result = {
                'signal_ts_utc': signal.signal_event_utc,
                'signal_ts_et': datetime.fromisoformat(signal.signal_event_utc).astimezone(timezone(timedelta(hours=-4))).isoformat(),
                'direction': signal.direction,
                'confidence': signal.confidence,
                'setup_type': signal.setup_type,
                'divergence_type': signal.divergence_type,
                'entry_price': signal.entry_price,
                'entry_filled': plan.entry_filled_price,
                'stop_price': plan.stop_price,
                'stop_filled': plan.stop_filled_price,
                'target1_price': plan.target_1_price,
                'target1_filled': plan.target_1_filled_price,
                'target2_price': plan.target_2_price,
                'target2_filled': plan.target_2_filled_price,
                'volatility_basis': lookback_vol,
                'outcome_type': outcome['type'],  # STOP_HIT, TARGET_HIT, or TIMEOUT
                'exit_price': outcome['exit_price'],
                'exit_ts': outcome.get('exit_ts', ''),
                'holding_time_minutes': outcome.get('holding_minutes', 0),
                'mae': outcome.get('mae', 0),
                'mfe': outcome.get('mfe', 0),
                'pnl_ticks': outcome['pnl_ticks'],
                'r_multiple': outcome['r_multiple'],
                'hit_target1': outcome['type'] in ['TARGET1_HIT', 'TARGET2_HIT'],
                'hit_target2': outcome['type'] == 'TARGET2_HIT',
                'hit_stop': outcome['type'] == 'STOP_HIT',
            }
            
            return result
        
        except Exception as e:
            print(f"[!] Error backtesting signal {signal.signal_event_utc}: {e}")
            return None
    
    def _find_outcome(self, direction: str, plan, events: List[Dict]) -> Dict:
        """
        Find outcome by replaying price action.
        
        CRITICAL: Stop priority - if both stop and target touched in same candle,
        stop executes first (realistic market behavior).
        
        Returns: Dict with outcome_type, exit_price, mae, mfe, r_multiple
        """
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        exit_price = events[-1]['price'] if events else plan.entry_filled_price
        exit_ts = events[-1]['ts'] if events else None
        holding_minutes = 0
        
        signal_ts = datetime.fromisoformat(events[0]['ts']) if events else datetime.now(timezone.utc)
        
        if direction == "SHORT":
            for i, event in enumerate(events):
                price = event['price']
                
                # Track extremes (for MAE/MFE)
                move_favorable = plan.entry_filled_price - price
                move_adverse = price - plan.entry_filled_price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                # Check stop FIRST (stop priority)
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    exit_ts = event['ts']
                    break
                
                # Check targets (only if stop not hit)
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    exit_ts = event['ts']
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    exit_ts = event['ts']
                    break
        
        else:  # LONG
            for i, event in enumerate(events):
                price = event['price']
                
                # Track extremes
                move_favorable = price - plan.entry_filled_price
                move_adverse = plan.entry_filled_price - price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                # Check stop FIRST
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    exit_ts = event['ts']
                    break
                
                # Check targets
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    exit_ts = event['ts']
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    exit_ts = event['ts']
                    break
        
        # Calculate holding time
        if exit_ts:
            exit_dt = datetime.fromisoformat(exit_ts)
            holding_seconds = (exit_dt - signal_ts).total_seconds()
            holding_minutes = holding_seconds / 60.0
        
        # Calculate P&L
        if direction == "SHORT":
            pnl = plan.entry_filled_price - exit_price
        else:
            pnl = exit_price - plan.entry_filled_price
        
        # Calculate R-multiple (using intended risk)
        intended_risk = abs(plan.entry_filled_price - plan.stop_filled_price)
        r_multiple = pnl / intended_risk if intended_risk > 0 else 0
        
        pnl_ticks = pnl / 0.25  # ES tick size
        
        return {
            'type': outcome_type,
            'exit_price': exit_price,
            'exit_ts': exit_ts,
            'holding_minutes': holding_minutes,
            'mae': mae,
            'mfe': mfe,
            'pnl': pnl,
            'pnl_ticks': pnl_ticks,
            'r_multiple': r_multiple,
        }
    
    def _compute_statistics(self):
        """Compute backtest statistics."""
        if not self.results:
            self.stats = {'error': 'No results'}
            return
        
        df_results = self.results
        
        # Basic metrics
        total_trades = len(df_results)
        wins = sum(1 for r in df_results if r['outcome_type'] in ['TARGET1_HIT', 'TARGET2_HIT'])
        losses = sum(1 for r in df_results if r['outcome_type'] == 'STOP_HIT')
        timeouts = sum(1 for r in df_results if r['outcome_type'] == 'TIMEOUT')
        
        win_rate = wins / total_trades if total_trades > 0 else 0
        
        # PnL metrics (using pnl_ticks and r_multiple)
        total_r = sum(r['r_multiple'] for r in df_results)
        avg_r = total_r / total_trades if total_trades > 0 else 0
        
        winning_trades = [r for r in df_results if r['outcome_type'] in ['TARGET1_HIT', 'TARGET2_HIT']]
        losing_trades = [r for r in df_results if r['outcome_type'] == 'STOP_HIT']
        
        avg_win = sum(r['r_multiple'] for r in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(r['r_multiple'] for r in losing_trades) / len(losing_trades) if losing_trades else 0
        
        pf = avg_win / abs(avg_loss) if avg_loss < 0 else (1.0 if avg_loss == 0 and avg_win > 0 else 0)
        
        # By direction
        longs = [r for r in df_results if r['direction'] == 'LONG']
        shorts = [r for r in df_results if r['direction'] == 'SHORT']
        
        long_wr = sum(1 for r in longs if r['outcome_type'] in ['TARGET1_HIT', 'TARGET2_HIT']) / len(longs) if longs else 0
        short_wr = sum(1 for r in shorts if r['outcome_type'] in ['TARGET1_HIT', 'TARGET2_HIT']) / len(shorts) if shorts else 0
        
        self.stats = {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'timeouts': timeouts,
            'win_rate': win_rate,
            'total_r': total_r,
            'avg_r_multiple': avg_r,
            'profit_factor': pf,
            'avg_winning_r': avg_win,
            'avg_losing_r': avg_loss,
            'long_win_rate': long_wr,
            'short_win_rate': short_wr,
            'avg_mae': sum(r['mae'] for r in df_results) / len(df_results),
            'avg_mfe': sum(r['mfe'] for r in df_results) / len(df_results),
            'avg_holding_minutes': sum(r['holding_time_minutes'] for r in df_results) / len(df_results),
        }
    
    def save_results(self):
        """Save detailed results and reports."""
        # Save CSV
        csv_path = self.output_dir / "trade_outcomes.csv"
        if self.results:
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.results[0].keys())
                writer.writeheader()
                writer.writerows(self.results)
        print(f"[✓] Saved: {csv_path}")
        
        # Save markdown report
        report_path = self.output_dir / "real_signal_backtest.md"
        with open(report_path, 'w') as f:
            f.write("# Real Signal Backtest Report\n\n")
            f.write(f"**Date:** {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"**Data:** May 4, 2026 ESM6 (Real signals + Real prices)\n\n")
            
            f.write("## Summary Statistics\n\n")
            for key, value in self.stats.items():
                if isinstance(value, float):
                    f.write(f"- **{key}:** {value:.4f}\n")
                else:
                    f.write(f"- **{key}:** {value}\n")
            
            f.write("\n## Trade Distribution\n\n")
            f.write(f"| Outcome | Count | % |\n")
            f.write(f"|---------|-------|---|\n")
            f.write(f"| Win (Target Hit) | {self.stats['wins']} | {self.stats['wins']/self.stats['total_trades']*100:.1f}% |\n")
            f.write(f"| Loss (Stop Hit) | {self.stats['losses']} | {self.stats['losses']/self.stats['total_trades']*100:.1f}% |\n")
            f.write(f"| Timeout | {self.stats['timeouts']} | {self.stats['timeouts']/self.stats['total_trades']*100:.1f}% |\n")
            
            f.write("\n## Verdict\n\n")
            if 0.45 <= self.stats['win_rate'] <= 0.65 and 1.5 <= self.stats['profit_factor'] <= 3.0:
                f.write("🟢 **LIVE_READY** - Realistic metrics, no red flags\n")
            elif self.stats['win_rate'] > 0.80 or self.stats['profit_factor'] > 10:
                f.write("🔴 **INVALID_BACKTEST** - Unrealistic metrics, possible lookahead bias\n")
            else:
                f.write("🟡 **PROMISING_BUT_UNVALIDATED** - Metrics in progress, needs more data\n")
        
        print(f"[✓] Saved: {report_path}")


if __name__ == "__main__":
    signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
    jsonl_path = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")
    
    engine = RealBacktestEngine(signals_csv, jsonl_path)
    stats = engine.run_backtest(max_signals=None)  # Run ALL 672 signals
    engine.save_results()
    
    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"{key:30s}: {value:.4f}")
        else:
            print(f"{key:30s}: {value}")
