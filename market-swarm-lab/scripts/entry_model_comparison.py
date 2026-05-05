#!/usr/bin/env python3
"""
Entry Model Comparison: Immediate vs Reclaim-Start vs Follow-Through-Confirmed

Compare three entry timing strategies on first 25 real signals:
A. Immediate absorption entry (current mechanical)
B. Reclaim-start entry (wait for reclaim to begin)
C. Follow-through-confirmed entry (wait for breakout confirmation)

Constraints: 25 signals, 10 min runtime, immediate results
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

signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
jsonl_path = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")
export_file = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/exports/entry_model_results.csv")
report_file = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/reports/entry_model_comparison.md")

class EntryModelComparison:
    def __init__(self):
        self.start_time = time.time()
        self.extractor = RealSignalExtractor(signals_csv)
        self.accessor = None
        self.results = {'model_a': [], 'model_b': [], 'model_c': []}
        
    def run(self, max_signals=25):
        print(f"[*] Entry Model Comparison: First {max_signals} signals")
        print(f"[*] Loading signals...")
        
        signals = self.extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        signals = signals[:max_signals]
        print(f"[✓] Loaded {len(signals)} signals")
        
        print(f"[*] Building JSONL index...")
        self.accessor = JsonlWindowAccessor(jsonl_path)
        self.accessor.build_index(sample_interval=10000)
        print(f"[✓] Index built")
        
        print(f"\n[*] Comparing entry models on {len(signals)} signals\n")
        
        for idx, sig in enumerate(signals, 1):
            signal_ts = datetime.fromisoformat(sig.signal_event_utc)
            
            # Get lookback for volatility
            lookback_start = signal_ts - timedelta(minutes=15)
            lookback_events = self.accessor.get_window(lookback_start, signal_ts)
            if lookback_events:
                prices = [e['price'] for e in lookback_events]
                lookback_vol = max(0.5, (max(prices) - min(prices)))
            else:
                lookback_vol = 0.5
            
            # Plan entry
            planner = EntryExitPlanner()
            plan = planner.plan_entry(sig.direction, sig.entry_price, lookback_vol, sig.candle_low, sig.candle_high)
            
            # Get outcome window
            outcome_start = signal_ts
            outcome_end = signal_ts + timedelta(minutes=30)
            outcome_events = self.accessor.get_window(outcome_start, outcome_end)
            
            if not outcome_events or len(outcome_events) == 0:
                print(f"[!] {idx:2d}: No outcome events")
                continue
            
            # Validate replay-safe
            is_safe, msg = self.accessor.validate_replay_safe(outcome_start, outcome_end, outcome_events)
            if not is_safe:
                print(f"[-] {idx:2d}: Not replay-safe ({msg})")
                continue
            
            # Model A: Immediate entry (current)
            result_a = self._model_a_immediate(sig, plan, outcome_events)
            
            # Model B: Reclaim-start entry (wait for reclaim to begin)
            result_b = self._model_b_reclaim_start(sig, plan, outcome_events)
            
            # Model C: Follow-through-confirmed entry (wait for breakout)
            result_c = self._model_c_followthrough_confirmed(sig, plan, outcome_events)
            
            self.results['model_a'].append(result_a)
            self.results['model_b'].append(result_b)
            self.results['model_c'].append(result_c)
            
            print(f"[✓] {idx:2d}: A:{result_a['r_multiple']:+.3f} | B:{result_b['r_multiple']:+.3f} | C:{result_c['r_multiple']:+.3f}")
            
            # Check runtime
            elapsed = time.time() - self.start_time
            if elapsed > 600:  # 10 minutes
                print(f"\n[⏱] Runtime limit reached ({elapsed:.0f}s)")
                break
        
        self.export_results()
        self.generate_report()
    
    def _model_a_immediate(self, sig, plan, events) -> Dict:
        """Model A: Immediate entry at signal time (current)"""
        # Use signal entry directly
        outcome = self._find_outcome(sig.direction, plan, events)
        return {
            'signal_ts_utc': sig.signal_event_utc,
            'model': 'A_IMMEDIATE',
            'entry_price': plan.entry_filled_price,
            'stop_price': plan.stop_filled_price,
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'exit_price': outcome['exit_price'],
            'r_multiple': outcome['r_multiple'],
            'holding_seconds': 1800,
        }
    
    def _model_b_reclaim_start(self, sig, plan, events) -> Dict:
        """Model B: Wait for reclaim to START (wait for initial bounce)"""
        # Find first upward move (reclaim starting)
        reclaim_start_idx = 0
        entry_price = plan.entry_filled_price
        
        for i, event in enumerate(events):
            price = event['price']
            if sig.direction == "SHORT":
                if price > entry_price:  # Bounced up
                    reclaim_start_idx = i
                    break
            else:
                if price < entry_price:  # Bounced down
                    reclaim_start_idx = i
                    break
        
        # Use reclaim-start as entry point
        if reclaim_start_idx > 0:
            events_subset = events[reclaim_start_idx:]
            outcome = self._find_outcome_custom(sig.direction, plan, entry_price, events_subset)
        else:
            outcome = self._find_outcome(sig.direction, plan, events)
        
        return {
            'signal_ts_utc': sig.signal_event_utc,
            'model': 'B_RECLAIM_START',
            'entry_price': entry_price,
            'stop_price': plan.stop_filled_price,
            'mae': outcome['mae'],
            'mfe': outcome['mfe'],
            'outcome_type': outcome['type'],
            'exit_price': outcome['exit_price'],
            'r_multiple': outcome['r_multiple'],
            'holding_seconds': 1800 - (reclaim_start_idx * 5),  # Approximate
        }
    
    def _model_c_followthrough_confirmed(self, sig, plan, events) -> Dict:
        """Model C: Wait for follow-through confirmation (breakout beyond initial adverse)"""
        # Find point where price breaks beyond initial adverse extreme
        entry_price = plan.entry_filled_price
        max_adverse = entry_price
        followthrough_idx = None
        
        for i, event in enumerate(events):
            price = event['price']
            
            if sig.direction == "SHORT":
                # Track maximum adverse
                max_adverse = min(max_adverse, price)
                # Wait for price to break below max adverse (confirm follow-through)
                if price < max_adverse - 0.5:
                    followthrough_idx = i
                    break
            else:
                # Track maximum adverse
                max_adverse = max(max_adverse, price)
                # Wait for price to break above max adverse
                if price > max_adverse + 0.5:
                    followthrough_idx = i
                    break
        
        # Use follow-through point as entry
        if followthrough_idx and followthrough_idx > 0:
            events_subset = events[followthrough_idx:]
            outcome = self._find_outcome_custom(sig.direction, plan, entry_price, events_subset)
        else:
            # No follow-through detected, skip or use full window
            outcome = {'type': 'NO_FOLLOWTHROUGH', 'mae': 999, 'mfe': 0, 'exit_price': entry_price, 'r_multiple': 0}
        
        return {
            'signal_ts_utc': sig.signal_event_utc,
            'model': 'C_FOLLOWTHROUGH_CONFIRMED',
            'entry_price': entry_price,
            'stop_price': plan.stop_filled_price,
            'mae': outcome.get('mae', 999),
            'mfe': outcome.get('mfe', 0),
            'outcome_type': outcome.get('type', 'SKIPPED'),
            'exit_price': outcome.get('exit_price', entry_price),
            'r_multiple': outcome.get('r_multiple', 0),
            'holding_seconds': 1800 - (followthrough_idx * 5) if followthrough_idx else 0,
        }
    
    def _find_outcome(self, direction, plan, events) -> Dict:
        """Find outcome on full event window"""
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        exit_price = events[-1]['price'] if events else plan.entry_filled_price
        
        for event in events:
            price = event['price']
            
            if direction == "SHORT":
                move_favorable = plan.entry_filled_price - price
                move_adverse = price - plan.entry_filled_price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
            else:
                move_favorable = price - plan.entry_filled_price
                move_adverse = plan.entry_filled_price - price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
        
        risk = abs(plan.stop_filled_price - plan.entry_filled_price)
        if outcome_type == "STOP_HIT":
            r_multiple = -1.0
        elif outcome_type == "TIMEOUT":
            if direction == "SHORT":
                profit = plan.entry_filled_price - exit_price
            else:
                profit = exit_price - plan.entry_filled_price
            r_multiple = profit / risk if risk > 0 else 0
        else:
            if direction == "SHORT":
                profit = plan.entry_filled_price - exit_price
            else:
                profit = exit_price - plan.entry_filled_price
            r_multiple = profit / risk if risk > 0 else 0
        
        return {
            'type': outcome_type,
            'mae': mae,
            'mfe': mfe,
            'exit_price': exit_price,
            'r_multiple': r_multiple,
        }
    
    def _find_outcome_custom(self, direction, plan, entry_price, events) -> Dict:
        """Find outcome starting from subset of events"""
        if not events:
            return {'type': 'NO_EVENTS', 'mae': 0, 'mfe': 0, 'exit_price': entry_price, 'r_multiple': 0}
        
        mfe = 0.0
        mae = 0.0
        outcome_type = "TIMEOUT"
        exit_price = events[-1]['price']
        
        for event in events:
            price = event['price']
            
            if direction == "SHORT":
                move_favorable = entry_price - price
                move_adverse = price - entry_price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                if price >= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                if price <= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price <= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
            else:
                move_favorable = price - entry_price
                move_adverse = entry_price - price
                
                if move_favorable > mfe:
                    mfe = move_favorable
                if move_adverse > mae:
                    mae = move_adverse
                
                if price <= plan.stop_filled_price:
                    outcome_type = "STOP_HIT"
                    exit_price = plan.stop_filled_price
                    break
                if price >= plan.target_2_filled_price:
                    outcome_type = "TARGET2_HIT"
                    exit_price = plan.target_2_filled_price
                    break
                elif price >= plan.target_1_filled_price:
                    outcome_type = "TARGET1_HIT"
                    exit_price = plan.target_1_filled_price
                    break
        
        risk = abs(plan.stop_filled_price - entry_price)
        if outcome_type == "STOP_HIT":
            r_multiple = -1.0
        elif outcome_type == "TIMEOUT":
            if direction == "SHORT":
                profit = entry_price - exit_price
            else:
                profit = exit_price - entry_price
            r_multiple = profit / risk if risk > 0 else 0
        else:
            if direction == "SHORT":
                profit = entry_price - exit_price
            else:
                profit = exit_price - entry_price
            r_multiple = profit / risk if risk > 0 else 0
        
        return {
            'type': outcome_type,
            'mae': mae,
            'mfe': mfe,
            'exit_price': exit_price,
            'r_multiple': r_multiple,
        }
    
    def export_results(self):
        """Export results to CSV"""
        all_results = []
        for model_key, trades in self.results.items():
            all_results.extend(trades)
        
        if not all_results:
            print("No results to export")
            return
        
        with open(export_file, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=all_results[0].keys())
            w.writeheader()
            w.writerows(all_results)
        
        print(f"\n[✓] Exported {len(all_results)} results to {export_file}")
    
    def generate_report(self):
        """Generate comparison report"""
        report = "# Entry Model Comparison: First 25 Signals\n\n"
        
        for model_key in ['model_a', 'model_b', 'model_c']:
            trades = self.results[model_key]
            if not trades:
                continue
            
            model_name = trades[0]['model'] if trades else model_key
            
            wins = sum(1 for t in trades if t['outcome_type'] in ['TARGET1_HIT', 'TARGET2_HIT'])
            losses = sum(1 for t in trades if t['outcome_type'] == 'STOP_HIT')
            timeouts = sum(1 for t in trades if t['outcome_type'] == 'TIMEOUT')
            skipped = sum(1 for t in trades if t['outcome_type'] in ['NO_FOLLOWTHROUGH', 'NO_EVENTS', 'SKIPPED'])
            
            total = len(trades)
            if total == 0:
                continue
            
            wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
            avg_r = sum(t['r_multiple'] for t in trades) / len(trades)
            avg_mae = sum(t['mae'] for t in trades if t['mae'] < 100) / len([t for t in trades if t['mae'] < 100])
            avg_mfe = sum(t['mfe'] for t in trades) / len(trades)
            
            report += f"## {model_name}\n\n"
            report += f"| Metric | Value |\n"
            report += f"|--------|-------|\n"
            report += f"| Trades | {total} |\n"
            report += f"| Wins | {wins} ({wins/total*100:.0f}%) |\n"
            report += f"| Losses | {losses} ({losses/total*100:.0f}%) |\n"
            report += f"| Timeouts | {timeouts} ({timeouts/total*100:.0f}%) |\n"
            report += f"| Skipped | {skipped} ({skipped/total*100:.0f}%) |\n"
            report += f"| **Avg R** | **{avg_r:+.4f}** |\n"
            report += f"| Avg MAE | {avg_mae:.2f} ticks |\n"
            report += f"| Avg MFE | {avg_mfe:.2f} ticks |\n"
            report += f"| MFE/MAE | {avg_mfe/avg_mae if avg_mae > 0 else 0:.2f}x |\n\n"
        
        # Summary
        report += "## Comparison Summary\n\n"
        
        for model_key in ['model_a', 'model_b', 'model_c']:
            trades = self.results[model_key]
            if trades:
                avg_r = sum(t['r_multiple'] for t in trades) / len(trades)
                print(f"{trades[0]['model']:30s}: {avg_r:+.4f}R avg")
        
        with open(report_file, 'w') as f:
            f.write(report)
        
        print(f"\n[✓] Report written to {report_file}")

if __name__ == "__main__":
    bt = EntryModelComparison()
    bt.run(max_signals=25)
    
    elapsed = time.time() - bt.start_time
    print(f"\n[✓] Complete in {elapsed:.0f}s")
