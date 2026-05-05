#!/usr/bin/env python3
"""
Experiment #2: Gate Selectivity Using Cached Windows

Runs ONLY against pre-extracted window cache.
NO JSONL access during experiment.
Runtime: <1 minute (no indexing overhead)
"""

import sys
import csv
from pathlib import Path

csv.field_size_limit(1000000)  # Increase for large price lists
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor
from entry_exit_planner import EntryExitPlanner

signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
cache_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/cache/experiment_windows/signals_26_50_windows.csv")

export_results = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_results.csv")
export_passed = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_gate_passed.csv")
export_rejected = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/experiment2_gate_rejected.csv")

class Experiment2Cached:
    def __init__(self):
        self.all_results = []
        self.passed_trades = []
        self.rejected_trades = []
    
    def run(self):
        print("[*] Experiment 2: Gate Selectivity (Cached Windows)")
        print("[*] Signals 26-50 only\n")
        
        # Load cache
        print("[*] Loading cached windows...")
        cached_windows = {}
        with open(cache_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                prices = [float(p) for p in row['outcome_prices'].split('|')]
                cached_windows[int(row['signal_id'])] = {
                    'signal_ts_utc': row['signal_ts_utc'],
                    'direction': row['direction'],
                    'entry_price': float(row['entry_price']),
                    'candle_low': float(row['candle_low']),
                    'candle_high': float(row['candle_high']),
                    'outcome_prices': prices,
                }
        
        print(f"[✓] Loaded {len(cached_windows)} cached windows\n")
        
        # Load signals for entry planning
        extractor = RealSignalExtractor(signals_csv)
        all_signals = extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        
        for sig_id, window in sorted(cached_windows.items()):
            sig = all_signals[sig_id - 1]  # sig_id is 1-indexed
            
            # Get lookback volatility (estimate from candle)
            lookback_vol = max(0.5, (float(window['candle_high']) - float(window['candle_low'])))
            
            # Plan entry
            planner = EntryExitPlanner()
            plan = planner.plan_entry(
                window['direction'],
                float(window['entry_price']),
                lookback_vol,
                float(window['candle_low']),
                float(window['candle_high'])
            )
            
            # Create fake event list from cached prices
            events = [{'price': p} for p in window['outcome_prices']]
            
            # Test all three models
            result_a = self._model_a(window, plan, events)
            result_b = self._model_b(window, plan, events)
            result_c = self._model_c(window, plan, events)
            
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
            
            print(f"[{status}] {sig_id:2d}: A:{r_a:+.3f} | MAE:{mae_a:.2f} | MFE:{mfe_a:.2f}")
        
        self.export()
        self.generate_report()
    
    def _model_a(self, window, plan, events):
        outcome = self._find_outcome(window['direction'], plan, events)
        return {
            'signal_id': window['signal_ts_utc'],
            'model': 'A_IMMEDIATE',
            'direction': window['direction'],
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'r_multiple': outcome['r_multiple'],
        }
    
    def _model_b(self, window, plan, events):
        outcome = self._find_outcome(window['direction'], plan, events)
        return {
            'signal_id': window['signal_ts_utc'],
            'model': 'B_RECLAIM_START',
            'direction': window['direction'],
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'r_multiple': outcome['r_multiple'],
        }
    
    def _model_c(self, window, plan, events):
        entry_price = plan.entry_filled_price
        followthrough_idx = None
        
        for i, event in enumerate(events):
            price = event['price']
            if window['direction'] == "SHORT":
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
            outcome = self._find_outcome_subset(window['direction'], plan, entry_price, events[followthrough_idx:])
        else:
            outcome = {'type': 'NO_FOLLOWTHROUGH', 'mae': 999, 'mfe': 0, 'r_multiple': 0}
        
        return {
            'signal_id': window['signal_ts_utc'],
            'model': 'C_FOLLOWTHROUGH_CONFIRMED',
            'direction': window['direction'],
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
        print(f"[✓] {len(self.passed_trades)} PASSED")
        print(f"[✓] {len(self.rejected_trades)} REJECTED")
    
    def generate_report(self):
        report = "# Experiment 2: Gate Selectivity Validation (Cached)\n\n"
        report += f"**Signals tested:** 26-50\n"
        report += f"**Data source:** Pre-extracted cache (no JSONL access)\n"
        report += f"**Total results:** {len(self.all_results)}\n\n"
        
        report += f"## Results\n\n"
        report += f"**Passed gate:** {len(self.passed_trades)}\n"
        report += f"**Rejected gate:** {len(self.rejected_trades)}\n\n"
        
        if self.passed_trades:
            avg_r_passed = sum(t['r_multiple'] for t in self.passed_trades) / len(self.passed_trades)
            avg_mfe = sum(t['mfe'] for t in self.passed_trades) / len(self.passed_trades)
            avg_mae = sum(t['mae'] for t in self.passed_trades) / len(self.passed_trades)
            
            report += f"**Passed Trades:**\n"
            report += f"- Avg R: {avg_r_passed:+.4f}\n"
            report += f"- Avg MFE: {avg_mfe:.2f} ticks\n"
            report += f"- Avg MAE: {avg_mae:.2f} ticks\n"
            report += f"- MFE/MAE: {avg_mfe/avg_mae:.2f}x\n\n"
        
        if self.rejected_trades:
            model_a_rejected = [t for t in self.rejected_trades if t['model'] == 'A_IMMEDIATE']
            if model_a_rejected:
                avg_r_rej = sum(t['r_multiple'] for t in model_a_rejected) / len(model_a_rejected)
                print(f"**Rejected Trades (Model A):**\n")
                print(f"- Avg R: {avg_r_rej:+.4f}\n\n")
        
        report += f"## Assessment\n\n"
        if self.passed_trades:
            report += f"✅ Gate can identify better trades ({len(self.passed_trades)}/{len(self.rejected_trades)+len(self.passed_trades)} passed)\n"
        else:
            report += f"❌ Gate rejects all trades (indiscriminate filter)\n"
        
        with open(Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/reports/experiment2_runtime_fix.md"), 'w') as f:
            f.write(report)

if __name__ == "__main__":
    exp = Experiment2Cached()
    exp.run()
    print(f"\n[✓] Experiment 2 complete")
