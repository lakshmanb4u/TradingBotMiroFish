#!/usr/bin/env python3
"""
Experiment #2: Gate Selectivity - Minimal Parquet Implementation

Fast iteration: Load parquet once, iterate signals in memory
No DuckDB, no query interface, no optimization tricks
Just: Load → Iterate → Run models → Export
"""

import sys
import csv
import time
from pathlib import Path

try:
    import pyarrow.parquet as pq
except ImportError:
    print("[!] PyArrow not installed")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor
from entry_exit_planner import EntryExitPlanner

signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
events_parquet = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/cache/signals_26_50_events.parquet")
metadata_parquet = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/cache/signals_26_50_metadata.parquet")

export_results = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_results.csv")
export_passed = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_gate_passed.csv")
export_rejected = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_gate_rejected.csv")

class Experiment2Minimal:
    def __init__(self):
        self.start_time = time.time()
        self.all_results = []
        self.passed_trades = []
        self.rejected_trades = []
    
    def run(self):
        print("[*] Experiment 2: Gate Selectivity (Minimal)")
        print("[*] Signals 26-50\n")
        
        # Load everything into memory ONCE
        print("[*] Loading parquet...")
        start = time.time()
        events_table = pq.read_table(events_parquet)
        metadata_table = pq.read_table(metadata_parquet)
        elapsed = time.time() - start
        print(f"[✓] Loaded in {elapsed:.2f}s ({len(events_table):,} events)\n")
        
        # Convert to Python dicts for fast access
        events_dict = {}
        for row in events_table.to_pylist():
            sig_id = row['signal_id']
            if sig_id not in events_dict:
                events_dict[sig_id] = []
            events_dict[sig_id].append({'price': row['price']})
        
        metadata_dict = {row['signal_id']: row for row in metadata_table.to_pylist()}
        
        # Load signals for entry planning
        extractor = RealSignalExtractor(signals_csv)
        all_signals = extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        
        # Iterate signals 26-50
        print("[*] Running models...\n")
        for sig_idx in range(25, 50):
            sig = all_signals[sig_idx]
            sig_id = sig_idx + 1
            
            if sig_id not in events_dict:
                continue
            
            events = events_dict[sig_id]
            meta = metadata_dict.get(sig_id)
            
            if not meta:
                continue
            
            # Get volatility from candle
            lookback_vol = max(0.5, (meta['candle_high'] - meta['candle_low']))
            
            # Plan entry
            planner = EntryExitPlanner()
            plan = planner.plan_entry(sig.direction, sig.entry_price, lookback_vol, meta['candle_low'], meta['candle_high'])
            
            # Run models
            result_a = self._model_a(sig, plan, events)
            result_b = self._model_b(sig, plan, events)
            result_c = self._model_c(sig, plan, events)
            
            self.all_results.extend([result_a, result_b, result_c])
            
            if result_c['outcome_type'] != 'NO_FOLLOWTHROUGH':
                self.passed_trades.append(result_c)
                status = "✓ PASS"
            else:
                self.rejected_trades.append(result_c)
                status = "✗ REJECT"
            
            mae_a = result_a['mae']
            mfe_a = result_a['mfe']
            r_a = result_a['r_multiple']
            
            print(f"[{status}] {sig_idx-24:2d}: A:{r_a:+.3f} | MAE:{mae_a:.2f} | MFE:{mfe_a:.2f}")
        
        self.export()
    
    def _model_a(self, sig, plan, events):
        outcome = self._find_outcome(sig.direction, plan, events)
        return {
            'signal_id': sig.signal_event_utc,
            'model': 'A_IMMEDIATE',
            'direction': sig.direction,
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'r_multiple': outcome['r_multiple'],
        }
    
    def _model_b(self, sig, plan, events):
        outcome = self._find_outcome(sig.direction, plan, events)
        return {
            'signal_id': sig.signal_event_utc,
            'model': 'B_RECLAIM_START',
            'direction': sig.direction,
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'r_multiple': outcome['r_multiple'],
        }
    
    def _model_c(self, sig, plan, events):
        entry_price = plan.entry_filled_price
        followthrough_idx = None
        
        for i, event in enumerate(events):
            price = event['price']
            if sig.direction == "SHORT":
                if i > 0:
                    min_so_far = min(e['price'] for e in events[:i+1])
                    if price < min_so_far - 0.5:
                        followthrough_idx = i
                        break
            else:
                if i > 0:
                    max_so_far = max(e['price'] for e in events[:i+1])
                    if price > max_so_far + 0.5:
                        followthrough_idx = i
                        break
        
        if followthrough_idx and followthrough_idx > 0:
            outcome = self._find_outcome_subset(sig.direction, plan, entry_price, events[followthrough_idx:])
        else:
            outcome = {'type': 'NO_FOLLOWTHROUGH', 'mae': 999, 'mfe': 0, 'r_multiple': 0}
        
        return {
            'signal_id': sig.signal_event_utc,
            'model': 'C_FOLLOWTHROUGH_CONFIRMED',
            'direction': sig.direction,
            'mae': outcome.get('mae', 999),
            'mfe': outcome.get('mfe', 0),
            'outcome_type': outcome.get('type', 'NO_FOLLOWTHROUGH'),
            'r_multiple': outcome.get('r_multiple', 0),
        }
    
    def _find_outcome(self, direction, plan, events):
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        
        for event in events:
            price = event['price']
            
            if direction == "SHORT":
                move_fav = plan.entry_filled_price - price
                move_adv = price - plan.entry_filled_price
                
                if move_fav > mfe:
                    mfe = move_fav
                if move_adv > mae:
                    mae = move_adv
                
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    break
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    break
            else:
                move_fav = price - plan.entry_filled_price
                move_adv = plan.entry_filled_price - price
                
                if move_fav > mfe:
                    mfe = move_fav
                if move_adv > mae:
                    mae = move_adv
                
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    break
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    break
        
        risk = abs(plan.stop_filled_price - plan.entry_filled_price)
        if outcome_type == "STOP_HIT":
            r_multiple = -1.0
        elif outcome_type == "TIMEOUT":
            if direction == "SHORT":
                profit = plan.entry_filled_price - events[-1]['price']
            else:
                profit = events[-1]['price'] - plan.entry_filled_price
            r_multiple = profit / risk if risk > 0 else 0
        else:
            r_multiple = 1.0 if outcome_type == "TARGET1_HIT" else 2.0
        
        return {'type': outcome_type, 'mae': mae, 'mfe': mfe, 'r_multiple': r_multiple}
    
    def _find_outcome_subset(self, direction, plan, entry_price, events):
        if not events:
            return {'type': 'NO_EVENTS', 'mae': 0, 'mfe': 0, 'r_multiple': 0}
        
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        
        for event in events:
            price = event['price']
            
            if direction == "SHORT":
                move_fav = entry_price - price
                move_adv = price - entry_price
                
                if move_fav > mfe:
                    mfe = move_fav
                if move_adv > mae:
                    mae = move_adv
                
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    break
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    break
            else:
                move_fav = price - entry_price
                move_adv = entry_price - price
                
                if move_fav > mfe:
                    mfe = move_fav
                if move_adv > mae:
                    mae = move_adv
                
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    break
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    break
        
        risk = abs(plan.stop_filled_price - entry_price)
        if outcome_type == "STOP_HIT":
            r_multiple = -1.0
        elif outcome_type == "TIMEOUT":
            if direction == "SHORT":
                profit = entry_price - events[-1]['price']
            else:
                profit = events[-1]['price'] - entry_price
            r_multiple = profit / risk if risk > 0 else 0
        else:
            r_multiple = 1.0 if outcome_type == "TARGET1_HIT" else 2.0
        
        return {'type': outcome_type, 'mae': mae, 'mfe': mfe, 'r_multiple': r_multiple}
    
    def export(self):
        with open(export_results, 'w', newline='') as f:
            if self.all_results:
                w = csv.DictWriter(f, fieldnames=self.all_results[0].keys())
                w.writeheader()
                w.writerows(self.all_results)
        
        with open(export_passed, 'w', newline='') as f:
            if self.passed_trades:
                w = csv.DictWriter(f, fieldnames=self.passed_trades[0].keys())
                w.writeheader()
                w.writerows(self.passed_trades)
        
        with open(export_rejected, 'w', newline='') as f:
            if self.rejected_trades:
                w = csv.DictWriter(f, fieldnames=self.rejected_trades[0].keys())
                w.writeheader()
                w.writerows(self.rejected_trades)
        
        print(f"\n[✓] {len(self.all_results)} total results")
        print(f"[✓] {len(self.passed_trades)} PASSED gate")
        print(f"[✓] {len(self.rejected_trades)} REJECTED gate")

if __name__ == "__main__":
    exp = Experiment2Minimal()
    exp.run()
    elapsed = time.time() - exp.start_time
    print(f"\n[✓] Complete in {elapsed:.1f}s")
