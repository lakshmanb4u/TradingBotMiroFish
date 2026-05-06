"""
Absorption detection - identifies where large orders are being absorbed.
"""

import logging
from typing import Optional, List, Dict
from collections import deque
from datetime import datetime, timedelta
import numpy as np

from data_types import (
    OrderFlowEvent, BarData, OrderSide, AbsorptionSignal
)

logger = logging.getLogger(__name__)


class AbsorptionDetector:
    """Detects order absorption patterns in orderflow."""
    
    def __init__(self, time_window_ms: int = 2000, 
                 delta_min_ratio: float = 0.5,
                 volume_min_pct: float = 0.3):
        """
        Initialize absorption detector.
        
        Args:
            time_window_ms: Window for aggregating trades
            delta_min_ratio: Minimum ratio of delta to absorbed volume
            volume_min_pct: Minimum % of bar volume to qualify
        """
        self.time_window_ms = time_window_ms
        self.delta_min_ratio = delta_min_ratio
        self.volume_min_pct = volume_min_pct
        
        # Event buffers by symbol
        self.event_buffers: Dict[str, deque] = {}
        self.last_bar: Dict[str, BarData] = {}
    
    def update_events(self, events: List[OrderFlowEvent], symbol: str):
        """Update with new order flow events."""
        if symbol not in self.event_buffers:
            self.event_buffers[symbol] = deque(maxlen=10000)
        
        for event in events:
            self.event_buffers[symbol].append(event)
    
    def analyze_bar(self, bar: BarData) -> List[AbsorptionSignal]:
        """
        Analyze bar for absorption patterns.
        
        Returns:
            List of absorption signals detected.
        """
        self.last_bar[bar.symbol] = bar
        signals = []
        
        if bar.symbol not in self.event_buffers:
            return signals
        
        # Get events for this bar period (with tolerance)
        window_start = bar.timestamp - (bar.timestamp % 60)  # Bar start
        window_end = window_start + 60  # 1-minute bar
        
        bar_events = [
            e for e in self.event_buffers[bar.symbol]
            if window_start <= e.timestamp <= window_end
        ]
        
        if not bar_events:
            return signals
        
        # Analyze buy and sell side separately
        for side in [OrderSide.BUY, OrderSide.SELL]:
            signal = self._detect_side_absorption(bar, bar_events, side)
            if signal:
                signals.append(signal)
        
        return signals
    
    def _detect_side_absorption(self, bar: BarData, events: List[OrderFlowEvent],
                               side: OrderSide) -> Optional[AbsorptionSignal]:
        """
        Detect absorption on a specific side.
        
        Logic:
        1. Find large orders on the side
        2. Check if they were absorbed (followed by opposite trades)
        3. Calculate absorption ratio
        """
        
        # Filter events for this side
        side_events = [e for e in events if e.side == side and e.size >= 100]
        
        if not side_events:
            return None
        
        # Group by price level (within ±1 tick)
        price_groups: Dict[float, List[OrderFlowEvent]] = {}
        for event in side_events:
            rounded_price = round(event.price * 4) / 4  # Round to nearest 0.25
            if rounded_price not in price_groups:
                price_groups[rounded_price] = []
            price_groups[rounded_price].append(event)
        
        # Find price levels with absorption
        for price_level, level_events in price_groups.items():
            absorbed_volume = sum(e.size for e in level_events)
            
            # Get opposite side volume at this level (sign of absorption)
            opposite_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
            opposite_events = [
                e for e in events
                if e.side == opposite_side and abs(e.price - price_level) < 0.5
            ]
            opposite_volume = sum(e.size for e in opposite_events)
            
            if opposite_volume < absorbed_volume * 0.3:
                continue  # Not enough opposite volume for absorption
            
            # Calculate metrics
            bar_total_volume = bar.volume if bar.volume > 0 else 1
            absorption_ratio = absorbed_volume / bar_total_volume
            
            # Calculate delta (buy - sell)
            buy_vol = sum(e.size for e in level_events if e.side == OrderSide.BUY)
            sell_vol = sum(e.size for e in level_events if e.side == OrderSide.SELL)
            delta = buy_vol - sell_vol
            
            # Check if this meets absorption criteria
            if absorption_ratio < self.volume_min_pct:
                continue
            
            if delta == 0:
                continue
            
            delta_ratio = abs(delta) / absorbed_volume if absorbed_volume > 0 else 0
            if delta_ratio < self.delta_min_ratio:
                continue
            
            # Confidence based on ratio
            confidence = min(absorption_ratio * (delta_ratio / self.delta_min_ratio), 1.0)
            
            return AbsorptionSignal(
                bar=bar,
                side=side,
                absorbed_volume=absorbed_volume,
                absorption_orders=level_events,
                ratio=absorption_ratio,
                confidence=confidence,
                timestamp=bar.timestamp
            )
        
        return None
    
    def detect_accumulation_zones(self, bar: BarData, lookback_bars: int = 10) -> List[Dict]:
        """
        Detect accumulation zones (repeated absorption).
        
        Returns:
            List of accumulation zones with price and volume info.
        """
        zones = []
        
        if bar.symbol not in self.event_buffers:
            return zones
        
        # Group events by price level
        price_levels: Dict[float, float] = {}
        for event in self.event_buffers[bar.symbol]:
            rounded_price = round(event.price * 4) / 4
            if rounded_price not in price_levels:
                price_levels[rounded_price] = 0
            price_levels[rounded_price] += event.size
        
        # Find high-volume price levels
        if price_levels:
            median_volume = np.median(list(price_levels.values()))
            threshold = median_volume * 1.5
            
            for price, volume in price_levels.items():
                if volume > threshold:
                    zones.append({
                        "price": price,
                        "volume": volume,
                        "type": "accumulation" if bar.close > price else "distribution"
                    })
        
        return sorted(zones, key=lambda z: z["volume"], reverse=True)[:3]
    
    def clear_old_events(self, symbol: str, max_age_seconds: int = 300):
        """Clear events older than max_age_seconds."""
        if symbol not in self.event_buffers:
            return
        
        current_time = datetime.now().timestamp()
        cutoff = current_time - max_age_seconds
        
        # Remove old events
        events_to_keep = [
            e for e in self.event_buffers[symbol]
            if e.timestamp > cutoff
        ]
        
        self.event_buffers[symbol] = deque(events_to_keep, maxlen=10000)
