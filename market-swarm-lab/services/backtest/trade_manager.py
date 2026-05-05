"""Multi-stage Trade Manager with partial profit-taking and trailing stops.

This module provides configurable trade management strategies:
1. Full-runner (current behavior) - hold until stop/target
2. Partial-profit - scale out at +1R, +2R, trail remainder
3. Trailing stops - ATR-based, EMA-based, or swing-low based

Usage:
    from trade_manager import TradeState, MultiStageManager, ExitStage
    
    manager = MultiStageManager(strategy="partial_profit")
    state = TradeState(action="BUY", entry_price=100, stop_loss=99, target_1=102, target_2=104)
    
    for bar in bars:
        exits = manager.process_bar(state, bar, indicators)
        if state.is_closed:
            break
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)


class ExitStage(Enum):
    """Trade exit stages for partial profit-taking."""
    FULL = "full"           # 100% position, no partials
    STAGE1 = "stage1"       # 50% off at +1R, stop→breakeven
    STAGE2 = "stage2"       # +25% off at +2R
    STAGE3 = "stage3"       # Trail remaining 25%
    CLOSED = "closed"


class TrailType(Enum):
    """Types of trailing stops."""
    NONE = "none"
    ATR = "atr"             # ATR multiplier trail
    EMA = "ema"             # EMA-based trail
    SWING = "swing"         # Swing low/high trail


@dataclass
class PartialExit:
    """Record of a partial exit."""
    stage: ExitStage
    ts: datetime
    price: float
    pct_closed: float      # % of original position closed
    pnl_r: float          # PnL in R terms for this exit


@dataclass 
class TradeState:
    """Mutable state for an open trade."""
    action: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    
    # Position sizing
    original_qty: float = 1.0
    remaining_pct: float = 1.0
    
    # Exit tracking
    stage: ExitStage = ExitStage.FULL
    partial_exits: list[PartialExit] = field(default_factory=list)
    
    # PnL tracking
    realized_pnl_r: float = 0.0
    exit_price: Optional[float] = None
    exit_reason: str = ""
    exit_ts: Optional[datetime] = None
    
    # Excursion tracking
    mfe: float = 0.0      # Max favorable excursion in R
    mae: float = 0.0      # Max adverse excursion in R
    
    @property
    def is_closed(self) -> bool:
        return self.stage == ExitStage.CLOSED
    
    @property
    def risk_pts(self) -> float:
        """Initial risk in price points."""
        return abs(self.entry_price - self.stop_loss)
    
    def current_pnl_r(self, current_price: float) -> float:
        """Current unrealized PnL in R terms."""
        if self.risk_pts <= 0:
            return 0.0
        if self.action == "BUY":
            return (current_price - self.entry_price) / self.risk_pts
        else:
            return (self.entry_price - current_price) / self.risk_pts
    
    def update_excursion(self, current_price: float) -> None:
        """Update MFE/MAE."""
        pnl_r = self.current_pnl_r(current_price)
        self.mfe = max(self.mfe, pnl_r)
        self.mae = min(self.mae, pnl_r)


class MultiStageManager:
    """Manages trade exits with partial profit-taking and trailing stops."""
    
    def __init__(
        self,
        strategy: str = "full_runner",  # full_runner | partial_profit
        trail_type: str = "none",       # none | atr | ema | swing
        trail_params: Optional[dict] = None,
    ):
        self.strategy = strategy
        self.trail_type = TrailType(trail_type)
        self.trail_params = trail_params or {}
        
        # Default partial profit levels
        self.stage1_r = 1.0   # Close 50% at +1R
        self.stage2_r = 2.0   # Close 25% at +2R  
        self.stage3_r = 3.0   # Trail remaining 25%
        
        # Trail params
        self.atr_multiplier = self.trail_params.get("atr_mult", 2.0)
        self.ema_period = self.trail_params.get("ema_period", 21)
        
        _log.info("[trade_mgr] Initialized: strategy=%s trail=%s", strategy, trail_type)
    
    def process_bar(self, trade: TradeState, bar: dict, indicators: dict) -> list[PartialExit]:
        """Process a new bar and return any exits that occurred.
        
        Args:
            trade: Current trade state
            bar: Current price bar {open, high, low, close, ts}
            indicators: Current indicators {ema9, ema21, atr, etc}
        
        Returns:
            List of partial exits that occurred this bar
        """
        if trade.is_closed:
            return []
        
        exits = []
        current_price = bar["close"]
        
        # Update excursion tracking
        trade.update_excursion(current_price)
        
        # Get current PnL in R
        pnl_r = trade.current_pnl_r(current_price)
        
        # Check stop loss first (always active)
        if self._check_stop(trade, bar):
            exit_pct = trade.remaining_pct
            exit_r = trade.current_pnl_r(trade.stop_loss) * exit_pct
            trade.realized_pnl_r += exit_r
            trade.remaining_pct = 0.0
            trade.stage = ExitStage.CLOSED
            trade.exit_price = trade.stop_loss
            trade.exit_reason = "stop"
            trade.exit_ts = bar["ts"]
            
            exits.append(PartialExit(
                stage=ExitStage.CLOSED,
                ts=bar["ts"],
                price=trade.stop_loss,
                pct_closed=exit_pct,
                pnl_r=exit_r
            ))
            return exits
        
        # Strategy-specific logic
        if self.strategy == "partial_profit":
            exits.extend(self._check_partial_exits(trade, bar, pnl_r))
        
        # Check trailing stop (if active)
        if not trade.is_closed and trade.stage in (ExitStage.STAGE3, ExitStage.FULL):
            trail_stop = self._calculate_trail_stop(trade, bar, indicators)
            if trail_stop and self._check_trail_stop(trade, bar, trail_stop):
                exit_pct = trade.remaining_pct
                exit_r = trade.current_pnl_r(trail_stop) * exit_pct
                trade.realized_pnl_r += exit_r
                trade.remaining_pct = 0.0
                trade.stage = ExitStage.CLOSED
                trade.exit_price = trail_stop
                trade.exit_reason = "trail_stop"
                trade.exit_ts = bar["ts"]
                
                exits.append(PartialExit(
                    stage=ExitStage.CLOSED,
                    ts=bar["ts"],
                    price=trail_stop,
                    pct_closed=exit_pct,
                    pnl_r=exit_r
                ))
        
        return exits
    
    def _check_stop(self, trade: TradeState, bar: dict) -> bool:
        """Check if stop loss was hit this bar."""
        if trade.action == "BUY":
            return bar["low"] <= trade.stop_loss
        else:
            return bar["high"] >= trade.stop_loss
    
    def _check_partial_exits(self, trade: TradeState, bar: dict, pnl_r: float) -> list[PartialExit]:
        """Check for partial profit-taking exits."""
        exits = []
        
        # Stage 1: At +1R, close 50%, move stop to breakeven
        if trade.stage == ExitStage.FULL and pnl_r >= self.stage1_r:
            close_pct = 0.50
            exit_r = pnl_r * close_pct
            trade.realized_pnl_r += exit_r
            trade.remaining_pct -= close_pct
            trade.stage = ExitStage.STAGE1
            
            # Move stop to breakeven (or better)
            if trade.action == "BUY":
                trade.stop_loss = max(trade.stop_loss, trade.entry_price)
            else:
                trade.stop_loss = min(trade.stop_loss, trade.entry_price)
            
            exits.append(PartialExit(
                stage=ExitStage.STAGE1,
                ts=bar["ts"],
                price=bar["close"],
                pct_closed=close_pct,
                pnl_r=exit_r
            ))
        
        # Stage 2: At +2R, close additional 25%
        if trade.stage == ExitStage.STAGE1 and pnl_r >= self.stage2_r:
            close_pct = 0.25
            exit_r = pnl_r * close_pct
            trade.realized_pnl_r += exit_r
            trade.remaining_pct -= close_pct
            trade.stage = ExitStage.STAGE2
            
            exits.append(PartialExit(
                stage=ExitStage.STAGE2,
                ts=bar["ts"],
                price=bar["close"],
                pct_closed=close_pct,
                pnl_r=exit_r
            ))
        
        # Stage 3: At +3R, activate trailing stop
        if trade.stage == ExitStage.STAGE2 and pnl_r >= self.stage3_r:
            trade.stage = ExitStage.STAGE3
            # Don't close position, just activate trailing
            _log.debug("[trade_mgr] Stage 3 activated for trade at %s", bar["ts"])
        
        return exits
    
    def _calculate_trail_stop(self, trade: TradeState, bar: dict, indicators: dict) -> Optional[float]:
        """Calculate trailing stop price based on trail type."""
        if self.trail_type == TrailType.ATR:
            atr = indicators.get("atr", 0)
            if atr > 0:
                if trade.action == "BUY":
                    return bar["close"] - atr * self.atr_multiplier
                else:
                    return bar["close"] + atr * self.atr_multiplier
        
        elif self.trail_type == TrailType.EMA:
            ema = indicators.get(f"ema{self.ema_period}", 0)
            if ema > 0:
                if trade.action == "BUY":
                    # Trail below EMA
                    return ema * 0.995  # 0.5% buffer below EMA
                else:
                    return ema * 1.005  # 0.5% buffer above EMA
        
        elif self.trail_type == TrailType.SWING:
            # Use recent swing low/high
            lookback = self.trail_params.get("swing_lookback", 5)
            # This would need price history - simplified here
            if trade.action == "BUY":
                return bar["low"] * 0.998  # Slightly below current low
            else:
                return bar["high"] * 1.002  # Slightly above current high
        
        return None
    
    def _check_trail_stop(self, trade: TradeState, bar: dict, trail_stop: float) -> bool:
        """Check if trailing stop was hit."""
        if trade.action == "BUY":
            return bar["low"] <= trail_stop
        else:
            return bar["high"] >= trail_stop
    
    def close_eod(self, trade: TradeState, bar: dict) -> PartialExit:
        """Close remaining position at end of day."""
        exit_pct = trade.remaining_pct
        pnl_r = trade.current_pnl_r(bar["close"])
        exit_r = pnl_r * exit_pct
        
        trade.realized_pnl_r += exit_r
        trade.remaining_pct = 0.0
        trade.stage = ExitStage.CLOSED
        trade.exit_price = bar["close"]
        trade.exit_reason = "eod"
        trade.exit_ts = bar["ts"]
        
        return PartialExit(
            stage=ExitStage.CLOSED,
            ts=bar["ts"],
            price=bar["close"],
            pct_closed=exit_pct,
            pnl_r=exit_r
        )


class MetricsCalculator:
    """Calculates trade metrics for comparison."""
    
    @staticmethod
    def calculate(trades: list[dict]) -> dict:
        """Calculate comprehensive trade metrics.
        
        Args:
            trades: List of completed trade dicts with 'pnl_r' field
        
        Returns:
            Dict of metrics
        """
        if not trades:
            return {}
        
        pnls = [t.get("pnl_r", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        # Basic metrics
        total = len(pnls)
        win_rate = len(wins) / total * 100 if total > 0 else 0
        
        # PnL metrics
        total_r = sum(pnls)
        avg_r = total_r / total if total > 0 else 0
        med_r = sorted(pnls)[len(pnls)//2] if total > 0 else 0
        
        # Risk metrics
        std = (sum((p - avg_r)**2 for p in pnls) / total)**0.5 if total > 0 else 0
        
        # Expectancy
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        win_pct = len(wins) / total if total > 0 else 0
        expectancy = (win_pct * avg_win) + ((1 - win_pct) * avg_loss) if total > 0 else 0
        
        # Skew
        if std > 0:
            skew = sum((p - avg_r)**3 for p in pnls) / (total * std**3) if total > 0 else 0
        else:
            skew = 0
        
        # Top 3 contribution
        sorted_pnls = sorted(pnls, reverse=True)
        top3 = sum(sorted_pnls[:3])
        top3_pct = (top3 / total_r * 100) if total_r != 0 else 0
        
        # Equity smoothness (coefficient of variation)
        cv = abs(std / avg_r) if avg_r != 0 else 0
        
        # Max drawdown
        peak = 0
        dd = 0
        max_dd = 0
        for p in pnls:
            peak = max(peak, p)
            dd = min(dd, p - peak)
            max_dd = min(max_dd, dd)
        
        return {
            "total_trades": total,
            "win_rate_pct": round(win_rate, 1),
            "total_r": round(total_r, 2),
            "avg_r": round(avg_r, 3),
            "median_r": round(med_r, 3),
            "std_r": round(std, 3),
            "expectancy": round(expectancy, 3),
            "skew": round(skew, 3),
            "top3_contribution_pct": round(top3_pct, 1),
            "equity_smoothness_cv": round(cv, 3),
            "max_drawdown_r": round(max_dd, 2),
            "avg_win_r": round(avg_win, 3),
            "avg_loss_r": round(avg_loss, 3),
            "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses else float('inf'),
        }


def compare_strategies(
    full_runner_trades: list[dict],
    partial_trades: list[dict],
) -> dict:
    """Compare two strategies and return difference metrics.
    
    Args:
        full_runner_trades: Trades from full-runner strategy
        partial_trades: Trades from partial-profit strategy
    
    Returns:
        Comparison dict
    """
    full_metrics = MetricsCalculator.calculate(full_runner_trades)
    partial_metrics = MetricsCalculator.calculate(partial_trades)
    
    comparison = {
        "full_runner": full_metrics,
        "partial_profit": partial_metrics,
        "differences": {},
    }
    
    # Calculate differences
    for key in full_metrics:
        if key in partial_metrics:
            full_val = full_metrics[key]
            partial_val = partial_metrics[key]
            if isinstance(full_val, (int, float)) and isinstance(partial_val, (int, float)):
                diff = partial_val - full_val
                pct_diff = (diff / abs(full_val) * 100) if full_val != 0 else 0
                comparison["differences"][key] = {
                    "absolute": round(diff, 3),
                    "pct": round(pct_diff, 1),
                }
    
    return comparison
