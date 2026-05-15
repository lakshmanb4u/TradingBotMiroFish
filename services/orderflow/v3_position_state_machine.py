#!/usr/bin/env python3
"""
v3_position_state_machine.py

Position state machine to prevent overlapping long/short positions.

States:
- FLAT: No active position
- LONG_ACTIVE: Long position open
- SHORT_ACTIVE: Short position open
- COOLDOWN: Post-exit cooldown before next trade

Rules:
- Only allow new alerts when FLAT
- Block opposite direction when position active (unless explicit reversal)
- Minimum 60s between opposite-direction signals
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum
from datetime import datetime
from zoneinfo import ZoneInfo
import json

UTC = ZoneInfo("UTC")
PT = ZoneInfo("America/Los_Angeles")


class PositionState(Enum):
    """Position state."""
    FLAT = "FLAT"
    LONG_ACTIVE = "LONG_ACTIVE"
    SHORT_ACTIVE = "SHORT_ACTIVE"
    COOLDOWN = "COOLDOWN"


@dataclass
class Trade:
    """Completed trade with outcome."""
    alert_num: int
    direction: str  # BUY or SELL
    entry_time: str  # PDT
    entry_price: float
    stop: float
    target1: float
    target2: float
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    result_ticks: Optional[float] = None
    result_pnl: Optional[float] = None
    target1_hit: bool = False
    target2_hit: bool = False
    stop_hit: bool = False
    mfe_ticks: float = 0.0  # Max favorable excursion
    mae_ticks: float = 0.0  # Max adverse excursion
    hold_time_sec: float = 0.0


@dataclass
class PositionRecord:
    """Position state at point in time."""
    timestamp: str
    state: str
    direction: Optional[str]
    entry_price: Optional[float]
    current_price: Optional[float]
    unrealized_ticks: Optional[float]
    alert_num: Optional[int]
    reason: str


class V3PositionStateMachine:
    """Position state machine for single-position trading."""
    
    def __init__(self):
        """Initialize state machine."""
        self.state = PositionState.FLAT
        self.active_trade: Optional[Trade] = None
        self.completed_trades: List[Trade] = []
        self.position_log: List[PositionRecord] = []
        
        # Timing
        self.last_entry_time = 0.0
        self.last_exit_time = 0.0
        self.min_opposite_direction_interval_sec = 60.0
        self.position_timeout_sec = 900.0  # 15 minute max hold
    
    def can_enter_long(self, current_time: float, current_ask_price: float) -> Tuple[bool, str]:
        """Check if BUY alert is allowed."""
        
        if self.state == PositionState.FLAT:
            return True, "FLAT_OK"
        
        if self.state == PositionState.LONG_ACTIVE:
            return False, "LONG_ALREADY_ACTIVE"
        
        if self.state == PositionState.SHORT_ACTIVE:
            # Check if opposite direction interval met
            time_since_opposite = current_time - self.last_entry_time
            if time_since_opposite < self.min_opposite_direction_interval_sec:
                return False, f"SHORT_ACTIVE_TOO_RECENT({time_since_opposite:.0f}s)"
            # Would need explicit reversal confirmation
            return False, "SHORT_ACTIVE_REVERSAL_REQUIRED"
        
        if self.state == PositionState.COOLDOWN:
            time_since_exit = current_time - self.last_exit_time
            if time_since_exit >= 30.0:  # 30s cooldown
                return True, "COOLDOWN_EXPIRED"
            return False, f"IN_COOLDOWN({time_since_exit:.0f}s)"
        
        return False, "INVALID_STATE"
    
    def can_enter_short(self, current_time: float, current_bid_price: float) -> Tuple[bool, str]:
        """Check if SELL alert is allowed."""
        
        if self.state == PositionState.FLAT:
            return True, "FLAT_OK"
        
        if self.state == PositionState.SHORT_ACTIVE:
            return False, "SHORT_ALREADY_ACTIVE"
        
        if self.state == PositionState.LONG_ACTIVE:
            # Check if opposite direction interval met
            time_since_opposite = current_time - self.last_entry_time
            if time_since_opposite < self.min_opposite_direction_interval_sec:
                return False, f"LONG_ACTIVE_TOO_RECENT({time_since_opposite:.0f}s)"
            # Would need explicit reversal confirmation
            return False, "LONG_ACTIVE_REVERSAL_REQUIRED"
        
        if self.state == PositionState.COOLDOWN:
            time_since_exit = current_time - self.last_exit_time
            if time_since_exit >= 30.0:  # 30s cooldown
                return True, "COOLDOWN_EXPIRED"
            return False, f"IN_COOLDOWN({time_since_exit:.0f}s)"
        
        return False, "INVALID_STATE"
    
    def enter_long(self, alert_num: int, entry_time: str, entry_price: float, 
                   stop: float, target1: float, target2: float) -> Trade:
        """Enter long position."""
        trade = Trade(
            alert_num=alert_num,
            direction="BUY",
            entry_time=entry_time,
            entry_price=entry_price,
            stop=stop,
            target1=target1,
            target2=target2
        )
        self.active_trade = trade
        self.state = PositionState.LONG_ACTIVE
        self.last_entry_time = datetime.fromisoformat(entry_time).timestamp()
        self._record_state(entry_time, "LONG_ACTIVE", "BUY", entry_price, entry_price, 0.0, alert_num, "ENTERED_LONG")
        return trade
    
    def enter_short(self, alert_num: int, entry_time: str, entry_price: float,
                    stop: float, target1: float, target2: float) -> Trade:
        """Enter short position."""
        trade = Trade(
            alert_num=alert_num,
            direction="SELL",
            entry_time=entry_time,
            entry_price=entry_price,
            stop=stop,
            target1=target1,
            target2=target2
        )
        self.active_trade = trade
        self.state = PositionState.SHORT_ACTIVE
        self.last_entry_time = datetime.fromisoformat(entry_time).timestamp()
        self._record_state(entry_time, "SHORT_ACTIVE", "SELL", entry_price, entry_price, 0.0, alert_num, "ENTERED_SHORT")
        return trade
    
    def exit_at_stop(self, exit_time: str, stop_price: float) -> Optional[Trade]:
        """Exit position at stop loss."""
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        trade.exit_time = exit_time
        trade.exit_price = stop_price
        trade.exit_reason = "STOP_HIT"
        trade.stop_hit = True
        
        if trade.direction == "BUY":
            trade.result_ticks = (stop_price - trade.entry_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100  # NQ = $100/tick
        else:
            trade.result_ticks = (trade.entry_price - stop_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100
        
        trade.hold_time_sec = (datetime.fromisoformat(exit_time).timestamp() - 
                               datetime.fromisoformat(trade.entry_time).timestamp())
        
        self.completed_trades.append(trade)
        self.state = PositionState.COOLDOWN
        self.last_exit_time = datetime.fromisoformat(exit_time).timestamp()
        self.active_trade = None
        self._record_state(exit_time, "COOLDOWN", None, None, stop_price, None, None, "STOPPED_OUT")
        return trade
    
    def exit_at_target(self, exit_time: str, target_price: float, target_num: int) -> Optional[Trade]:
        """Exit position at target."""
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        trade.exit_time = exit_time
        trade.exit_price = target_price
        trade.exit_reason = f"TARGET{target_num}_HIT"
        
        if target_num == 1:
            trade.target1_hit = True
        elif target_num == 2:
            trade.target2_hit = True
        
        if trade.direction == "BUY":
            trade.result_ticks = (target_price - trade.entry_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100
        else:
            trade.result_ticks = (trade.entry_price - target_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100
        
        trade.hold_time_sec = (datetime.fromisoformat(exit_time).timestamp() - 
                               datetime.fromisoformat(trade.entry_time).timestamp())
        
        self.completed_trades.append(trade)
        self.state = PositionState.COOLDOWN
        self.last_exit_time = datetime.fromisoformat(exit_time).timestamp()
        self.active_trade = None
        self._record_state(exit_time, "COOLDOWN", None, None, target_price, None, None, f"TARGET{target_num}_EXIT")
        return trade
    
    def exit_at_time(self, exit_time: str, exit_price: float) -> Optional[Trade]:
        """Exit position at time limit."""
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.exit_reason = "TIME_EXIT"
        
        if trade.direction == "BUY":
            trade.result_ticks = (exit_price - trade.entry_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100
        else:
            trade.result_ticks = (trade.entry_price - exit_price) / 0.25
            trade.result_pnl = trade.result_ticks * 100
        
        trade.hold_time_sec = (datetime.fromisoformat(exit_time).timestamp() - 
                               datetime.fromisoformat(trade.entry_time).timestamp())
        
        self.completed_trades.append(trade)
        self.state = PositionState.COOLDOWN
        self.last_exit_time = datetime.fromisoformat(exit_time).timestamp()
        self.active_trade = None
        self._record_state(exit_time, "COOLDOWN", None, None, exit_price, None, None, "TIME_EXIT")
        return trade
    
    def _record_state(self, timestamp: str, state: str, direction: Optional[str],
                      entry_price: Optional[float], current_price: Optional[float],
                      unrealized_ticks: Optional[float], alert_num: Optional[int],
                      reason: str):
        """Record position state change."""
        record = PositionRecord(
            timestamp=timestamp,
            state=state,
            direction=direction,
            entry_price=entry_price,
            current_price=current_price,
            unrealized_ticks=unrealized_ticks,
            alert_num=alert_num,
            reason=reason
        )
        self.position_log.append(record)
    
    def get_statistics(self) -> Dict:
        """Calculate trade statistics."""
        if not self.completed_trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "avg_winner": 0.0,
                "avg_loser": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "avg_hold_sec": 0.0
            }
        
        wins = sum(1 for t in self.completed_trades if t.result_ticks > 0.5)
        losses = sum(1 for t in self.completed_trades if t.result_ticks < -0.5)
        breakeven = len(self.completed_trades) - wins - losses
        
        winner_ticks = [t.result_ticks for t in self.completed_trades if t.result_ticks > 0.5]
        loser_ticks = [abs(t.result_ticks) for t in self.completed_trades if t.result_ticks < -0.5]
        
        avg_winner = sum(winner_ticks) / len(winner_ticks) if winner_ticks else 0.0
        avg_loser = sum(loser_ticks) / len(loser_ticks) if loser_ticks else 0.0
        
        gross_profit = sum(t.result_pnl for t in self.completed_trades if t.result_pnl > 0)
        gross_loss = abs(sum(t.result_pnl for t in self.completed_trades if t.result_pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        total_pnl = sum(t.result_pnl for t in self.completed_trades)
        avg_hold = sum(t.hold_time_sec for t in self.completed_trades) / len(self.completed_trades)
        
        return {
            "total_trades": len(self.completed_trades),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": 100 * wins / len(self.completed_trades) if self.completed_trades else 0.0,
            "avg_winner_ticks": avg_winner,
            "avg_loser_ticks": avg_loser,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "avg_hold_sec": avg_hold,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss
        }
    
    def export_trades_csv(self, csv_path: str):
        """Export completed trades to CSV."""
        with open(csv_path, 'w') as f:
            f.write("alert_num,direction,entry_time,entry_price,stop,target1,target2,exit_time,exit_price,exit_reason,result_ticks,result_pnl,target1_hit,target2_hit,stop_hit,hold_time_sec\n")
            for trade in self.completed_trades:
                f.write(
                    f"{trade.alert_num},{trade.direction},{trade.entry_time},{trade.entry_price:.2f},"
                    f"{trade.stop:.2f},{trade.target1:.2f},{trade.target2:.2f},"
                    f"{trade.exit_time or 'OPEN'},{trade.exit_price or '':.2f if trade.exit_price else ''},"
                    f"{trade.exit_reason or ''},"
                    f"{trade.result_ticks or '':.1f' if trade.result_ticks else ''},"
                    f"{trade.result_pnl or '':.0f' if trade.result_pnl else ''},"
                    f"{trade.target1_hit},{trade.target2_hit},{trade.stop_hit},"
                    f"{trade.hold_time_sec:.0f}\n"
                )
    
    def export_position_log_csv(self, csv_path: str):
        """Export position state log to CSV."""
        with open(csv_path, 'w') as f:
            f.write("timestamp,state,direction,entry_price,current_price,unrealized_ticks,alert_num,reason\n")
            for record in self.position_log:
                f.write(
                    f"{record.timestamp},{record.state},{record.direction or ''},"
                    f"{record.entry_price or '':.2f' if record.entry_price else ''},"
                    f"{record.current_price or '':.2f' if record.current_price else ''},"
                    f"{record.unrealized_ticks or '':.1f' if record.unrealized_ticks is not None else ''},"
                    f"{record.alert_num or ''},{record.reason}\n"
                )
