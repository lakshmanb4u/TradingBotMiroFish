"""
Safe delta calculation engine for orderflow.
Implements Blocker #5: Safe delta engine with valid-trade-only logic.

Rules:
- Only count valid (normalized, non-duplicated) trades
- Track cumulative delta per symbol
- Calculate delta acceleration (rate of change)
- Aggression (ratio of buy to total volume)
- Imbalance detection
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from datetime import datetime
from collections import deque
import statistics

logger = logging.getLogger(__name__)


@dataclass
class DeltaSnapshot:
    """Snapshot of delta state at a point in time."""
    timestamp: float
    cumulative_delta: float
    buy_volume: float
    sell_volume: float
    total_volume: float
    aggression_ratio: float  # buy_volume / total_volume
    price: Optional[float] = None
    
    @property
    def is_bullish(self) -> bool:
        """Simple bullish bias: more buy volume."""
        return self.buy_volume > self.sell_volume
    
    @property
    def imbalance_ratio(self) -> float:
        """Buy to sell ratio (avoid division by zero)."""
        if self.sell_volume == 0:
            return float('inf') if self.buy_volume > 0 else 1.0
        return self.buy_volume / self.sell_volume


@dataclass
class DeltaAcceleration:
    """Delta acceleration (rate of change)."""
    timestamp: float
    window_size: int  # Number of deltas in window
    delta_change: float  # Change in cumulative delta
    aggression_change: float  # Change in aggression ratio
    acceleration: float  # Delta change rate
    
    def is_accelerating_buy(self, threshold: float = 0.1) -> bool:
        """Check if buying is accelerating."""
        return self.acceleration > threshold
    
    def is_accelerating_sell(self, threshold: float = 0.1) -> bool:
        """Check if selling is accelerating."""
        return self.acceleration < -threshold


class SafeDeltaEngine:
    """Calculates delta safely, only from valid trades."""
    
    def __init__(self):
        """Initialize delta engine."""
        # Per-symbol state
        self.cumulative_delta: Dict[str, float] = {}
        self.total_buy_volume: Dict[str, float] = {}
        self.total_sell_volume: Dict[str, float] = {}
        
        # History for trend analysis
        self.delta_history: Dict[str, deque] = {}  # symbol -> deque of DeltaSnapshot
        self.max_history = 10000
        
        # Statistics
        self.total_valid_trades = 0
        self.total_invalid_trades_skipped = 0
        self.total_duplicates_skipped = 0
    
    def process_trade(self,
                     symbol: str,
                     side: str,  # BUY or SELL
                     size: float,
                     price: Optional[float] = None,
                     is_valid: bool = True,
                     is_duplicate: bool = False) -> Dict:
        """Process a single trade.
        
        Args:
            symbol: Symbol
            side: BUY or SELL
            size: Trade size
            price: Trade price (optional)
            is_valid: Whether trade passed normalization
            is_duplicate: Whether trade is a duplicate
            
        Returns:
            Dictionary with processing result
        """
        result = {
            'symbol': symbol,
            'side': side,
            'size': size,
            'price': price,
            'processed': False,
            'reason': None,
            'delta_contribution': 0,
        }
        
        # Skip invalid trades
        if not is_valid:
            self.total_invalid_trades_skipped += 1
            result['reason'] = 'INVALID_TRADE'
            logger.debug(f"[{symbol}] Skipping invalid trade: {side} {size} @ {price}")
            return result
        
        # Skip duplicates
        if is_duplicate:
            self.total_duplicates_skipped += 1
            result['reason'] = 'DUPLICATE'
            logger.debug(f"[{symbol}] Skipping duplicate trade: {side} {size} @ {price}")
            return result
        
        # Initialize symbol if needed
        if symbol not in self.cumulative_delta:
            self.cumulative_delta[symbol] = 0
            self.total_buy_volume[symbol] = 0
            self.total_sell_volume[symbol] = 0
            self.delta_history[symbol] = deque(maxlen=self.max_history)
        
        # Update totals
        if side.upper() == 'BUY':
            self.total_buy_volume[symbol] += size
            delta_contribution = size
        else:
            self.total_sell_volume[symbol] += size
            delta_contribution = -size
        
        # Update cumulative delta
        self.cumulative_delta[symbol] += delta_contribution
        self.total_valid_trades += 1
        
        result['processed'] = True
        result['delta_contribution'] = delta_contribution
        result['new_cumulative_delta'] = self.cumulative_delta[symbol]
        
        return result
    
    def get_delta_snapshot(self, symbol: str) -> Optional[DeltaSnapshot]:
        """Get current delta state for a symbol.
        
        Args:
            symbol: Symbol
            
        Returns:
            DeltaSnapshot with current state
        """
        if symbol not in self.cumulative_delta:
            return None
        
        buy_vol = self.total_buy_volume[symbol]
        sell_vol = self.total_sell_volume[symbol]
        total_vol = buy_vol + sell_vol
        
        if total_vol == 0:
            aggression = 0.5  # Neutral
        else:
            aggression = buy_vol / total_vol
        
        snapshot = DeltaSnapshot(
            timestamp=datetime.now().timestamp(),
            cumulative_delta=self.cumulative_delta[symbol],
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            total_volume=total_vol,
            aggression_ratio=aggression
        )
        
        # Store in history
        self.delta_history[symbol].append(snapshot)
        
        return snapshot
    
    def calculate_acceleration(self, 
                              symbol: str,
                              window_size: int = 20) -> Optional[DeltaAcceleration]:
        """Calculate delta acceleration over a window.
        
        Args:
            symbol: Symbol
            window_size: Number of snapshots to use for window
            
        Returns:
            DeltaAcceleration with acceleration metrics
        """
        if symbol not in self.delta_history:
            return None
        
        history = self.delta_history[symbol]
        if len(history) < 2:
            return None
        
        # Get window
        window = list(history)[-window_size:] if len(history) >= window_size else list(history)
        
        if len(window) < 2:
            return None
        
        # Calculate change
        first = window[0]
        last = window[-1]
        
        delta_change = last.cumulative_delta - first.cumulative_delta
        aggression_change = last.aggression_ratio - first.aggression_ratio
        time_delta_sec = last.timestamp - first.timestamp
        
        if time_delta_sec > 0:
            acceleration = delta_change / time_delta_sec
        else:
            acceleration = 0
        
        return DeltaAcceleration(
            timestamp=last.timestamp,
            window_size=len(window),
            delta_change=delta_change,
            aggression_change=aggression_change,
            acceleration=acceleration
        )
    
    def calculate_imbalance_score(self, symbol: str, window_size: int = 50) -> Optional[dict]:
        """Calculate imbalance detection score.
        
        Args:
            symbol: Symbol
            window_size: Number of trades to analyze
            
        Returns:
            Dictionary with imbalance metrics
        """
        if symbol not in self.delta_history:
            return None
        
        history = self.delta_history[symbol]
        if len(history) < 2:
            return None
        
        # Get window of snapshots
        window = list(history)[-window_size:] if len(history) >= window_size else list(history)
        
        # Calculate imbalance metrics
        deltas = [s.cumulative_delta for s in window]
        aggression_ratios = [s.aggression_ratio for s in window]
        
        if len(deltas) < 2:
            return None
        
        avg_delta = statistics.mean(deltas)
        std_delta = statistics.stdev(deltas) if len(deltas) > 1 else 0
        
        avg_aggression = statistics.mean(aggression_ratios)
        
        # Current deviation from average
        current_delta = deltas[-1]
        delta_zscore = (current_delta - avg_delta) / (std_delta + 0.001)
        
        # Imbalance severity
        imbalance_severity = abs(delta_zscore)
        
        return {
            'avg_delta': avg_delta,
            'std_delta': std_delta,
            'current_delta': current_delta,
            'delta_zscore': delta_zscore,
            'imbalance_severity': imbalance_severity,
            'avg_aggression': avg_aggression,
            'current_aggression': aggression_ratios[-1],
            'is_extreme_imbalance': imbalance_severity > 2.0,
            'window_size': len(window),
        }
    
    def get_delta_state(self, symbol: str) -> Optional[dict]:
        """Get complete delta state for a symbol.
        
        Args:
            symbol: Symbol
            
        Returns:
            Dictionary with delta state
        """
        if symbol not in self.cumulative_delta:
            return None
        
        snapshot = self.get_delta_snapshot(symbol)
        acceleration = self.calculate_acceleration(symbol)
        imbalance = self.calculate_imbalance_score(symbol)
        
        return {
            'snapshot': snapshot,
            'acceleration': acceleration,
            'imbalance': imbalance,
        }
    
    def get_stats(self) -> dict:
        """Get engine statistics."""
        return {
            'total_valid_trades': self.total_valid_trades,
            'total_invalid_skipped': self.total_invalid_trades_skipped,
            'total_duplicates_skipped': self.total_duplicates_skipped,
            'symbols_tracked': len(self.cumulative_delta),
            'total_history_entries': sum(len(h) for h in self.delta_history.values()),
        }
    
    def get_symbol_delta(self, symbol: str) -> Optional[float]:
        """Get cumulative delta for a symbol."""
        return self.cumulative_delta.get(symbol)
    
    def get_all_symbol_deltas(self) -> Dict[str, float]:
        """Get cumulative deltas for all symbols."""
        return dict(self.cumulative_delta)
    
    def reset_symbol(self, symbol: str):
        """Reset statistics for a symbol."""
        if symbol in self.cumulative_delta:
            del self.cumulative_delta[symbol]
        if symbol in self.total_buy_volume:
            del self.total_buy_volume[symbol]
        if symbol in self.total_sell_volume:
            del self.total_sell_volume[symbol]
        if symbol in self.delta_history:
            self.delta_history[symbol].clear()
