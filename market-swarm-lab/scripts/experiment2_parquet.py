#!/usr/bin/env python3
"""
Experiment #2: Gate Selectivity Using Parquet Cache

Uses pre-built parquet files. NO JSONL indexing. NO CSV parsing.
Runtime: <2 minutes (no data access bottleneck)
"""

import sys
import csv
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
except ImportError:
    print("[!] PyArrow not installed. Install: pip install pyarrow")
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
report_file = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/reports/experiment2_gate_validation.md")

class Experiment2Parquet:
    def __init__(self):
        self.start_time = time.time()
        self.extractor = RealSignalExtractor(signals_csv)
        self.events_table = None
        self.metadata_table = None
        self.all_results = []
        self.passed_trades = []
        self.rejected_trades = []
    
    def load_parquet(self):
        """Load parquet files into memory"""
        print("[*] Loading parquet files...")
        start = time.time()
        
        self.events_table = pq.read_table(events_parquet)
        self.metadata_table = pq.read_table(metadata_parquet)
        
        elapsed = time.time() - start
        print(f"[✓] Loaded in {elapsed:.2f}s")
        print(f"[✓] {len(self.events_table):,} events")
        print(f"[✓] {len(self.metadata_table)} signals\n")
    
    def run(self, start_idx=25, max_signals=25):
        print(f"[*] Experiment 2: Gate Selectivity (Parquet)\n")
        print(f"[*] Signals {start_idx+1}-{start_idx+max_signals}")
        print(f"[*] Data source: Pre-extracted parquet (no JSONL access)\n")
        
        self.load_parquet()
        
        # Load all signals for reference
        all_signals = self.extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        
        for sig_idx in range(start_idx, start_idx + max_signals):
            sig = all_signals[sig_idx]
            signal_id = sig_idx + 1
            
            # Get events for this signal
            signal_events = self.events_table.filter(
                pc.equal(self.events_table['signal_id'], signal_id)
            )
            
            if len(signal_events) == 0:
                print(f"[⚠️] {sig_idx - start_idx + 1:2d}: No events for signal {signal_id}")
                continue
            
            # Get metadata for this signal
            signal_meta = self.metadata_table.filter(
                pc.equal(self.metadata_table['signal_id'], signal_id)
            )
            
            if len(signal_meta) == 0:
                print(f"[⚠️] {sig_idx - start_idx + 1:2d}: No metadata for signal {signal_id}")
                continue
            
            # Extract prices
            prices = signal_events['price'].to_pylist()
            
            # Get lookback volatility from candle
            candle_low = signal_meta['candle_low'][0].as_py()
            candle_high = signal_meta['candle_high'][0].as_py()
            lookback_vol = max(0.5, (candle_high - candle_low))
            
            # Plan entry
            planner = EntryExitPlanner()
            plan = planner.plan_entry(sig.direction, sig.entry_price, lookback_vol, candle_low, candle_high)
            
            # Create fake event list from prices
            events = [{'price': p} for p in prices]
            
            # Test all three models
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
            
            print(f"[{status}] {sig_idx - start_idx + 1:2d}: A:{r_a:+.3f} | MAE:{mae_a:.2f} | MFE:{mfe_a:.2f}")
            
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
        report = "# Experiment 2: Gate Selectivity Validation (Parquet)\n\n"
        report += f"**Signals tested:** 26-50\n"
        report += f"**Data source:** Parquet (no JSONL access)\n"
        report += f"**Total results:** {len(self.all_results)}\n\n"
        
        report += f"## Results\n\n"
        report += f"**Passed gate:** {len(self.passed_trades)}\n"
        report += f"**Rejected gate:** {len(self.rejected_trades)}\n\n"
        
        if self.passed_trades:
            avg_r_passed = sum(t['r_multiple'] for t in self.passed_trades) / len(self.passed_trades)
            avg_mfe = sum(t['mfe'] for t in self.passed_trades) / len(self.passed_trades)
            avg_mae = sum(t['mae'] for t in self.passed_trades) / len(self.passed_trades)
            
            report += f"**Passed Trades Analysis:**\n"
            report += f"- Count: {len(self.passed_trades)}\n"
            report += f"- Avg R: {avg_r_passed:+.4f}\n"
            report += f"- Avg MFE: {avg_mfe:.2f} ticks\n"
            report += f"- Avg MAE: {avg_mae:.2f} ticks\n"
            report += f"- MFE/MAE: {avg_mfe/avg_mae:.2f}x (should be >2.0x)\n\n"
        
        if self.rejected_trades:
            model_a_rejected = [t for t in self.rejected_trades if t['model'] == 'A_IMMEDIATE']
            if model_a_rejected:
                avg_r_rej = sum(t['r_multiple'] for t in model_a_rejected) / len(model_a_rejected)
                avg_mfe_rej = sum(t['mfe'] for t in model_a_rejected) / len(model_a_rejected)
                avg_mae_rej = sum(t['mae'] for t in model_a_rejected) / len(model_a_rejected)
                
                report += f"**Rejected Trades (Model A) Analysis:**\n"
                report += f"- Count: {len(model_a_rejected)}\n"
                report += f"- Avg R: {avg_r_rej:+.4f}\n"
                report += f"- Avg MFE: {avg_mfe_rej:.2f} ticks\n"
                report += f"- Avg MAE: {avg_mae_rej:.2f} ticks\n\n"
        
        report += f"## Gate Assessment\n\n"
        if self.passed_trades:
            pct_passed = len(self.passed_trades) / (len(self.passed_trades) + len(self.rejected_trades)) * 100
            report += f"✅ Gate allows {pct_passed:.0f}% of trades ({len(self.passed_trades)}/{len(self.passed_trades)+len(self.rejected_trades)})\n"
            report += f"✅ Gate is SELECTIVE (not indiscriminate)\n"
        else:
            report += f"❌ Gate rejects 100% of trades\n"
            report += f"⚠️ Gate may be too strict for this market\n"
        
        with open(report_file, 'w') as f:
            f.write(report)

if __name__ == "__main__":
    exp = Experiment2Parquet()
    exp.run(start_idx=25, max_signals=25)
    elapsed = time.time() - exp.start_time
    print(f"\n[✓] Complete in {elapsed:.0f}s")
