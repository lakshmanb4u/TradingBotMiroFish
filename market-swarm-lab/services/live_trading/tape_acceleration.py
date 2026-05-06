"""
Tape Acceleration Detection Service

Detects aggressive market activity acceleration:
- Aggressive market order increase (% change in volume of market orders)
- Trade velocity increase (trades per second)
- Delta velocity increase (delta changing faster)
- Spread stability (asks for spread collapse/stability)
- Consecutive initiative prints (aggressive buys/sells in sequence)
- Accelerating tape after reclaim (volume surge post-reclaim)

Produces tape_acceleration_score (0-100) representing the likelihood
that the market is accelerating in the intended direction.
"""

import logging
from typing import Dict, List, Optional, Tuple
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field

from data_types import OrderFlowEvent, BarData, OrderSide

logger = logging.getLogger(__name__)


@dataclass
class TapeAccelerationMetrics:
    """Metrics for tape acceleration scoring."""
    market_order_acceleration: float = 0.0  # % change in market order volume
    trade_velocity: float = 0.0  # trades per second
    delta_velocity: float = 0.0  # abs(delta change) per second
    spread_stability_score: float = 0.0  # 0-1, higher = more stable/collapsed
    consecutive_initiative_count: int = 0  # consecutive prints on one side
    consecutive_initiative_strength: float = 0.0  # 0-1
    post_reclaim_acceleration: float = 0.0  # volume surge after reclaim
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class TapeAccelerationSignal:
    """Tape acceleration detection result."""
    symbol: str
    side: OrderSide
    tape_acceleration_score: float  # 0-100
    metrics: TapeAccelerationMetrics
    bar: BarData
    is_accelerating: bool  # True if score > threshold
    threshold_exceeded: bool  # confidence check
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def __hash__(self):
        return hash((self.bar.timestamp, self.symbol, self.side))


