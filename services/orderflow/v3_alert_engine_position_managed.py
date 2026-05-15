#!/usr/bin/env python3
"""
v3_alert_engine_position_managed.py

V3 Alert engine with position state machine integration.

Now enforces:
- Single position at a time (no overlapping long/short)
- Minimum 60s between opposite-direction signals
- Full trade lifecycle (entry → exit → cooldown)
- Reversal criteria (6.0x+ imbalance, 5s+ persistence)
"""

import json
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
PT = ZoneInfo("America/Los_Angeles")


class PositionState(Enum):
    """Position states."""
    FLAT = "FLAT"
    LONG_ACTIVE = "LONG_ACTIVE"
    SHORT_ACTIVE = "SHORT_ACTIVE"
    COOLDOWN = "COOLDOWN"


@dataclass
class CompletedTrade:
    """Completed trade with full lifecycle."""
    trade_num: int
    direction: str
    entry_time_pdt: str
    entry_price: float
    exit_time_pdt: Optional[str] = None
    exit_price: Optional[float] = None
    stop: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    exit_reason: Optional[str] = None
    result_ticks: Optional[float] = None
    result_pnl: Optional[float] = None
    mfe_ticks: float = 0.0
    mae_ticks: float = 0.0
    hold_duration_sec: float = 0.0
    human_score: float = 0.0
    target1_hit: bool = False
    target2_hit: bool = False
    stop_hit: bool = False


@dataclass
class ActiveTrade:
    """Active (open) trade."""
    trade_num: int
    direction: str
    entry_time_pdt: str
    entry_time_sec: float
    entry_price: float
    stop: float
    target1: float
    target2: float
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    current_unrealized_ticks: float = 0.0


class BookState:
    """Current best bid/ask."""
    def __init__(self):
        self.bid_ladder: Dict[float, float] = {}
        self.ask_ladder: Dict[float, float] = {}
        self.bid_ts: Optional[str] = None
        self.ask_ts: Optional[str] = None
    
    def update_bid(self, price: float, size: float):
        if size == 0 and price in self.bid_ladder:
            del self.bid_ladder[price]
        elif size > 0:
            self.bid_ladder[price] = size
    
    def update_ask(self, price: float, size: float):
        if size == 0 and price in self.ask_ladder:
            del self.ask_ladder[price]
        elif size > 0:
            self.ask_ladder[price] = size
    
    def get_best_bid(self) -> Optional[Tuple[float, float]]:
        active = [(p, s) for p, s in self.bid_ladder.items() if s > 0]
        return max(active, key=lambda x: x[0]) if active else None
    
    def get_best_ask(self) -> Optional[Tuple[float, float]]:
        active = [(p, s) for p, s in self.ask_ladder.items() if s > 0]
        return min(active, key=lambda x: x[0]) if active else None
    
    def is_valid(self) -> bool:
        return self.get_best_bid() is not None and self.get_best_ask() is not None
    
    def spread_ticks(self) -> float:
        bid = self.get_best_bid()
        ask = self.get_best_ask()
        if not bid or not ask:
            return 0
        return (ask[0] - bid[0]) / 0.25
    
    def imbalance_ratio_buy(self) -> float:
        bid = self.get_best_bid()
        ask = self.get_best_ask()
        if not bid or not ask or ask[1] == 0:
            return 0.0
        return bid[1] / ask[1]
    
    def imbalance_ratio_sell(self) -> float:
        bid = self.get_best_bid()
        ask = self.get_best_ask()
        if not bid or not ask or bid[1] == 0:
            return 0.0
        return ask[1] / bid[1]


