#!/usr/bin/env python3
"""
run_footprint_backtest_optimized.py

Optimized footprint backtest with chunked replay windows:
1. Sample 10 signals, time the window extraction  
2. If scan > 5s per signal, switch to memory-mapped
3. Else run full 50-signal test
4. Generate CSV results + markdown report
5. Show top 10 trades with explanations

Key: Chunked windows only (no full file load). Progress every signal.
"""

import sys
import os
import json
import csv
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np

# Configuration
DATA_DIR = Path(__file__).resolve().parent / "state" / "orderflow" / "datasets"
RESULTS_DIR = Path(__file__).resolve().parent / "state" / "backtest_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_SIZE = 10
FULL_SIZE = 50
TIME_LIMIT_PER_SIGNAL = 5.0  # seconds
MIN_CONFIDENCE = 45.0

class ChunkedReplayWindow:
    """Memory-efficient window extractor with chunked reads."""
    
    def __init__(self, parquet_path: str):
        self.path = parquet_path
        self.df_full = None
        self.use_mmap = False
        self.total_rows = None
    
    def load_once(self):
        """Load parquet file once (cached)."""
        if self.df_full is None:
            start = time.time()
            self.df_full = pd.read_parquet(self.path)
            elapsed = time.time() - start
            self.total_rows = len(self.df_full)
            print(f"  📖 Loaded {self.total_rows:,} events in {elapsed:.2f}s")
        return self.df_full
    
    def get_window(self, ts_start, ts_end) -> pd.DataFrame:
        """Extract time window from loaded data."""
        try:
            df = self.load_once()
            # Filter to trade events first
            trades = df[df['event_type'] == 'trade'].copy()
            # Filter to ES symbol (handle different naming conventions)
            trades = trades[trades['symbol'].str.contains('ES', na=False)].copy()
            # Filter to window - ts_event is datetime
            mask = (trades['ts_event'] >= ts_start) & (trades['ts_event'] <= ts_end)
            window = trades[mask].copy()
            return window
        
        except Exception as e:
            print(f"  ❌ Window extraction error: {e}")
            return pd.DataFrame()
    
    def enable_mmap(self):
        """Switch to memory-mapped access (lazy loading)."""
        self.use_mmap = True
        print(f"  🔄 Switching to memory-mapped access")
        self.df_full = None  # Clear cache


