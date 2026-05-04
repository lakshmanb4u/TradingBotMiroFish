#!/usr/bin/env python3
"""
entry_exit_planner.py

Defines entry, stop, and target prices for REAL signals.
NO LOOKAHEAD - all rules use only information known at signal time.

Entry/Exit Rules (from Reddit orderflow logic):

BUY:
  entry = reclaim price (or next tick confirmation)
  stop = absorption low - buffer (typically 2 ticks + volatility)
  target_1 = entry + 1R (where R = stop distance)
  target_2 = entry + 2R (or next structural resistance)

SELL:
  entry = rejection price (or next tick confirmation)  
  stop = absorption high + buffer (typically 2 ticks + volatility)
  target_1 = entry - 1R (where R = stop distance)
  target_2 = entry - 2R (or next structural support)

Realistic Modeling:
  - Slippage: 1-2 ticks on market orders
  - Spread: 0.25-0.50 points (ES typical)
  - Commission: $2-5 per round-trip
  - Stop priority: If stop AND target hit in same window, stop executes first
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import numpy as np


ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


@dataclass
class EntryExitPlan:
    """Complete entry/exit plan for a signal at signal time (no lookahead)."""
    signal_timestamp_utc: str
    direction: str                  # LONG | SHORT
    
    entry_price: float              # Where we would enter
    entry_slippage: float           # Expected slippage (2 ticks typical)
    entry_filled_price: float       # Actual entry with slippage
    
    stop_price: float               # Stop loss
    stop_slippage: float            # Stop typically fills WORSE (3 ticks)
    stop_filled_price: float        # Stop with slippage
    
    target_1_price: float           # First target
    target_1_slippage: float        # Target typically fills BETTER (1 tick)
    target_1_filled_price: float
    
    target_2_price: float           # Second target
    target_2_slippage: float
    target_2_filled_price: float
    
    risk_ticks: float               # Ticks at risk (entry to stop)
    target_1_reward_ticks: float    # Ticks for target 1
    target_2_reward_ticks: float    # Ticks for target 2
    
    rr_ratio_1: float               # Target 1 risk-reward ratio
    rr_ratio_2: float               # Target 2 risk-reward ratio
    
    volatility_basis: float         # Volatility used for stop/target calc
    spread_cost: float              # Spread cost (per entry/exit)
    commission_per_rt: float        # Commission per round-trip


class EntryExitPlanner:
    """
    Plans entry, stop, and target prices for real signals.
    Uses only information available at signal time (no lookahead).
    """
    
    def __init__(self, 
                 tick_size: float = ES_TICK_SIZE,
                 slippage_entry_ticks: int = 2,
                 slippage_stop_ticks: int = 3,
                 slippage_target_ticks: int = 1,
                 spread_ticks: int = 1,
                 commission_per_rt: float = 3.0):
        """
        Initialize planner with realistic modeling parameters.
        
        Args:
            tick_size: ES=0.25, NQ=0.50
            slippage_entry_ticks: Market order slippage on entry
            slippage_stop_ticks: Stop loss typically fills WORSE
            slippage_target_ticks: Target typically fills BETTER (bid/ask favor)
            spread_ticks: Bid-ask spread cost
            commission_per_rt: Dollar commission per round-trip
        """
        self.tick_size = tick_size
        self.slippage_entry = slippage_entry_ticks * tick_size
        self.slippage_stop = slippage_stop_ticks * tick_size
        self.slippage_target = slippage_target_ticks * tick_size
        self.spread = spread_ticks * tick_size
        self.commission_per_rt = commission_per_rt
    
    def calculate_volatility(self, recent_prices: List[float]) -> float:
        """
        Calculate volatility from BEFORE signal (no lookahead).
        
        Used to determine stop/target distances.
        
        Args:
            recent_prices: Prices before signal (for context)
        
        Returns:
            Standard deviation of prices (volatility measure)
        """
        if not recent_prices or len(recent_prices) < 2:
            return self.tick_size * 2  # Default: 2 ticks
        
        prices_array = np.array(recent_prices, dtype=float)
        return np.std(prices_array)
    
    def plan_long_entry(self,
                       entry_price: float,
                       volatility: float,
                       absorption_low: float,
                       absorption_high: float) -> EntryExitPlan:
        """
        Plan entry/exit for LONG signal.
        
        Entry rules:
        - Entry at reclaim price (entry_price)
        - Stop = absorption_low - buffer (2 ticks + vol)
        - Target1 = entry + 1R (R = entry - stop)
        - Target2 = entry + 2R
        
        Args:
            entry_price: Reclaim price (entry)
            volatility: Market volatility (standard deviation)
            absorption_low: Lowest price during absorption
            absorption_high: Highest price during absorption
        
        Returns:
            EntryExitPlan with all fills modeled
        """
        # Determine stop distance (no lookahead - use pre-signal vol + absorption)
        min_stop_distance = self.tick_size * 2
        vol_based_stop = volatility * 1.0
        stop_distance = max(min_stop_distance, vol_based_stop)
        
        stop_price = absorption_low - stop_distance
        
        # Calculate R (risk per trade)
        r_value = entry_price - stop_price
        
        # Targets
        target_1_price = entry_price + r_value
        target_2_price = entry_price + (r_value * 2)
        
        # Model slippage and spread
        entry_filled = entry_price - self.slippage_entry - self.spread
        stop_filled = stop_price - self.slippage_stop - self.spread
        target_1_filled = target_1_price + self.slippage_target - self.spread
        target_2_filled = target_2_price + self.slippage_target - self.spread
        
        # Calculate risk/reward ratios
        actual_risk = entry_filled - stop_filled
        rr_1 = (target_1_filled - entry_filled) / actual_risk if actual_risk > 0 else 0
        rr_2 = (target_2_filled - entry_filled) / actual_risk if actual_risk > 0 else 0
        
        return EntryExitPlan(
            signal_timestamp_utc=datetime.now().isoformat(),  # Updated by caller
            direction="LONG",
            entry_price=entry_price,
            entry_slippage=self.slippage_entry,
            entry_filled_price=entry_filled,
            stop_price=stop_price,
            stop_slippage=self.slippage_stop,
            stop_filled_price=stop_filled,
            target_1_price=target_1_price,
            target_1_slippage=self.slippage_target,
            target_1_filled_price=target_1_filled,
            target_2_price=target_2_price,
            target_2_slippage=self.slippage_target,
            target_2_filled_price=target_2_filled,
            risk_ticks=r_value / self.tick_size,
            target_1_reward_ticks=(target_1_price - entry_price) / self.tick_size,
            target_2_reward_ticks=(target_2_price - entry_price) / self.tick_size,
            rr_ratio_1=rr_1,
            rr_ratio_2=rr_2,
            volatility_basis=volatility,
            spread_cost=self.spread,
            commission_per_rt=self.commission_per_rt,
        )
    
    def plan_short_entry(self,
                        entry_price: float,
                        volatility: float,
                        absorption_low: float,
                        absorption_high: float) -> EntryExitPlan:
        """
        Plan entry/exit for SHORT signal.
        
        Entry rules:
        - Entry at rejection price (entry_price)
        - Stop = absorption_high + buffer (2 ticks + vol)
        - Target1 = entry - 1R (R = stop - entry)
        - Target2 = entry - 2R
        
        Args:
            entry_price: Rejection price (entry)
            volatility: Market volatility
            absorption_low: Lowest price during absorption
            absorption_high: Highest price during absorption
        
        Returns:
            EntryExitPlan with all fills modeled
        """
        # Determine stop distance
        min_stop_distance = self.tick_size * 2
        vol_based_stop = volatility * 1.0
        stop_distance = max(min_stop_distance, vol_based_stop)
        
        stop_price = absorption_high + stop_distance
        
        # Calculate R (risk per trade)
        r_value = stop_price - entry_price
        
        # Targets
        target_1_price = entry_price - r_value
        target_2_price = entry_price - (r_value * 2)
        
        # Model slippage and spread
        entry_filled = entry_price + self.slippage_entry + self.spread
        stop_filled = stop_price + self.slippage_stop + self.spread
        target_1_filled = target_1_price - self.slippage_target + self.spread
        target_2_filled = target_2_price - self.slippage_target + self.spread
        
        # Calculate risk/reward ratios
        actual_risk = stop_filled - entry_filled
        rr_1 = (entry_filled - target_1_filled) / actual_risk if actual_risk > 0 else 0
        rr_2 = (entry_filled - target_2_filled) / actual_risk if actual_risk > 0 else 0
        
        return EntryExitPlan(
            signal_timestamp_utc=datetime.now().isoformat(),  # Updated by caller
            direction="SHORT",
            entry_price=entry_price,
            entry_slippage=self.slippage_entry,
            entry_filled_price=entry_filled,
            stop_price=stop_price,
            stop_slippage=self.slippage_stop,
            stop_filled_price=stop_filled,
            target_1_price=target_1_price,
            target_1_slippage=self.slippage_target,
            target_1_filled_price=target_1_filled,
            target_2_price=target_2_price,
            target_2_slippage=self.slippage_target,
            target_2_filled_price=target_2_filled,
            risk_ticks=r_value / self.tick_size,
            target_1_reward_ticks=(entry_price - target_1_price) / self.tick_size,
            target_2_reward_ticks=(entry_price - target_2_price) / self.tick_size,
            rr_ratio_1=rr_1,
            rr_ratio_2=rr_2,
            volatility_basis=volatility,
            spread_cost=self.spread,
            commission_per_rt=self.commission_per_rt,
        )
    
    def plan_entry(self,
                  direction: str,
                  entry_price: float,
                  volatility: float,
                  absorption_low: float,
                  absorption_high: float) -> EntryExitPlan:
        """
        Universal plan_entry that delegates to LONG or SHORT.
        
        Args:
            direction: "LONG" or "SHORT"
            entry_price: Entry level
            volatility: Volatility for stop calc
            absorption_low: Low end of absorption zone
            absorption_high: High end of absorption zone
        
        Returns:
            EntryExitPlan
        """
        if direction.upper() == "LONG":
            return self.plan_long_entry(entry_price, volatility, absorption_low, absorption_high)
        else:
            return self.plan_short_entry(entry_price, volatility, absorption_low, absorption_high)


if __name__ == "__main__":
    # Test
    planner = EntryExitPlanner()
    
    # Example LONG
    print("LONG Entry/Exit Plan")
    print("=" * 50)
    recent_prices = [7226.5, 7226.75, 7227.0, 7226.75, 7226.5]
    vol = planner.calculate_volatility(recent_prices)
    
    plan = planner.plan_long_entry(
        entry_price=7227.0,
        volatility=vol,
        absorption_low=7226.0,
        absorption_high=7227.5
    )
    
    print(f"Entry: ${plan.entry_filled_price:.2f} (vs ${plan.entry_price:.2f})")
    print(f"Stop:  ${plan.stop_filled_price:.2f} (risk: {plan.risk_ticks:.1f} ticks)")
    print(f"T1:    ${plan.target_1_filled_price:.2f} (RR: {plan.rr_ratio_1:.2f})")
    print(f"T2:    ${plan.target_2_filled_price:.2f} (RR: {plan.rr_ratio_2:.2f})")
    
    print("\nSHORT Entry/Exit Plan")
    print("=" * 50)
    plan = planner.plan_short_entry(
        entry_price=7227.0,
        volatility=vol,
        absorption_low=7226.0,
        absorption_high=7227.5
    )
    
    print(f"Entry: ${plan.entry_filled_price:.2f} (vs ${plan.entry_price:.2f})")
    print(f"Stop:  ${plan.stop_filled_price:.2f} (risk: {plan.risk_ticks:.1f} ticks)")
    print(f"T1:    ${plan.target_1_filled_price:.2f} (RR: {plan.rr_ratio_1:.2f})")
    print(f"T2:    ${plan.target_2_filled_price:.2f} (RR: {plan.rr_ratio_2:.2f})")