class TapeAccelerationDetector:
    """
    Detects tape acceleration by monitoring order flow characteristics.
    
    Core logic:
    1. Track market order volume trends within bars
    2. Calculate trade velocity (trades/sec)
    3. Monitor delta velocity (how fast delta is changing)
    4. Assess spread stability (tight/collapsed spreads indicate continuation)
    5. Count consecutive initiative prints (marker of aggression)
    6. Detect post-reclaim acceleration spikes
    
    Score computation:
    - Each metric contributes 0-100 points
    - Average across active metrics
    - Threshold: >60 = confident acceleration
    """
    
    def __init__(self,
                 market_order_accel_threshold: float = 0.5,  # 50% increase
                 min_trade_velocity: float = 2.0,  # trades/sec
                 spread_collapse_threshold: float = 0.3,  # 30% of bar range
                 min_consecutive_prints: int = 3,
                 post_reclaim_window_ms: int = 1000,
                 acceleration_score_threshold: float = 60.0):
        """
        Initialize tape acceleration detector.
        
        Args:
            market_order_accel_threshold: % increase in market order volume
            min_trade_velocity: minimum trades/second to consider active
            spread_collapse_threshold: spread as % of bar range (lower = more collapsed)
            min_consecutive_prints: min consecutive prints to signal aggression
            post_reclaim_window_ms: window to look for post-reclaim acceleration
            acceleration_score_threshold: score threshold for confident signal (0-100)
        """
        self.market_order_accel_threshold = market_order_accel_threshold
        self.min_trade_velocity = min_trade_velocity
        self.spread_collapse_threshold = spread_collapse_threshold
        self.min_consecutive_prints = min_consecutive_prints
        self.post_reclaim_window_ms = post_reclaim_window_ms
        self.acceleration_score_threshold = acceleration_score_threshold
        
        # Per-symbol tracking
        self.event_history: Dict[str, deque] = {}  # symbol -> deque of events
        self.market_order_history: Dict[str, deque] = {}  # symbol -> deque of (ts, volume)
        self.delta_history: Dict[str, deque] = {}  # symbol -> deque of (ts, delta)
        self.consecutive_side: Dict[str, OrderSide] = {}  # symbol -> last initiative side
        self.consecutive_count: Dict[str, int] = {}  # symbol -> consecutive count
        
        # Configuration
        self.max_history_events = 500
        self.max_history_points = 100
    
    def update_events(self, events: List[OrderFlowEvent], symbol: str):
        """
        Update detector with incoming events.
        
        Args:
            events: List of order flow events
            symbol: Symbol being tracked
        """
        if symbol not in self.event_history:
            self.event_history[symbol] = deque(maxlen=self.max_history_events)
            self.market_order_history[symbol] = deque(maxlen=self.max_history_points)
            self.delta_history[symbol] = deque(maxlen=self.max_history_points)
            self.consecutive_side[symbol] = None
            self.consecutive_count[symbol] = 0
        
        # Add events
        for event in events:
            self.event_history[symbol].append(event)
    
    def analyze_bar(self, bar: BarData) -> Optional[TapeAccelerationSignal]:
        """
        Analyze bar for tape acceleration.
        
        Returns:
            TapeAccelerationSignal if detected, None otherwise
        """
        symbol = bar.symbol
        
        if symbol not in self.event_history:
            return None
        
        events = list(self.event_history[symbol])
        if not events:
            return None
        
        # Filter events for this bar (rough heuristic: last N events)
        bar_duration = 60  # assume 1-min bars
        bar_start = bar.timestamp - bar_duration
        bar_events = [e for e in events if e.timestamp >= bar_start]
        
        if not bar_events:
            bar_events = events[-20:]  # fallback: last 20 events
        
        # Compute metrics
        metrics = self._compute_metrics(bar_events, bar, symbol)
        
        # Calculate tape acceleration score
        tape_acceleration_score = self._calculate_score(metrics)
        
        # Determine dominant side
        buy_volume = sum(e.size for e in bar_events if e.side == OrderSide.BUY)
        sell_volume = sum(e.size for e in bar_events if e.side == OrderSide.SELL)
        dominant_side = OrderSide.BUY if buy_volume >= sell_volume else OrderSide.SELL
        
        # Create signal
        signal = TapeAccelerationSignal(
            symbol=symbol,
            side=dominant_side,
            tape_acceleration_score=tape_acceleration_score,
            metrics=metrics,
            bar=bar,
            is_accelerating=tape_acceleration_score > self.acceleration_score_threshold,
            threshold_exceeded=tape_acceleration_score > 60.0,
            timestamp=bar.timestamp
        )
        
        logger.debug(
            f"Tape acceleration for {symbol}: score={tape_acceleration_score:.1f}, "
            f"is_accelerating={signal.is_accelerating}"
        )
        
        return signal
    
    def _compute_metrics(self, events: List[OrderFlowEvent], bar: BarData,
                        symbol: str) -> TapeAccelerationMetrics:
        """Compute individual acceleration metrics."""
        
        metrics = TapeAccelerationMetrics()
        
        if not events:
            return metrics
        
        # 1. Market order acceleration
        metrics.market_order_acceleration = self._compute_market_order_acceleration(
            events, bar, symbol
        )
        
        # 2. Trade velocity
        metrics.trade_velocity = self._compute_trade_velocity(events)
        
        # 3. Delta velocity
        metrics.delta_velocity = self._compute_delta_velocity(events, bar, symbol)
        
        # 4. Spread stability
        metrics.spread_stability_score = self._compute_spread_stability(bar, events)
        
        # 5. Consecutive initiative prints
        (metrics.consecutive_initiative_count,
         metrics.consecutive_initiative_strength) = self._compute_consecutive_initiative(
            events, symbol
        )
        
        # 6. Post-reclaim acceleration
        metrics.post_reclaim_acceleration = self._compute_post_reclaim_acceleration(
            events, bar
        )
        
        return metrics
    
    def _compute_market_order_acceleration(self, events: List[OrderFlowEvent],
                                         bar: BarData, symbol: str) -> float:
        """
        Compute market order acceleration.
        
        Returns: % change in market order volume (0-100, capped at 100)
        """
        market_orders = [e for e in events if e.is_market_order]
        if not market_orders:
            return 0.0
        
        current_market_vol = sum(e.size for e in market_orders)
        
        # Compare to average of prior bars (from history)
        if symbol in self.market_order_history:
            history = list(self.market_order_history[symbol])
            if history:
                avg_prior = sum(history) / len(history) if history else 0.0
                if avg_prior > 0:
                    accel = (current_market_vol - avg_prior) / avg_prior
                    # Normalize to 0-100
                    score = min(100.0, max(0.0, accel * 100))
                else:
                    score = 50.0 if current_market_vol > 0 else 0.0
            else:
                score = 50.0 if current_market_vol > 0 else 0.0
        else:
            score = 50.0 if current_market_vol > 0 else 0.0
        
        # Store for future comparison
        if symbol not in self.market_order_history:
            self.market_order_history[symbol] = deque(maxlen=self.max_history_points)
        self.market_order_history[symbol].append(current_market_vol)
        
        return score
    
    def _compute_trade_velocity(self, events: List[OrderFlowEvent]) -> float:
        """
        Compute trade velocity (trades per second).
        
        Returns: 0-100 score based on trades/sec vs minimum threshold
        """
        if not events:
            return 0.0
        
        time_span = events[-1].timestamp - events[0].timestamp
        if time_span <= 0:
            time_span = 0.1
        
        trades_per_sec = len(events) / time_span
        
        # Score: 0 if below threshold, 100 if 5x threshold
        if trades_per_sec < self.min_trade_velocity:
            score = (trades_per_sec / self.min_trade_velocity) * 50
        else:
            excess = trades_per_sec - self.min_trade_velocity
            max_excess = self.min_trade_velocity * 4
            score = 50 + (excess / max_excess) * 50
        
        return min(100.0, score)
    
    def _compute_delta_velocity(self, events: List[OrderFlowEvent],
                               bar: BarData, symbol: str) -> float:
        """
        Compute delta velocity (how fast cumulative delta is changing).
        
        Returns: 0-100 score
        """
        if not events or len(events) < 2:
            return 0.0
        
        # Calculate cumulative delta over time
        cumulative_delta = 0.0
        delta_changes = []
        
        for event in events:
            delta_change = event.size if event.side == OrderSide.BUY else -event.size
            cumulative_delta += delta_change
            delta_changes.append((event.timestamp, cumulative_delta))
        
        # Calculate rate of change
        time_span = events[-1].timestamp - events[0].timestamp
        if time_span <= 0:
            time_span = 0.1
        
        final_delta = cumulative_delta
        delta_velocity = abs(final_delta) / time_span
        
        # Store for comparison
        if symbol not in self.delta_history:
            self.delta_history[symbol] = deque(maxlen=self.max_history_points)
        self.delta_history[symbol].append((bar.timestamp, delta_velocity))
        
        # Score based on comparison to historical average
        if len(self.delta_history[symbol]) > 2:
            history = [v for ts, v in list(self.delta_history[symbol])[:-1]]
            avg_historical = sum(history) / len(history) if history else 0.0
            if avg_historical > 0:
                ratio = delta_velocity / avg_historical
                score = min(100.0, (ratio - 1.0) * 50 + 50)
            else:
                score = 50.0 if delta_velocity > 0 else 0.0
        else:
            score = 50.0 if delta_velocity > 0 else 0.0
        
        return max(0.0, min(100.0, score))
    
    def _compute_spread_stability(self, bar: BarData,
                                 events: List[OrderFlowEvent]) -> float:
        """
        Compute spread stability (collapsed/tight spreads indicate continuation).
        
        Returns: 0-100 score (higher = tighter/more stable spread)
        """
        if not bar.price_levels:
            # Use bid/ask volume as proxy
            total_vol = bar.bid_volume + bar.ask_volume
            if total_vol == 0:
                return 50.0
            
            # If bid_volume dominates, spread likely tight
            bid_ratio = bar.bid_volume / total_vol
            spread_score = min(100.0, abs(bid_ratio - 0.5) * 200)
            return spread_score
        
        # Analyze price level clustering
        bid_levels = sum(1 for p, pl in bar.price_levels.items() if pl.bid_volume > 0)
        ask_levels = sum(1 for p, pl in bar.price_levels.items() if pl.ask_volume > 0)
        
        if bid_levels + ask_levels == 0:
            return 50.0
        
        # Concentration score: fewer levels = tighter spread
        total_levels = bid_levels + ask_levels
        concentration = 1.0 - (total_levels / 20.0)  # normalize to 20 levels
        concentration = max(0.0, min(1.0, concentration))
        
        return concentration * 100.0
    
    def _compute_consecutive_initiative(self, events: List[OrderFlowEvent],
                                       symbol: str) -> Tuple[int, float]:
        """
        Compute consecutive initiative prints (aggressive buys/sells in sequence).
        
        Returns: (consecutive_count, strength_score)
        """
        if not events:
            return 0, 0.0
        
        # Identify aggressive orders (large market orders)
        size_threshold = sorted([e.size for e in events])
        if len(size_threshold) > 3:
            size_threshold = size_threshold[-int(len(size_threshold) * 0.25)]  # top 25%
        else:
            size_threshold = max([e.size for e in events]) * 0.8
        
        aggressive = [e for e in events if e.is_market_order and e.size >= size_threshold]
        if not aggressive:
            return 0, 0.0
        
        # Count consecutive prints on same side
        current_side = aggressive[0].side
        current_count = 1
        max_count = 1
        
        for order in aggressive[1:]:
            if order.side == current_side:
                current_count += 1
                max_count = max(max_count, current_count)
            else:
                current_side = order.side
                current_count = 1
        
        # Strength: ratio of consecutive to total aggressive
        strength = min(1.0, max_count / max(1, len(aggressive)))
        
        # Update tracker
        if max_count >= self.min_consecutive_prints:
            self.consecutive_side[symbol] = current_side
            self.consecutive_count[symbol] = max_count
        
        return max_count, strength
    
    def _compute_post_reclaim_acceleration(self, events: List[OrderFlowEvent],
                                          bar: BarData) -> float:
        """
        Compute acceleration after reclaim (volume surge post-reclaim).
        
        Heuristic: Check if more volume appears in second half of time window.
        Returns: 0-100 score
        """
        if not events:
            return 0.0
        
        mid_time = (events[0].timestamp + events[-1].timestamp) / 2.0
        
        first_half = [e for e in events if e.timestamp < mid_time]
        second_half = [e for e in events if e.timestamp >= mid_time]
        
        if not first_half or not second_half:
            return 0.0
        
        first_vol = sum(e.size for e in first_half)
        second_vol = sum(e.size for e in second_half)
        
        if first_vol == 0:
            return 50.0 if second_vol > 0 else 0.0
        
        accel = (second_vol - first_vol) / first_vol
        score = min(100.0, max(0.0, accel * 50 + 50))
        
        return score
    
    def _calculate_score(self, metrics: TapeAccelerationMetrics) -> float:
        """
        Calculate final tape acceleration score (0-100).
        
        Weighted average of component metrics:
        - Market order acceleration: 30%
        - Trade velocity: 20%
        - Delta velocity: 20%
        - Spread stability: 15%
        - Consecutive initiative: 10%
        - Post-reclaim acceleration: 5%
        """
        weights = {
            'market_order': (metrics.market_order_acceleration, 0.30),
            'velocity': (metrics.trade_velocity, 0.20),
            'delta_velocity': (metrics.delta_velocity, 0.20),
            'spread': (metrics.spread_stability_score, 0.15),
            'initiative': (metrics.consecutive_initiative_strength * 100, 0.10),
            'reclaim': (metrics.post_reclaim_acceleration, 0.05),
        }
        
        total_score = 0.0
        for metric_val, weight in weights.values():
            total_score += metric_val * weight
        
        return min(100.0, max(0.0, total_score))


def create_detector(config) -> TapeAccelerationDetector:
    """Factory function to create detector from config."""
    return TapeAccelerationDetector(
        market_order_accel_threshold=getattr(
            config, 'market_order_accel_threshold', 0.5
        ),
        min_trade_velocity=getattr(config, 'min_trade_velocity', 2.0),
        spread_collapse_threshold=getattr(config, 'spread_collapse_threshold', 0.3),
        min_consecutive_prints=getattr(config, 'min_consecutive_prints', 3),
        post_reclaim_window_ms=getattr(config, 'post_reclaim_window_ms', 1000),
        acceleration_score_threshold=getattr(config, 'acceleration_score_threshold', 60.0),
    )
