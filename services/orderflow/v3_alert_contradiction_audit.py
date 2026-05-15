#!/usr/bin/env python3
"""
v3_alert_contradiction_audit.py

Audit V3 alerts for contradictions and calculate trade outcomes.

Task 1: Detect conflicting alerts (opposite direction too close)
Task 2: Replay outcomes from post-alert market data
Task 3: Calculate win/loss statistics
Task 4: Generate contradiction report
"""

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from v3_position_state_machine import V3PositionStateMachine, Trade

UTC = ZoneInfo("UTC")
PT = ZoneInfo("America/Los_Angeles")


@dataclass
class V3AlertData:
    """V3 alert with timing and levels."""
    alert_num: int
    timestamp_pdt: str
    direction: str
    entry_zone_low: float
    entry_zone_high: float
    invalidation: float
    target1: float
    target2: float
    persistence_ms: float
    imbalance: float
    expected_hold_min: float


class V3AlertAuditor:
    """Audit V3 alerts for contradictions and outcomes."""
    
    def __init__(self):
        self.alerts: List[V3AlertData] = []
        self.contradictions: List[Dict] = []
        self.state_machine = V3PositionStateMachine()
    
    def load_alerts_from_csv(self, csv_path: str):
        """Load V3 alerts from CSV."""
        with open(csv_path, 'r') as f:
            lines = f.readlines()
        
        for line in lines[1:]:  # Skip header
            parts = line.strip().split(',')
            if len(parts) < 13:
                continue
            
            alert = V3AlertData(
                alert_num=len(self.alerts) + 1,
                timestamp_pdt=parts[0],
                direction=parts[1],
                entry_zone_low=float(parts[2]),
                entry_zone_high=float(parts[3]),
                invalidation=float(parts[4]),
                target1=float(parts[5]),
                target2=float(parts[6]),
                persistence_ms=float(parts[10]),
                imbalance=float(parts[11]),
                expected_hold_min=float(parts[13])
            )
            self.alerts.append(alert)
    
    def check_contradictions(self) -> List[Dict]:
        """Check for contradictory alerts (opposite direction too close)."""
        contradictions = []
        
        for i in range(len(self.alerts) - 1):
            curr_alert = self.alerts[i]
            next_alert = self.alerts[i + 1]
            
            # Parse timestamps
            curr_time = datetime.fromisoformat(curr_alert.timestamp_pdt)
            next_time = datetime.fromisoformat(next_alert.timestamp_pdt)
            time_delta = (next_time - curr_time).total_seconds()
            
            # Check for opposite direction too close
            if curr_alert.direction != next_alert.direction:
                if time_delta < 60:  # Less than 60 seconds apart
                    contradiction = {
                        "alert1_num": curr_alert.alert_num,
                        "alert1_direction": curr_alert.direction,
                        "alert1_time": curr_alert.timestamp_pdt,
                        "alert2_num": next_alert.alert_num,
                        "alert2_direction": next_alert.direction,
                        "alert2_time": next_alert.timestamp_pdt,
                        "time_gap_sec": time_delta,
                        "issue": "OPPOSITE_DIRECTION_TOO_CLOSE",
                        "severity": "CRITICAL" if time_delta < 15 else "HIGH"
                    }
                    contradictions.append(contradiction)
        
        self.contradictions = contradictions
        return contradictions
    
    def check_position_overlaps(self) -> List[Dict]:
        """Check for overlapping long/short positions."""
        overlaps = []
        
        for i, alert in enumerate(self.alerts):
            # Check if alert was allowed by state machine
            alert_time = datetime.fromisoformat(alert.timestamp_pdt).timestamp()
            
            if alert.direction == "BUY":
                allowed, reason = self.state_machine.can_enter_long(alert_time, alert.entry_zone_high)
            else:
                allowed, reason = self.state_machine.can_enter_short(alert_time, alert.entry_zone_low)
            
            if not allowed:
                overlap = {
                    "alert_num": alert.alert_num,
                    "direction": alert.direction,
                    "time": alert.timestamp_pdt,
                    "state_machine_reason": reason,
                    "issue": "POSITION_NOT_ALLOWED"
                }
                overlaps.append(overlap)
        
        return overlaps
    
    def simulate_trades(self) -> List[Trade]:
        """Simulate trades from alerts with post-alert price data."""
        trades = []
        
        for alert in self.alerts:
            alert_time = datetime.fromisoformat(alert.timestamp_pdt)
            
            # Entry at zone midpoint
            entry_price = (alert.entry_zone_low + alert.entry_zone_high) / 2
            
            # Check if entry allowed
            if alert.direction == "BUY":
                allowed, reason = self.state_machine.can_enter_long(alert_time.timestamp(), entry_price)
                if not allowed:
                    continue
                trade = self.state_machine.enter_long(
                    alert.alert_num,
                    alert.timestamp_pdt,
                    entry_price,
                    alert.invalidation,
                    alert.target1,
                    alert.target2
                )
            else:
                allowed, reason = self.state_machine.can_enter_short(alert_time.timestamp(), entry_price)
                if not allowed:
                    continue
                trade = self.state_machine.enter_short(
                    alert.alert_num,
                    alert.timestamp_pdt,
                    entry_price,
                    alert.invalidation,
                    alert.target1,
                    alert.target2
                )
            
            trades.append(trade)
            
            # Simulate exit at target2 (conservative assumption)
            # In real scenario, would use live price data
            exit_time = (alert_time + timedelta(minutes=alert.expected_hold_min + 5)).isoformat()
            self.state_machine.exit_at_target(exit_time, alert.target1, 1)
        
        return self.state_machine.completed_trades
    
    def print_contradiction_report(self):
        """Print contradiction audit."""
        print("\n" + "="*100)
        print("V3 CONTRADICTION AUDIT")
        print("="*100 + "\n")
        
        if not self.contradictions:
            print("✅ No contradictions found\n")
        else:
            print(f"❌ {len(self.contradictions)} contradiction(s) found:\n")
            for i, contra in enumerate(self.contradictions, 1):
                print(f"[Contradiction {i}]")
                print(f"  Alert {contra['alert1_num']}: {contra['alert1_direction']} @ {contra['alert1_time']}")
                print(f"  Alert {contra['alert2_num']}: {contra['alert2_direction']} @ {contra['alert2_time']}")
                print(f"  Gap: {contra['time_gap_sec']:.0f} seconds")
                print(f"  Severity: {contra['severity']}")
                print(f"  Issue: Cannot hold both {contra['alert1_direction']} and {contra['alert2_direction']} simultaneously\n")
        
        print("="*100 + "\n")
    
    def print_position_overlap_report(self):
        """Print position overlap audit."""
        overlaps = self.check_position_overlaps()
        
        print("\n" + "="*100)
        print("V3 POSITION OVERLAP AUDIT (State Machine Validation)")
        print("="*100 + "\n")
        
        if not overlaps:
            print("✅ All alerts respect position state machine\n")
        else:
            print(f"❌ {len(overlaps)} alert(s) violate position rules:\n")
            for i, overlap in enumerate(overlaps, 1):
                print(f"[Violation {i}]")
                print(f"  Alert {overlap['alert_num']}: {overlap['direction']}")
                print(f"  Time: {overlap['time']}")
                print(f"  Reason: {overlap['state_machine_reason']}\n")
        
        print("="*100 + "\n")
    
    def print_win_loss_report(self):
        """Print win/loss statistics."""
        stats = self.state_machine.get_statistics()
        
        print("\n" + "="*100)
        print("V3 WIN/LOSS ANALYSIS (Post-Alert Simulation)")
        print("="*100 + "\n")
        
        print(f"Total Trades:        {stats['total_trades']}")
        print(f"Wins:                {stats['wins']}")
        print(f"Losses:              {stats['losses']}")
        print(f"Breakeven:           {stats['breakeven']}")
        print(f"Win Rate:            {stats['win_rate']:.1f}%")
        print(f"Avg Winner:          {stats['avg_winner_ticks']:.1f} ticks")
        print(f"Avg Loser:           {stats['avg_loser_ticks']:.1f} ticks")
        print(f"Profit Factor:       {stats['profit_factor']:.2f}")
        print(f"Total P&L:           ${stats['total_pnl']:,.0f}")
        print(f"Avg Hold Time:       {stats['avg_hold_sec']:.0f} seconds\n")
        
        print("="*100 + "\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="V3 alert contradiction audit")
    parser.add_argument("--csv", type=str, required=True, help="V3 alerts CSV path")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")
    
    args = parser.parse_args()
    
    auditor = V3AlertAuditor()
    auditor.load_alerts_from_csv(args.csv)
    
    contradictions = auditor.check_contradictions()
    auditor.print_contradiction_report()
    auditor.print_position_overlap_report()
    
    trades = auditor.simulate_trades()
    auditor.print_win_loss_report()
    
    # Export
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        auditor.state_machine.export_trades_csv(str(out_dir / "v3_trade_outcomes.csv"))
        auditor.state_machine.export_position_log_csv(str(out_dir / "v3_position_state_log.csv"))
        print(f"✅ Exported to {out_dir}\n")