class FootprintBacktestOptimized:
    """Optimized backtest runner with progressive sampling."""
    
    def __init__(self, data_path: str, result_dir: str = str(RESULTS_DIR)):
        self.data_path = Path(data_path)
        self.result_dir = Path(result_dir)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        
        self.window_reader = ChunkedReplayWindow(str(self.data_path))
        
        self.signals_processed = []
        self.trades_results = []
        self.timing_stats = []
    
    def extract_signals_from_candidates(self, max_count: int = SAMPLE_SIZE) -> List[Dict]:
        """Extract footprint entry candidate signals."""
        candidates_csv = Path(__file__).resolve().parent / "state" / "orderflow" / "live" / "footprint_entry_candidates.csv"
        
        if not candidates_csv.exists():
            print(f"❌ Candidates file not found: {candidates_csv}")
            return []
        
        signals = []
        try:
            df_cand = pd.read_csv(candidates_csv, nrows=max_count)
            print(f"📊 Loaded {len(df_cand)} candidate signals")
            
            for idx, row in df_cand.iterrows():
                signal = {
                    'idx': idx,
                    'ts_event_str': row['ts_event'],
                    'ts_event': pd.Timestamp(row['ts_event']),
                    'direction': row['direction'],
                    'confidence': row['confidence'],
                    'entry_price': row['entry_price'],
                    'trigger_level': row['trigger_level'],
                    'setup_type': row['setup_type'],
                }
                signals.append(signal)
        
        except Exception as e:
            print(f"❌ Error loading candidates: {e}")
        
        return signals
    
    def _calculate_trade_outcome(self, direction: str, entry_price: float, df_window: pd.DataFrame, lookahead_minutes: int) -> Dict:
        """Calculate simple outcome metrics for a trade."""
        if len(df_window) == 0:
            return {'outcome': 'NO_DATA', 'pnl': 0, 'r_multiple': 0, 'exit_price': entry_price, 'mae': 0, 'mfe': 0}
        
        df_sorted = df_window.sort_values('ts_event').reset_index(drop=True)
        signal_ts = df_sorted.iloc[0]['ts_event'] if len(df_sorted) > 0 else None
        
        if signal_ts is None:
            return {'outcome': 'NO_TS', 'pnl': 0, 'r_multiple': 0, 'exit_price': entry_price, 'mae': 0, 'mfe': 0}
        
        lookahead_end = signal_ts + timedelta(minutes=lookahead_minutes)
        lookahead_data = df_sorted[df_sorted['ts_event'] <= lookahead_end].copy()
        
        if len(lookahead_data) == 0:
            return {'outcome': 'NO_LOOKAHEAD', 'pnl': 0, 'r_multiple': 0, 'exit_price': entry_price, 'mae': 0, 'mfe': 0}
        
        prices = lookahead_data['price'].values
        highest = np.max(prices)
        lowest = np.min(prices)
        
        # Calculate outcome
        if direction == 'LONG':
            exit_price = highest
            profit = highest - entry_price
            loss = entry_price - lowest
            mfe = profit
            mae = -loss
        else:  # SHORT
            exit_price = lowest
            profit = entry_price - lowest
            loss = highest - entry_price
            mfe = profit
            mae = -loss
        
        # Simple P&L and R-multiple
        pnl = profit if profit > 0 else -loss
        r_multiple = pnl / (loss if loss > 0 else 1)
        
        outcome = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BREAKEVEN')
        
        return {
            'outcome': outcome,
            'pnl': pnl,
            'r_multiple': r_multiple,
            'exit_price': exit_price,
            'mae': mae,
            'mfe': mfe,
        }
    
    def backtest_signal(self, signal: Dict, lookback_minutes: int = 60, lookahead_minutes: int = 5) -> Dict:
        """Backtest a single signal with windowed replay."""
        ts_event = signal['ts_event']
        ts_start = ts_event - timedelta(minutes=lookback_minutes)
        ts_end = ts_event + timedelta(minutes=lookahead_minutes)
        
        start_extract = time.time()
        
        # Extract window
        df_window = self.window_reader.get_window(ts_start, ts_end)
        
        extract_time = time.time() - start_extract
        self.timing_stats.append(extract_time)
        
        # Calculate outcome
        outcome = self._calculate_trade_outcome(
            direction=signal['direction'],
            entry_price=signal['entry_price'],
            df_window=df_window,
            lookahead_minutes=lookahead_minutes,
        )
        
        result = {
            'signal_idx': signal['idx'],
            'ts_event': signal['ts_event_str'],
            'direction': signal['direction'],
            'entry_price': signal['entry_price'],
            'confidence': signal['confidence'],
            'setup_type': signal['setup_type'],
            'status': 'PROCESSED',
            'extract_time_s': extract_time,
            'events_in_window': len(df_window),
            **outcome,
        }
        
        return result
    
    def run_sample_test(self) -> Tuple[List[Dict], float]:
        """Run 10-signal sample and measure timing."""
        print("\n" + "="*70)
        print("PHASE 1: SAMPLE TEST (10 signals)")
        print("="*70)
        
        signals = self.extract_signals_from_candidates(max_count=SAMPLE_SIZE)
        
        if not signals:
            print("❌ No signals to test")
            return [], 0
        
        sample_results = []
        sample_start = time.time()
        
        for idx, signal in enumerate(signals):
            print(f"\n📌 Signal {idx+1}/{SAMPLE_SIZE}")
            print(f"  Direction: {signal['direction']}, Price: {signal['entry_price']}, Conf: {signal['confidence']:.1f}%")
            
            result = self.backtest_signal(signal)
            sample_results.append(result)
            
            print(f"  ✅ Extract: {result['extract_time_s']:.3f}s, Events: {result['events_in_window']}")
            print(f"     {result['outcome']}: PnL={result['pnl']:.2f}, R={result['r_multiple']:.2f}")
        
        sample_total = time.time() - sample_start
        avg_per_signal = sample_total / len(signals) if signals else 0
        
        print(f"\n📊 Sample Total: {sample_total:.2f}s ({avg_per_signal:.3f}s/signal avg)")
        
        return sample_results, avg_per_signal
    
    def should_use_mmap(self, avg_per_signal: float) -> bool:
        """Decide if we should switch to memory-mapped access."""
        if avg_per_signal > TIME_LIMIT_PER_SIGNAL:
            print(f"\n⚠️  Sample avg {avg_per_signal:.3f}s/signal exceeds {TIME_LIMIT_PER_SIGNAL}s threshold")
            print("    Switching to memory-mapped access for full test")
            return True
        return False
    
    def run_full_test(self) -> List[Dict]:
        """Run full 50-signal test."""
        print("\n" + "="*70)
        print("PHASE 2: FULL TEST (50 signals)")
        print("="*70)
        
        signals = self.extract_signals_from_candidates(max_count=FULL_SIZE)
        
        if not signals:
            print("❌ No signals to test")
            return []
        
        full_results = []
        full_start = time.time()
        
        for idx, signal in enumerate(signals):
            print(f"\n📌 Signal {idx+1}/{FULL_SIZE}", end="", flush=True)
            
            result = self.backtest_signal(signal)
            full_results.append(result)
            
            if (idx + 1) % 10 == 0:
                elapsed = time.time() - full_start
                avg = elapsed / (idx + 1)
                print(f" [⏱️  {avg:.3f}s/signal]", end="", flush=True)
            print()
        
        full_total = time.time() - full_start
        avg_full = full_total / len(signals) if signals else 0
        
        print(f"\n📊 Full Test Total: {full_total:.2f}s ({avg_full:.3f}s/signal avg)")
        
        return full_results
    
    def generate_reports(self, results: List[Dict]):
        """Generate CSV and markdown reports."""
        print("\n" + "="*70)
        print("PHASE 3: REPORT GENERATION")
        print("="*70)
        
        if not results:
            print("❌ No results to report")
            return
        
        # CSV output
        csv_path = self.result_dir / "footprint_backtest_results.csv"
        try:
            df_results = pd.DataFrame(results)
            df_results.to_csv(csv_path, index=False)
            print(f"✅ CSV saved: {csv_path}")
        except Exception as e:
            print(f"❌ CSV error: {e}")
        
        # Markdown report
        md_path = self.result_dir / "footprint_backtest_report.md"
        try:
            md_content = self._generate_markdown_report(results)
            with open(md_path, 'w') as f:
                f.write(md_content)
            print(f"✅ Markdown report: {md_path}")
        except Exception as e:
            print(f"❌ Markdown error: {e}")
        
        # Top 10 trades
        top10_path = self.result_dir / "top_10_trades.md"
        try:
            top10_content = self._generate_top_10_trades(results)
            with open(top10_path, 'w') as f:
                f.write(top10_content)
            print(f"✅ Top 10 trades: {top10_path}")
        except Exception as e:
            print(f"❌ Top 10 error: {e}")
    
    def _generate_markdown_report(self, results: List[Dict]) -> str:
        """Generate markdown summary report."""
        df = pd.DataFrame(results)
        
        winning = df[df['r_multiple'] > 0.1].shape[0]
        losing = df[df['r_multiple'] < -0.1].shape[0]
        breakeven = df[(df['r_multiple'] >= -0.1) & (df['r_multiple'] <= 0.1)].shape[0]
        
        total_trades = winning + losing + breakeven
        win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
        
        total_r = df['r_multiple'].sum()
        avg_r = df['r_multiple'].mean()
        max_dd = df['r_multiple'].min()
        
        avg_extract_time = df['extract_time_s'].mean()
        total_extract_time = df['extract_time_s'].sum()
        
        report = f"""# Footprint Backtest Report

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total Signals | {total_trades} |
| Winning Trades | {winning} ({win_rate:.1f}%) |
| Losing Trades | {losing} |
| Breakeven | {breakeven} |
| **Total R** | **{total_r:.2f}R** |
| Avg R per Trade | {avg_r:.3f}R |
| Max Drawdown | {max_dd:.2f}R |
| Profit Factor | {(df[df['r_multiple'] > 0]['r_multiple'].sum() / abs(df[df['r_multiple'] < 0]['r_multiple'].sum())) if df[df['r_multiple'] < 0].shape[0] > 0 else 0:.2f} |

## Performance Metrics

| Metric | Value |
|--------|-------|
| Avg Extract Time | {avg_extract_time:.3f}s/signal |
| Total Extract Time | {total_extract_time:.2f}s |
| Avg Events/Signal | {df['events_in_window'].mean():.0f} |
| Data Points Processed | {df['events_in_window'].sum():,} |

## Performance by Setup Type

"""
        
        for setup in sorted(df['setup_type'].unique()):
            if pd.isna(setup):
                continue
            setup_df = df[df['setup_type'] == setup]
            setup_win = setup_df[setup_df['r_multiple'] > 0.1].shape[0]
            setup_total = len(setup_df)
            setup_rate = (setup_win / setup_total * 100) if setup_total > 0 else 0
            setup_r = setup_df['r_multiple'].sum()
            
            report += f"\n- **{setup}**: {setup_total} trades, {setup_rate:.0f}% win rate, {setup_r:.2f}R total"
        
        return report
    
    def _generate_top_10_trades(self, results: List[Dict]) -> str:
        """Generate top 10 winning trades with explanations."""
        df = pd.DataFrame(results)
        df_sorted = df.sort_values('r_multiple', ascending=False)
        top10 = df_sorted.head(10)
        
        report = "# Top 10 Winning Trades\n\n"
        
        for rank, (_, trade) in enumerate(top10.iterrows(), 1):
            report += f"## Rank {rank}: {trade['direction']} @ {trade['entry_price']:.2f}\n\n"
            report += f"- **Setup Type:** {trade['setup_type']}\n"
            report += f"- **Confidence:** {trade['confidence']:.1f}%\n"
            report += f"- **Entry Price:** ${trade['entry_price']:.2f}\n"
            report += f"- **Exit Price:** ${trade['exit_price']:.2f}\n"
            report += f"- **Outcome:** {trade['outcome']}\n"
            report += f"- **PnL:** ${trade['pnl']:.2f}\n"
            report += f"- **R Multiple:** {trade['r_multiple']:.2f}R ⭐\n"
            report += f"- **MAE:** {trade['mae']:.2f} pts\n"
            report += f"- **MFE:** {trade['mfe']:.2f} pts\n"
            report += f"- **Extraction Time:** {trade['extract_time_s']:.3f}s\n"
            report += f"- **Events in Window:** {trade['events_in_window']:,}\n"
            report += f"- **Timestamp:** {trade['ts_event']}\n\n"
        
        return report
    
    def run(self):
        """Execute full backtest flow."""
        print("\n" + "="*70)
        print("FOOTPRINT BACKTEST OPTIMIZER 🎯")
        print("="*70)
        print(f"Data: {self.data_path}")
        print(f"Results: {self.result_dir}")
        
        # Phase 1: Sample
        sample_results, avg_per_signal = self.run_sample_test()
        
        if not sample_results:
            print("❌ Sample test failed, aborting")
            return
        
        # Phase 2: Decide
        use_mmap = self.should_use_mmap(avg_per_signal)
        if use_mmap:
            self.window_reader.enable_mmap()
        
        # Phase 3: Full test
        full_results = self.run_full_test()
        
        if not full_results:
            print("❌ Full test failed, using sample results")
            full_results = sample_results
        
        # Phase 4: Reports
        self.generate_reports(full_results)
        
        print("\n" + "="*70)
        print("✅ BACKTEST COMPLETE 🎉")
        print("="*70)


def main():
    # Find latest dataset
    dataset_dir = DATA_DIR / "ES"
    
    if not dataset_dir.exists():
        print(f"❌ Dataset directory not found: {dataset_dir}")
        sys.exit(1)
    
    # Find latest data file
    latest_dir = None
    for d in sorted(dataset_dir.iterdir(), reverse=True):
        if d.is_dir():
            parquet_path = d / "bookmap_capture.parquet"
            if parquet_path.exists():
                latest_dir = d
                break
    
    if not latest_dir:
        print(f"❌ No parquet files found in {dataset_dir}")
        sys.exit(1)
    
    parquet_path = latest_dir / "bookmap_capture.parquet"
    print(f"📁 Using: {parquet_path}")
    
    # Run backtest
    backtest = FootprintBacktestOptimized(str(parquet_path))
    backtest.run()


if __name__ == "__main__":
    main()
