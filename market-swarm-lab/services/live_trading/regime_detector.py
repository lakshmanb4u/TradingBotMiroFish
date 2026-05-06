"""
Market regime detection using moving averages and volatility analysis.
"""

import logging
from typing import Optional, List, Deque
from collections import deque
from dataclasses import dataclass
import numpy as np

from data_types import BarData, RegimeType, RegimeState

logger = logging.getLogger(__name__)


@dataclass
class RegimeMetrics:
    """Metrics for regime detection."""
    short_ma: float  # Short-term moving average
    long_ma: float   # Long-term moving average
    atr: float       # Average True Range (volatility)
    slope: float     # Trend slope


class RegimeDetector:
    """Detects market regime (uptrend, downtrend, range, breakout)."""
    
    def __init__(self, ma_short: int = 5, ma_long: int = 20, atr_period: int = 14,
                 volatility_threshold: float = 0.02):
        self.ma_short_period = ma_short
        self.ma_long_period = ma_long
        self.atr_period = atr_period
        self.volatility_threshold = volatility_threshold
        
        # Buffers for calculations
        self.price_buffer: Deque[float] = deque(maxlen=ma_long + 10)
        self.high_buffer: Deque[float] = deque(maxlen=atr_period + 10)
        self.low_buffer: Deque[float] = deque(maxlen=atr_period + 10)
        self.tr_buffer: Deque[float] = deque(maxlen=atr_period + 10)
        
        self.last_close: Optional[float] = None
        self.last_regime: Optional[RegimeState] = None
    
    def update(self, bar: BarData) -> Optional[RegimeState]:
        """
        Update regime detector with new bar data.
        
        Returns:
            RegimeState if regime is determined, None otherwise.
        """
        self.price_buffer.append(bar.close)
        self.high_buffer.append(bar.high)
        self.low_buffer.append(bar.low)
        
        # Calculate True Range
        if self.last_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self.last_close),
                abs(bar.low - self.last_close)
            )
        else:
            tr = bar.high - bar.low
        
        self.tr_buffer.append(tr)
        self.last_close = bar.close
        
        # Need minimum data to calculate
        if len(self.price_buffer) < self.ma_long_period:
            return None
        
        # Calculate metrics
        metrics = self._calculate_metrics()
        
        # Detect regime
        regime = self._detect_regime(bar, metrics)
        self.last_regime = regime
        
        return regime
    
    def _calculate_metrics(self) -> RegimeMetrics:
        """Calculate regime metrics."""
        prices = list(self.price_buffer)
        
        short_ma = np.mean(prices[-self.ma_short_period:])
        long_ma = np.mean(prices[-self.ma_long_period:])
        
        # Calculate ATR
        if len(self.tr_buffer) >= self.atr_period:
            atr = np.mean(list(self.tr_buffer)[-self.atr_period:])
        else:
            atr = np.mean(list(self.tr_buffer)) if self.tr_buffer else 0.0
        
        # Calculate trend slope
        if len(prices) >= 2:
            recent_prices = prices[-10:]
            x = np.arange(len(recent_prices))
            y = np.array(recent_prices)
            slope = np.polyfit(x, y, 1)[0]
        else:
            slope = 0.0
        
        return RegimeMetrics(
            short_ma=short_ma,
            long_ma=long_ma,
            atr=atr,
            slope=slope
        )
    
    def _detect_regime(self, bar: BarData, metrics: RegimeMetrics) -> RegimeState:
        """
        Detect market regime based on metrics.
        
        Regimes:
        - UPTREND: Short MA > Long MA, positive slope
        - DOWNTREND: Short MA < Long MA, negative slope
        - RANGE: Short MA ≈ Long MA
        - BREAKOUT: High volatility with price above resistance
        - BREAKDOWN: High volatility with price below support
        """
        
        current_price = bar.close
        short_ma = metrics.short_ma
        long_ma = metrics.long_ma
        atr = metrics.atr
        slope = metrics.slope
        
        # Calculate volatility
        volatility = atr / current_price if current_price > 0 else 0.0
        
        # Find support/resistance
        support = min(list(self.low_buffer)[-20:]) if len(self.low_buffer) >= 20 else bar.low
        resistance = max(list(self.high_buffer)[-20:]) if len(self.high_buffer) >= 20 else bar.high
        
        # Trend strength (0-1)
        ma_distance = abs(short_ma - long_ma) / long_ma if long_ma > 0 else 0
        trend_strength = min(ma_distance * 10, 1.0)
        
        # Determine regime type
        if volatility > self.volatility_threshold:
            if slope > 0 and current_price > resistance:
                regime_type = RegimeType.BREAKOUT
            elif slope < 0 and current_price < support:
                regime_type = RegimeType.BREAKDOWN
            else:
                regime_type = RegimeType.RANGE
        else:
            if short_ma > long_ma and slope > 0:
                regime_type = RegimeType.UPTREND
            elif short_ma < long_ma and slope < 0:
                regime_type = RegimeType.DOWNTREND
            else:
                regime_type = RegimeType.RANGE
        
        return RegimeState(
            regime_type=regime_type,
            trend_strength=trend_strength,
            volatility=volatility,
            support_price=support,
            resistance_price=resistance,
            timestamp=bar.timestamp
        )
    
    def get_current_regime(self) -> Optional[RegimeState]:
        """Get current regime state."""
        return self.last_regime
    
    def is_trending(self) -> bool:
        """Check if market is trending."""
        if not self.last_regime:
            return False
        return self.last_regime.regime_type in (
            RegimeType.UPTREND,
            RegimeType.DOWNTREND,
            RegimeType.BREAKOUT,
            RegimeType.BREAKDOWN
        )
    
    def is_high_volatility(self) -> bool:
        """Check if volatility is high."""
        if not self.last_regime:
            return False
        return self.last_regime.volatility > self.volatility_threshold
