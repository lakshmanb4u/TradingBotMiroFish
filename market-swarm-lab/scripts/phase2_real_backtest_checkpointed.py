#!/usr/bin/env python3
"""
Phase 2 Real Replay Backtest with Checkpointing
- Saves progress every 10 signals
- Resumable if killed
- Bounded memory usage
- Partial output generation
"""

import sys
import csv
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor
from entry_exit_planner import EntryExitPlanner
from jsonl_window_accessor import JsonlWindowAccessor

# Paths
signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
jsonl_path = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")
progress_file = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/backtest/progress.json")
partial_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/reports/trade_outcomes_partial.csv")
progress_file.parent.mkdir(parents=True, exist_ok=True)

class CheckpointedBacktest:
    def __init__(self):
        self.results = []
        self.progress = self._load_progress()
        self.start_time = time.time()
        self.extractor = RealSignalExtractor(signals_csv)
        self.accessor = JsonlWindowAccessor(jsonl_path)
        
    def _load_progress(self) -> Dict:
        if progress_file.exists():
            with open(progress_file) as f:
                return json.load(f)
        return {
            'processed_signals': 0,
            'completed_signals': 0,
            'rejected_signals': 0,
            'failed_signals': 0,
            'current_signal_index': 0,
            'runtime_seconds': 0,
            'last_update_time': datetime.now(timezone.utc).isoformat(),
        }
    
    def _save_progress(self):
        self.progress['runtime_seconds'] = int(time.time() - self.start_time)
        self.progress['last_update_time'] = datetime.now(timezone.utc).isoformat()
        with open(progress_file, 'w') as f:
            json.dump(self.progress, f, indent=2)
    
    def _save_partial_csv(self):
        if not self.results:
            return
        
        with open(partial_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=self.results[0].keys())
            w.writeheader()
            w.writerows(self.results)
    
    def run(self):
        print(f"[*] Loading signals (resuming from index {self.progress['current_signal_index']})...")
        signals = self.extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        print(f"[✓] Loaded {len(signals)} signals")
        
        print(f"[*] Building JSONL index...")
        self.accessor.build_index(sample_interval=5000)
        print(f"[✓] Index built")
        
        # Process signals starting from last checkpoint
        for idx in range(self.progress['current_signal_index'], len(signals)):
            sig = signals[idx]
            
            try:
                result = self._backtest_signal(sig)
                if result:
                    self.results.append(result)
                    self.progress['completed_signals'] += 1
                else:
                    self.progress['rejected_signals'] += 1
                    
            except Exception as e:
                print(f"[!] Error on signal {idx}: {e}")
                self.progress['failed_signals'] += 1
            
            self.progress['processed_signals'] += 1
            self.progress['current_signal_index'] = idx + 1
            
            # Checkpoint every 10 signals
            if (idx + 1) % 10 == 0:
                self._save_progress()
                self._save_partial_csv()
                print(f"[✓] Progress: {idx+1}/{len(signals)} signals ({self.progress['completed_signals']} valid)")
        
        # Final save
        self._save_progress()
        self._save_partial_csv()
        print(f"[✓] Backtest complete: {self.progress['completed_signals']} valid signals")
    
    def _backtest_signal(self, sig) -> Optional[Dict]:
        """Backtest one signal. Returns result dict or None if rejected."""
        signal_ts = datetime.fromisoformat(sig.signal_event_utc)
        
        # Lookback for volatility
        lookback_start = signal_ts - timedelta(minutes=15)
        lookback_events = self.accessor.get_window(lookback_start, signal_ts)
        
        if lookback_events:
            prices = [e['price'] for e in lookback_events]
            lookback_vol = max(0.5, (max(prices) - min(prices)))
        else:
            lookback_vol = 0.5
        
        # Plan entry/stop/targets
        planner = EntryExitPlanner()
        plan = planner.plan_entry(sig.direction, sig.entry_price, lookback_vol, sig.candle_low, sig.candle_high)
        
        # Get outcome window
        outcome_start = signal_ts
        outcome_end = signal_ts + timedelta(minutes=30)
        outcome_events = self.accessor.get_window(outcome_start, outcome_end)
        
        # Validate replay-safe
        is_safe, msg = self.accessor.validate_replay_safe(outcome_start, outcome_end, outcome_events)
        if not is_safe:
            return None
        
        # Find outcome
        outcome = self._find_outcome(sig.direction, plan, outcome_events)
        
        # Build result
        result = {
            'signal_ts_utc': sig.signal_event_utc,
            'signal_ts_et': datetime.fromisoformat(sig.signal_event_utc).astimezone(timezone(timedelta(hours=-4))).isoformat(),
            'direction': sig.direction,
            'confidence': sig.confidence,
            'entry_price': sig.entry_price,
            'entry_filled': plan.entry_filled_price,
            'stop_price': plan.stop_price,
            'stop_filled': plan.stop_filled_price,
            'target1_filled': plan.target_1_filled_price,
            'target2_filled': plan.target_2_filled_price,
            'outcome_type': outcome['type'],
            'exit_price': outcome['exit_price'],
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'r_multiple': outcome['r_multiple'],
            'holding_minutes': outcome['holding_minutes'],
            'outcome_events': len(outcome_events),
        }
        
        return result
    
    def _find_outcome(self, direction: str, plan, events: List[Dict]) -> Dict:
        """Find exit by replaying prices. Stop priority."""
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        exit_price = events[-1]['price'] if events else plan.entry_filled_price
        holding_minutes = 0
        
        if direction == "SHORT":
            for event in events:
                price = event['price']
                move_favorable = plan.entry_filled_price - price
                move_adverse = price - plan.entry_filled_price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                # Stop priority
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                
                # Targets
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
        
        else:  # LONG
            for event in events:
                price = event['price']
                move_favorable = price - plan.entry_filled_price
                move_adverse = plan.entry_filled_price - price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                # Stop priority
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                
                # Targets
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
        
        # Calculate R-multiple
        risk = abs(plan.stop_filled_price - plan.entry_filled_price)
        if outcome_type == "STOP_HIT":
            profit = 0  # Loss equals full risk
            r_multiple = -1.0
        elif outcome_type == "TIMEOUT":
            profit = exit_price - plan.entry_filled_price if direction == "LONG" else plan.entry_filled_price - exit_price
            r_multiple = profit / risk if risk > 0 else 0
        else:  # Target hit
            if direction == "LONG":
                profit = exit_price - plan.entry_filled_price
            else:
                profit = plan.entry_filled_price - exit_price
            r_multiple = profit / risk if risk > 0 else 0
        
        return {
            'type': outcome_type,
            'exit_price': exit_price,
            'mae': mae,
            'mfe': mfe,
            'r_multiple': r_multiple,
            'holding_minutes': 30,  # Default 30min window
        }

if __name__ == "__main__":
    bt = CheckpointedBacktest()
    bt.run()
    print(f"\n{'='*70}")
    print(f"BACKTEST COMPLETE")
    print(f"Results saved to: {partial_csv}")
    print(f"Progress: {bt.progress}")
    print(f"{'='*70}")
