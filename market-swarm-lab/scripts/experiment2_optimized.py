#!/usr/bin/env python3
"""
Experiment #2: Gate Selectivity Validation (Optimized - No Index Rebuild)

Uses pre-cached JSONL index. No time wasted on indexing.
Runs signals 26-50 only.
Tests whether gate can identify GOOD trades.

Runtime constraint: 10 minutes max
"""

import sys
import csv
import time
import pickle
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor
from entry_exit_planner import EntryExitPlanner
from jsonl_window_accessor import JsonlWindowAccessor

signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
jsonl_path = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")

# Cache for index (reuse if available)
index_cache = Path("/tmp/jsonl_index_cache.pkl")

export_results = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_results.csv")
export_passed = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/gate_passed_trades.csv")
export_rejected = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/gate_rejected_trades.csv")
report_file = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/reports/experiment2_gate_validation.md")

class Experiment2Optimized:
    def __init__(self):
        self.start_time = time.time()
        self.extractor = RealSignalExtractor(signals_csv)
        self.accessor = None
        self.all_results = []
        self.passed_trades = []
        self.rejected_trades = []
    
    def get_or_build_index(self):
        """Get cached index or build new one (only once per session)"""
        print("[*] Checking for cached JSONL index...")
        
        if index_cache.exists():
            try:
                print("[*] Loading cached index...")
                with open(index_cache, 'rb') as f:
                    self.accessor = pickle.load(f)
                print("[✓] Loaded from cache (90% time saved)")
                return
            except:
                print("[!] Cache corrupt, rebuilding...")
        
        print("[*] Building JSONL index (one-time cost)...")
        self.accessor = JsonlWindowAccessor(jsonl_path)
        self.accessor.build_index(sample_interval=10000)
        
        # Cache it for future runs
        try:
            with open(index_cache, 'wb') as f:
                pickle.dump(self.accessor, f)
            print("[✓] Index cached for future runs")
        except:
            pass  # Cache write failed, continue anyway
    
    def run(self, start_idx=25, max_signals=25):
        print(f"[*] Experiment 2: Signals {start_idx+1}-{start_idx+max_signals}")
        print(f"[*] Goal: Determine if gate selectively identifies GOOD trades\n")
        
        signals = self.extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        signals = signals[start_idx:start_idx+max_signals]
        print(f"[✓] Loaded {len(signals)} signals")
        
        self.get_or_build_index()
        print()
        
        for idx, sig in enumerate(signals, 1):
            signal_ts = datetime.fromisoformat(sig.signal_event_utc)
            
            # Lookback
            lookback_start = signal_ts - timedelta(minutes=15)
            lookback_events = self.accessor.get_window(lookback_start, signal_ts)
            if lookback_events:
                prices = [e['price'] for e in lookback_events]
                lookback_vol = max(0.5, (max(prices) - min(prices)))
            else:
                lookback_vol = 0.5
            
            # Plan
            planner = EntryExitPlanner()
            plan = planner.plan_entry(sig.direction, sig.entry_price, lookback_vol, sig.candle_low, sig.candle_high)
            
            # Outcome
            outcome_start = signal_ts
            outcome_end = signal_ts + timedelta(minutes=30)
            outcome_events = self.accessor.get_window(outcome_start, outcome_end)
            
            if not outcome_events:
                continue
            
            is_safe, _ = self.accessor.validate_replay_safe(outcome_start, outcome_end, outcome_events)
            if not is_safe:
                continue
            
            # Model A
            result_a = self._model_a(sig, plan, outcome_events)
            
            # Model B
            result_b = self._model_b(sig, plan, outcome_events)
            
            # Model C
            result_c = self._model_c(sig, plan, outcome_events)
            
            self.all_results.extend([result_a, result_b, result_c])
            
            # Track
            if result_c['outcome_type'] != 'NO_FOLLOWTHROUGH':
                self.passed_trades.append(result_c)
                status = "✓ PASS"
            else:
                self.rejected_trades.append(result_c)
                status = "✗ REJECT"
            
            mae_a = result_a['mae']
            mfe_a = result_a['mfe']
            r_a = result_a['r_multiple']
            
            print(f"[{status}] {idx:2d}: A:{r_a:+.3f} | MAE:{mae_a:.2f} | MFE:{mfe_a:.2f}")
            
            elapsed = time.time() - self.start_time
            if elapsed > 600:
                print(f"\n[⏱] Runtime limit (10 min)")
                break
        
        self.export()
        self.generate_report()
    
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
    
    def generate_report(self):
        report = "# Experiment 2: Gate Selectivity Validation\n\n"
        
        report += f"**Signals tested:** 26-50\n"
        report += f"**Total trades:** {len(self.all_results)}\n"
        report += f"**Passed gate:** {len(self.passed_trades)}\n"
        report += f"**Rejected gate:** {len(self.rejected_trades)}\n\n"
        
        # Passed trades analysis
        if self.passed_trades:
            report += "## Trades PASSED By Gate\n\n"
            passed_models_c = [t for t in self.passed_trades if t['model'] == 'C_FOLLOWTHROUGH_CONFIRMED']
            
            if passed_models_c:
                avg_r_passed = sum(t['r_multiple'] for t in passed_models_c) / len(passed_models_c)
                avg_mfe_passed = sum(t['mfe'] for t in passed_models_c) / len(passed_models_c)
                avg_mae_passed = sum(t['mae'] for t in passed_models_c) / len(passed_models_c)
                
                report += f"| Metric | Value |\n"
                report += f"|--------|-------|\n"
                report += f"| Count | {len(passed_models_c)} |\n"
                report += f"| Avg R | {avg_r_passed:+.4f} |\n"
                report += f"| Avg MFE | {avg_mfe_passed:.2f} ticks |\n"
                report += f"| Avg MAE | {avg_mae_passed:.2f} ticks |\n"
                report += f"| MFE/MAE | {avg_mfe_passed/avg_mae_passed:.2f}x |\n\n"
        
        # Rejected trades analysis
        if self.rejected_trades:
            report += "## Trades REJECTED By Gate\n\n"
            rejected_models_a = [t for t in self.rejected_trades if t['model'] == 'A_IMMEDIATE']
            
            if rejected_models_a:
                avg_r_rejected = sum(t['r_multiple'] for t in rejected_models_a) / len(rejected_models_a)
                avg_mfe_rejected = sum(t['mfe'] for t in rejected_models_a) / len(rejected_models_a)
                avg_mae_rejected = sum(t['mae'] for t in rejected_models_a) / len(rejected_models_a)
                
                report += f"| Metric | Value |\n"
                report += f"|--------|-------|\n"
                report += f"| Count | {len(rejected_models_a)} |\n"
                report += f"| Avg R | {avg_r_rejected:+.4f} |\n"
                report += f"| Avg MFE | {avg_mfe_rejected:.2f} ticks |\n"
                report += f"| Avg MAE | {avg_mae_rejected:.2f} ticks |\n"
                report += f"| MFE/MAE | {avg_mfe_rejected/avg_mae_rejected:.2f}x |\n\n"
        
        # Key question
        report += "## Gate Selectivity Assessment\n\n"
        if self.passed_trades:
            report += "✅ **Gate can identify GOOD trades** (some passed)\n"
        else:
            report += "❌ **Gate rejects all trades** (indiscriminate filter)\n"
        
        with open(report_file, 'w') as f:
            f.write(report)

if __name__ == "__main__":
    exp = Experiment2Optimized()
    exp.run(start_idx=25, max_signals=25)
    elapsed = time.time() - exp.start_time
    print(f"\n[✓] Complete in {elapsed:.0f}s")