class V3PositionManagedEngine:
    """V3 engine with position state machine."""
    
    def __init__(self):
        """Initialize."""
        self.book = BookState()
        self.position_state = PositionState.FLAT
        self.active_trade: Optional[ActiveTrade] = None
        self.completed_trades: List[CompletedTrade] = []
        
        # Timing
        self.last_exit_time = 0.0
        self.min_opposite_interval_sec = 60.0
        self.position_timeout_sec = 900.0  # 15 min max hold
        self.cooldown_duration_sec = 30.0
        self.cooldown_until = 0.0
        
        # Thresholds
        self.imbalance_threshold = 2.5
        self.persistence_threshold_ms = 5000
        self.spread_threshold_ticks = 4
        self.reversal_imbalance_threshold = 6.0
        
        # Alert history
        self.bid_history: List[Tuple[float, float]] = []
        self.ask_history: List[Tuple[float, float]] = []
        self.recent_bids: List[float] = []
        self.recent_asks: List[float] = []
        
        self.bid_dominance_start: Optional[float] = None
        self.ask_dominance_start: Optional[float] = None
        
        # Counters
        self.trade_counter = 0
        self.alerts_generated = 0
        
        logger.info("V3PositionManagedEngine initialized with position state machine")
    
    def _check_buy_persistence(self) -> Tuple[bool, float]:
        """Check if bid dominance persisted >= 5s."""
        now = time.time()
        if self.book.imbalance_ratio_buy() < self.imbalance_threshold:
            self.bid_dominance_start = None
            return False, 0.0
        
        if self.bid_dominance_start is None:
            self.bid_dominance_start = now
            return False, 0.0
        
        persistence_ms = (now - self.bid_dominance_start) * 1000
        if persistence_ms >= self.persistence_threshold_ms and len(self.recent_bids) >= 3:
            if self.recent_bids[-1] > self.recent_bids[0]:
                return True, persistence_ms
        
        return False, persistence_ms
    
    def _check_sell_persistence(self) -> Tuple[bool, float]:
        """Check if ask dominance persisted >= 5s."""
        now = time.time()
        if self.book.imbalance_ratio_sell() < self.imbalance_threshold:
            self.ask_dominance_start = None
            return False, 0.0
        
        if self.ask_dominance_start is None:
            self.ask_dominance_start = now
            return False, 0.0
        
        persistence_ms = (now - self.ask_dominance_start) * 1000
        if persistence_ms >= self.persistence_threshold_ms and len(self.recent_asks) >= 3:
            if self.recent_asks[-1] < self.recent_asks[0]:
                return True, persistence_ms
        
        return False, persistence_ms
    
    def _can_enter_long(self, current_time: float) -> Tuple[bool, str]:
        """Check if BUY allowed by position state machine."""
        now = time.time()
        
        # Check cooldown
        if now < self.cooldown_until:
            return False, f"IN_COOLDOWN({self.cooldown_until - now:.0f}s)"
        
        if self.position_state == PositionState.FLAT:
            return True, "FLAT_OK"
        elif self.position_state == PositionState.LONG_ACTIVE:
            return False, "LONG_ALREADY_ACTIVE"
        elif self.position_state == PositionState.SHORT_ACTIVE:
            # Check reversal criteria
            time_since_entry = now - self.last_exit_time
            if time_since_entry < self.min_opposite_interval_sec:
                return False, f"REVERSAL_TOO_SOON({time_since_entry:.0f}s)"
            
            # Check reversal confirmation
            imbalance = self.book.imbalance_ratio_buy()
            if imbalance < self.reversal_imbalance_threshold:
                return False, f"REVERSAL_IMBALANCE_LOW({imbalance:.2f}x, need {self.reversal_imbalance_threshold}x)"
            
            return True, "REVERSAL_ALLOWED"
        
        return False, "INVALID_STATE"
    
    def _can_enter_short(self, current_time: float) -> Tuple[bool, str]:
        """Check if SELL allowed by position state machine."""
        now = time.time()
        
        # Check cooldown
        if now < self.cooldown_until:
            return False, f"IN_COOLDOWN({self.cooldown_until - now:.0f}s)"
        
        if self.position_state == PositionState.FLAT:
            return True, "FLAT_OK"
        elif self.position_state == PositionState.SHORT_ACTIVE:
            return False, "SHORT_ALREADY_ACTIVE"
        elif self.position_state == PositionState.LONG_ACTIVE:
            # Check reversal criteria
            time_since_entry = now - self.last_exit_time
            if time_since_entry < self.min_opposite_interval_sec:
                return False, f"REVERSAL_TOO_SOON({time_since_entry:.0f}s)"
            
            # Check reversal confirmation
            imbalance = self.book.imbalance_ratio_sell()
            if imbalance < self.reversal_imbalance_threshold:
                return False, f"REVERSAL_IMBALANCE_LOW({imbalance:.2f}x, need {self.reversal_imbalance_threshold}x)"
            
            return True, "REVERSAL_ALLOWED"
        
        return False, "INVALID_STATE"
    
    def _exit_trade(self, exit_time: str, exit_price: float, reason: str) -> Optional[CompletedTrade]:
        """Exit active trade and complete it."""
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        exit_time_sec = datetime.fromisoformat(exit_time).timestamp()
        
        # Calculate result
        if trade.direction == "BUY":
            result_ticks = (exit_price - trade.entry_price) / 0.25
        else:
            result_ticks = (trade.entry_price - exit_price) / 0.25
        
        completed = CompletedTrade(
            trade_num=trade.trade_num,
            direction=trade.direction,
            entry_time_pdt=trade.entry_time_pdt,
            entry_price=trade.entry_price,
            exit_time_pdt=exit_time,
            exit_price=exit_price,
            stop=trade.stop,
            target1=trade.target1,
            target2=trade.target2,
            exit_reason=reason,
            result_ticks=result_ticks,
            result_pnl=result_ticks * 100,
            mfe_ticks=trade.max_favorable_excursion,
            mae_ticks=trade.max_adverse_excursion,
            hold_duration_sec=exit_time_sec - trade.entry_time_sec,
            target1_hit=(reason == "TARGET1_HIT"),
            target2_hit=(reason == "TARGET2_HIT"),
            stop_hit=(reason == "STOP_HIT")
        )
        
        # Human score
        if result_ticks >= 20:
            completed.human_score = 95.0
        elif result_ticks >= 10:
            completed.human_score = 85.0
        elif result_ticks >= 0:
            completed.human_score = 70.0
        else:
            completed.human_score = 40.0
        
        self.completed_trades.append(completed)
        self.position_state = PositionState.COOLDOWN
        self.last_exit_time = exit_time_sec
        self.cooldown_until = time.time() + self.cooldown_duration_sec
        self.active_trade = None
        
        return completed
    
    def process_event(self, obj: Dict) -> Optional[CompletedTrade]:
        """Process one orderflow event."""
        
        side = obj.get('side')
        price = obj.get('price')
        size = obj.get('size')
        ts_recv = obj.get('ts_recv')
        
        if not all([side, price is not None, size is not None, ts_recv]):
            return None
        
        now = time.time()
        
        # Update book
        if side == 'bid':
            self.book.update_bid(price, size)
            self.book.bid_ts = ts_recv
            self.bid_history.append((price, now))
            self.recent_bids.append(price)
            if len(self.bid_history) > 500:
                self.bid_history.pop(0)
            if len(self.recent_bids) > 100:
                self.recent_bids.pop(0)
        elif side == 'ask':
            self.book.update_ask(price, size)
            self.book.ask_ts = ts_recv
            self.ask_history.append((price, now))
            self.recent_asks.append(price)
            if len(self.ask_history) > 500:
                self.ask_history.pop(0)
            if len(self.recent_asks) > 100:
                self.recent_asks.pop(0)
        else:
            return None
        
        # Update active trade MFE/MAE if open
        if self.active_trade and self.book.is_valid():
            bid = self.book.get_best_bid()
            ask = self.book.get_best_ask()
            mid = (bid[0] + ask[0]) / 2 if bid and ask else self.active_trade.entry_price
            
            if self.active_trade.direction == "BUY":
                mfe_ticks = max(self.active_trade.max_favorable_excursion, (mid - self.active_trade.entry_price) / 0.25)
                mae_ticks = min(0, min(self.active_trade.max_adverse_excursion, (mid - self.active_trade.entry_price) / 0.25))
                self.active_trade.max_favorable_excursion = mfe_ticks
                self.active_trade.max_adverse_excursion = mae_ticks
                
                # Check exit conditions
                if bid and price <= self.active_trade.stop:
                    return self._exit_trade(ts_recv, self.active_trade.stop, "STOP_HIT")
                if ask and price >= self.active_trade.target1:
                    return self._exit_trade(ts_recv, self.active_trade.target1, "TARGET1_HIT")
                if ask and price >= self.active_trade.target2:
                    return self._exit_trade(ts_recv, self.active_trade.target2, "TARGET2_HIT")
                
                # Check timeout
                hold_time = (now - self.active_trade.entry_time_sec) / 60
                if hold_time >= 15:
                    return self._exit_trade(ts_recv, mid, "TIME_EXIT")
            
            else:  # SHORT
                mfe_ticks = max(self.active_trade.max_favorable_excursion, (self.active_trade.entry_price - mid) / 0.25)
                mae_ticks = min(0, min(self.active_trade.max_adverse_excursion, (self.active_trade.entry_price - mid) / 0.25))
                self.active_trade.max_favorable_excursion = mfe_ticks
                self.active_trade.max_adverse_excursion = mae_ticks
                
                # Check exit conditions
                if ask and price >= self.active_trade.stop:
                    return self._exit_trade(ts_recv, self.active_trade.stop, "STOP_HIT")
                if bid and price <= self.active_trade.target1:
                    return self._exit_trade(ts_recv, self.active_trade.target1, "TARGET1_HIT")
                if bid and price <= self.active_trade.target2:
                    return self._exit_trade(ts_recv, self.active_trade.target2, "TARGET2_HIT")
                
                # Check timeout
                hold_time = (now - self.active_trade.entry_time_sec) / 60
                if hold_time >= 15:
                    return self._exit_trade(ts_recv, mid, "TIME_EXIT")
        
        # Check for new alerts
        if self.position_state in [PositionState.FLAT, PositionState.COOLDOWN]:
            if now >= self.cooldown_until:
                self.position_state = PositionState.FLAT
        
        # BUY alert
        buy_persistent, buy_persistence = self._check_buy_persistence()
        if buy_persistent and self.book.is_valid():
            allowed, reason = self._can_enter_long(now)
            if allowed and self.book.spread_ticks() <= self.spread_threshold_ticks:
                bid = self.book.get_best_bid()
                ask = self.book.get_best_ask()
                entry_price = (ask[0] + ask[0] + 0.25) / 2  # Entry zone midpoint
                
                dt = datetime.fromisoformat(ts_recv.replace('Z', '+00:00')).astimezone(PT)
                
                self.trade_counter += 1
                self.active_trade = ActiveTrade(
                    trade_num=self.trade_counter,
                    direction="BUY",
                    entry_time_pdt=dt.isoformat(),
                    entry_time_sec=now,
                    entry_price=entry_price,
                    stop=entry_price - 2.00,
                    target1=entry_price + 5.00,
                    target2=entry_price + 15.00
                )
                self.position_state = PositionState.LONG_ACTIVE
                self.alerts_generated += 1
                return None
        
        # SELL alert
        sell_persistent, sell_persistence = self._check_sell_persistence()
        if sell_persistent and self.book.is_valid():
            allowed, reason = self._can_enter_short(now)
            if allowed and self.book.spread_ticks() <= self.spread_threshold_ticks:
                bid = self.book.get_best_bid()
                ask = self.book.get_best_ask()
                entry_price = (bid[0] + bid[0] - 0.25) / 2  # Entry zone midpoint
                
                dt = datetime.fromisoformat(ts_recv.replace('Z', '+00:00')).astimezone(PT)
                
                self.trade_counter += 1
                self.active_trade = ActiveTrade(
                    trade_num=self.trade_counter,
                    direction="SELL",
                    entry_time_pdt=dt.isoformat(),
                    entry_time_sec=now,
                    entry_price=entry_price,
                    stop=entry_price + 2.00,
                    target1=entry_price - 5.00,
                    target2=entry_price - 15.00
                )
                self.position_state = PositionState.SHORT_ACTIVE
                self.alerts_generated += 1
                return None
        
        return None
    
    def print_summary(self):
        """Print trade summary."""
        print("\n" + "="*100)
        print("V3 POSITION-MANAGED TRADES")
        print("="*100 + "\n")
        
        for trade in self.completed_trades:
            print(f"[Trade {trade.trade_num}] {trade.direction}")
            print(f"  Entry:    {trade.entry_time_pdt}")
            print(f"  Exit:     {trade.exit_time_pdt}")
            print(f"  Price:    {trade.entry_price:.2f} → {trade.exit_price:.2f}")
            print(f"  Stop/T1/T2: {trade.stop:.2f} / {trade.target1:.2f} / {trade.target2:.2f}")
            print(f"  Result:   {trade.result_ticks:+.1f}t (${trade.result_pnl:+.0f})")
            print(f"  Hold:     {trade.hold_duration_sec:.0f}s")
            print(f"  MFE/MAE:  {trade.mfe_ticks:.1f}t / {abs(trade.mae_ticks):.1f}t")
            print(f"  Exit:     {trade.exit_reason}")
            print(f"  Score:    {trade.human_score:.0f}/100")
            print()
        
        if self.completed_trades:
            stats = self._calculate_stats()
            print("="*100)
            print("STATISTICS")
            print("="*100)
            print(f"Total trades:    {stats['total']}")
            print(f"Wins:            {stats['wins']}")
            print(f"Losses:          {stats['losses']}")
            print(f"Win rate:        {stats['win_rate']:.1f}%")
            print(f"Avg winner:      {stats['avg_winner']:.1f}t")
            print(f"Avg loser:       {stats['avg_loser']:.1f}t")
            print(f"Total P&L:       ${stats['total_pnl']:+.0f}")
            print(f"Avg hold:        {stats['avg_hold']:.0f}s\n")
        
        print("="*100 + "\n")
    
    def _calculate_stats(self) -> Dict:
        """Calculate trade statistics."""
        if not self.completed_trades:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_winner": 0, "avg_loser": 0, "total_pnl": 0, "avg_hold": 0}
        
        wins = sum(1 for t in self.completed_trades if t.result_ticks > 0.5)
        losses = sum(1 for t in self.completed_trades if t.result_ticks < -0.5)
        winner_ticks = [t.result_ticks for t in self.completed_trades if t.result_ticks > 0.5]
        loser_ticks = [abs(t.result_ticks) for t in self.completed_trades if t.result_ticks < -0.5]
        
        return {
            "total": len(self.completed_trades),
            "wins": wins,
            "losses": losses,
            "win_rate": 100 * wins / len(self.completed_trades) if self.completed_trades else 0,
            "avg_winner": sum(winner_ticks) / len(winner_ticks) if winner_ticks else 0,
            "avg_loser": sum(loser_ticks) / len(loser_ticks) if loser_ticks else 0,
            "total_pnl": sum(t.result_pnl for t in self.completed_trades),
            "avg_hold": sum(t.hold_duration_sec for t in self.completed_trades) / len(self.completed_trades) if self.completed_trades else 0
        }
    
    def export_trades_csv(self, csv_path: str):
        """Export completed trades to CSV."""
        with open(csv_path, 'w') as f:
            f.write("trade_num,direction,entry_time,entry_price,exit_time,exit_price,stop,target1,target2,exit_reason,result_ticks,result_pnl,mfe_ticks,mae_ticks,hold_sec,human_score\n")
            for trade in self.completed_trades:
                f.write(
                    f"{trade.trade_num},{trade.direction},{trade.entry_time_pdt},{trade.entry_price:.2f},"
                    f"{trade.exit_time_pdt},{trade.exit_price:.2f},"
                    f"{trade.stop:.2f},{trade.target1:.2f},{trade.target2:.2f},"
                    f"{trade.exit_reason},{trade.result_ticks:.1f},{trade.result_pnl:.0f},"
                    f"{trade.mfe_ticks:.1f},{abs(trade.mae_ticks):.1f},"
                    f"{trade.hold_duration_sec:.0f},{trade.human_score:.0f}\n"
                )
        print(f"✅ Exported {len(self.completed_trades)} trades to {csv_path}")
