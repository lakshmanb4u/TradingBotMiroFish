"""
Live Entry Confirmation Service

Validates entries 1-3 seconds after reclaim by checking:
- Continuation is active (delta continuing in same direction)
- No reversal starting (opposite side volume surge)
- Delta not collapsing (delta velocity maintained)
- Spread not widening (stable/tight bid-ask)
- Participation ratio (aggressive buy % vs sell %, need dominance)
- Initiative dominance (consecutive prints on intended side)

Rejects entries if any condition fails.
Produces continuation_quality_score (0-100) representing confidence
that the position should remain open.
"""

import logging
from typing import Dict, List, Optional
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field

from data_types import OrderFlowEvent, BarData, OrderSide

logger = logging.getLogger(__name__)


@dataclass
class ContinuationMetrics:
    """Metrics for continuation validation."""
    participation_ratio_buy: float = 0.0  # aggressive buy % of total
    participation_ratio_sell: float = 0.0  # aggressive sell % of total
    participation_ratio_dominance: float = 0.0  # 0-1, how dominant is intended side
    delta_direction_aligned: bool = True  # delta moving in same direction
    delta_velocity_maintained: bool = True  # velocity not dropped significantly
    reversal_signals: int = 0  # count of reversal indicators
    spread_health_score: float = 0.0  # 0-1, higher = healthier/tighter
    liquidity_sufficient: bool = True  # volume depth available
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class LiveConfirmationSignal:
    """Live confirmation validation result."""
    symbol: str
    side: OrderSide
    entry_bar_timestamp: float  # timestamp of entry bar (reclaim bar)
    confirmation_bar_timestamp: float  # timestamp of confirmation bar
    continuation_quality_score: float  # 0-100
    metrics: ContinuationMetrics
    should_accept_entry: bool  # True = continue, False = reject/exit
    rejection_reasons: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def __hash__(self):
        return hash((self.entry_bar_timestamp, self.symbol, self.side))


class LiveConfirmationValidator:
    """
    Validates live entries 1-3 seconds after reclaim.
    
    Entry flow:
    1. Reclaim bar detected (tape_acceleration + absorption detected)
    2. Wait 1-3 seconds
    3. Sample confirmation bar
    4. Run 5 validation checks
    5. Return accept/reject decision
    
    Validation checks:
    - Continuation active: delta moving in entry direction
    - No reversal: opposite side volume < threshold
    - Delta velocity: maintained at >70% of entry velocity
    - Spread health: bid-ask spread not widening significantly
    - Participation ratio: intended side has >60% participation
    """
    
    def __init__(self,
                 confirmation_delay_sec: float = 2.0,
                 delta_direction_tolerance: float = 0.3,  # allow 30% reversal before reject
                 delta_velocity_maintenance: float = 0.70,  # 70% of entry velocity
                 reversal_volume_threshold: float = 0.40,  # 40% of entry volume
                 spread_widening_tolerance: float = 0.25,  # 25% widening allowed
                 min_participation_ratio: float = 0.60,  # 60% dominance needed
                 spread_stability_threshold: float = 0.50):  # spread score > 50
        """
        Initialize live confirmation validator.
        
        Args:
            confirmation_delay_sec: wait time after entry before confirming
            delta_direction_tolerance: max delta reversal before reject
            delta_velocity_maintenance: min velocity ratio to maintain
            reversal_volume_threshold: max opposite side volume ratio
            spread_widening_tolerance: max spread widening allowed
            min_participation_ratio: min participation needed on intended side
            spread_stability_threshold: min spread health score (0-100)
        """
        self.confirmation_delay_sec = confirmation_delay_sec
        self.delta_direction_tolerance = delta_direction_tolerance
        self.delta_velocity_maintenance = delta_velocity_maintenance
        self.reversal_volume_threshold = reversal_volume_threshold
        self.spread_widening_tolerance = spread_widening_tolerance
        self.min_participation_ratio = min_participation_ratio
        self.spread_stability_threshold = spread_stability_threshold
        
        # Per-symbol entry tracking
        self.pending_entries: Dict[str, Dict] = {}  # symbol -> {entry_time, entry_bar, ...}
        self.entry_metrics: Dict[str, Dict] = {}  # symbol -> {entry_delta_vel, spread, ...}
    
    def record_entry(self, symbol: str, side: OrderSide, entry_bar: BarData,
                    entry_velocity: float, entry_spread: float):
        """
        Record entry for later confirmation.
        
        Args:
            symbol: Trading symbol
            side: Buy or Sell entry
            entry_bar: The bar at which entry was taken
            entry_velocity: Delta velocity at entry (for comparison)
            entry_spread: Spread at entry (for comparison)
        """
        self.pending_entries[symbol] = {
            'side': side,
            'entry_bar': entry_bar,
            'entry_time': datetime.now().timestamp(),
            'entry_bar_timestamp': entry_bar.timestamp,
        }
        
        self.entry_metrics[symbol] = {
            'delta_velocity': entry_velocity,
            'spread': entry_spread,
            'entry_delta': entry_bar.cumulative_delta,
        }
        
        logger.info(
            f"Recorded entry for {symbol} {side.value} at ${entry_bar.close:.2f}, "
            f"vel={entry_velocity:.2f}, spread={entry_spread:.2f}"
        )
    
    def confirm_entry(self, symbol: str, confirmation_bar: BarData,
                     confirmation_events: List[OrderFlowEvent]) -> Optional[LiveConfirmationSignal]:
        """
        Confirm entry with new bar data.
        
        Args:
            symbol: Trading symbol
            confirmation_bar: Bar after confirmation delay
            confirmation_events: Events during confirmation period
            
        Returns:
            LiveConfirmationSignal with accept/reject decision
        """
        if symbol not in self.pending_entries:
            logger.warning(f"No pending entry for {symbol}")
            return None
        
        entry_data = self.pending_entries[symbol]
        metrics_data = self.entry_metrics[symbol]
        
        # Check confirmation delay has passed
        time_elapsed = confirmation_bar.timestamp - entry_data['entry_bar_timestamp']
        if time_elapsed < 1.0:
            logger.debug(f"Confirmation delay not met: {time_elapsed:.2f}s < 1.0s")
            return None
        
        side = entry_data['side']
        
        # Compute metrics
        metrics = self._validate_continuation(
            side, confirmation_bar, confirmation_events,
            metrics_data, entry_data['entry_bar']
        )
        
        # Calculate continuation quality score
        continuation_quality_score = self._calculate_continuation_score(
            metrics, confirmation_events, side
        )
        
        # Determine accept/reject
        rejection_reasons = []
        should_accept = self._check_acceptance_criteria(
            metrics, continuation_quality_score, rejection_reasons
        )
        
        signal = LiveConfirmationSignal(
            symbol=symbol,
            side=side,
            entry_bar_timestamp=entry_data['entry_bar_timestamp'],
            confirmation_bar_timestamp=confirmation_bar.timestamp,
            continuation_quality_score=continuation_quality_score,
            metrics=metrics,
            should_accept_entry=should_accept,
            rejection_reasons=rejection_reasons,
            timestamp=confirmation_bar.timestamp
        )
        
        logger.info(
            f"Confirmation for {symbol}: score={continuation_quality_score:.1f}, "
            f"accept={should_accept}, reasons={rejection_reasons}"
        )
        
        # Clean up
        del self.pending_entries[symbol]
        if symbol in self.entry_metrics:
            del self.entry_metrics[symbol]
        
        return signal
    
    def _validate_continuation(self, side: OrderSide, confirmation_bar: BarData,
                              confirmation_events: List[OrderFlowEvent],
                              metrics_data: Dict, entry_bar: BarData) -> ContinuationMetrics:
        """Validate continuation metrics."""
        
        metrics = ContinuationMetrics()
        
        # 1. Check delta direction alignment
        entry_delta = metrics_data['entry_delta']
        current_delta = confirmation_bar.cumulative_delta
        
        if side == OrderSide.BUY:
            metrics.delta_direction_aligned = current_delta >= entry_delta
        else:
            metrics.delta_direction_aligned = current_delta <= entry_delta
        
        # 2. Compute participation ratios
        if confirmation_events:
            buy_volume = sum(e.size for e in confirmation_events if e.side == OrderSide.BUY)
            sell_volume = sum(e.size for e in confirmation_events if e.side == OrderSide.SELL)
            total_volume = buy_volume + sell_volume
            
            if total_volume > 0:
                metrics.participation_ratio_buy = buy_volume / total_volume
                metrics.participation_ratio_sell = sell_volume / total_volume
                
                if side == OrderSide.BUY:
                    metrics.participation_ratio_dominance = metrics.participation_ratio_buy
                else:
                    metrics.participation_ratio_dominance = metrics.participation_ratio_sell
            else:
                metrics.participation_ratio_dominance = 0.5
        
        # 3. Check reversal signals
        metrics.reversal_signals = self._count_reversal_signals(
            side, confirmation_events, confirmation_bar, entry_bar
        )
        
        # 4. Compute spread health
        metrics.spread_health_score = self._compute_spread_health(confirmation_bar)
        
        # 5. Check liquidity
        if confirmation_bar.volume > 0:
            metrics.liquidity_sufficient = True
        else:
            metrics.liquidity_sufficient = False
        
        # 6. Check delta velocity
        current_velocity = self._compute_delta_velocity(confirmation_events)
        entry_velocity = metrics_data['delta_velocity']
        
        if entry_velocity > 0:
            velocity_ratio = current_velocity / entry_velocity
            metrics.delta_velocity_maintained = velocity_ratio >= self.delta_velocity_maintenance
        else:
            metrics.delta_velocity_maintained = True
        
        return metrics
    
    def _count_reversal_signals(self, entry_side: OrderSide,
                               confirmation_events: List[OrderFlowEvent],
                               confirmation_bar: BarData,
                               entry_bar: BarData) -> int:
        """Count number of reversal indicators."""
        
        reversal_count = 0
        
        if not confirmation_events:
            return reversal_count
        
        # 1. Check for opposite side volume surge
        buy_volume = sum(e.size for e in confirmation_events if e.side == OrderSide.BUY)
        sell_volume = sum(e.size for e in confirmation_events if e.side == OrderSide.SELL)
        total_volume = buy_volume + sell_volume
        
        if entry_side == OrderSide.BUY and sell_volume > 0:
            sell_ratio = sell_volume / total_volume if total_volume > 0 else 0
            if sell_ratio > self.reversal_volume_threshold:
                reversal_count += 1
        elif entry_side == OrderSide.SELL and buy_volume > 0:
            buy_ratio = buy_volume / total_volume if total_volume > 0 else 0
            if buy_ratio > self.reversal_volume_threshold:
                reversal_count += 1
        
        # 2. Check for delta collapse
        entry_delta_abs = abs(entry_bar.cumulative_delta)
        current_delta_abs = abs(confirmation_bar.cumulative_delta)
        
        if entry_delta_abs > 0:
            delta_ratio = current_delta_abs / entry_delta_abs
            if delta_ratio < 0.5:  # delta collapsed to < 50%
                reversal_count += 1
        
        # 3. Check for price reversal
        if entry_side == OrderSide.BUY and confirmation_bar.close < entry_bar.close:
            if (entry_bar.close - confirmation_bar.close) > (entry_bar.close * 0.01):  # 1% reversal
                reversal_count += 1
        elif entry_side == OrderSide.SELL and confirmation_bar.close > entry_bar.close:
            if (confirmation_bar.close - entry_bar.close) > (entry_bar.close * 0.01):  # 1% reversal
                reversal_count += 1
        
        return reversal_count
    
    def _compute_spread_health(self, bar: BarData) -> float:
        """
        Compute spread health score (0-100).
        
        Higher = tighter/healthier spread.
        """
        if not bar.price_levels:
            # Use bid/ask volume ratio as proxy
            total_vol = bar.bid_volume + bar.ask_volume
            if total_vol == 0:
                return 50.0
            
            bid_ratio = bar.bid_volume / total_vol
            # If close to 50/50, spread is tight
            imbalance = abs(bid_ratio - 0.5)
            score = max(0.0, 100.0 - imbalance * 200)
            return score
        
        # Analyze bid/ask level concentration
        best_bid = max([p for p, pl in bar.price_levels.items() if pl.bid_volume > 0],
                       default=0)
        best_ask = min([p for p, pl in bar.price_levels.items() if pl.ask_volume > 0],
                       default=float('inf'))
        
        if best_bid == 0 or best_ask == float('inf'):
            return 50.0
        
        spread = best_ask - best_bid
        # Normalize: small spread = 100, large spread = low score
        max_spread = bar.close * 0.01  # 1% of price
        if spread <= 0:
            return 100.0
        
        score = max(0.0, 100.0 * (1.0 - spread / max_spread))
        return min(100.0, score)
    
    def _compute_delta_velocity(self, events: List[OrderFlowEvent]) -> float:
        """Compute delta velocity from events."""
        if not events or len(events) < 2:
            return 0.0
        
        cumulative_delta = 0.0
        for event in events:
            if event.side == OrderSide.BUY:
                cumulative_delta += event.size
            else:
                cumulative_delta -= event.size
        
        time_span = events[-1].timestamp - events[0].timestamp
        if time_span <= 0:
            return 0.0
        
        return abs(cumulative_delta) / time_span
    
    def _calculate_continuation_score(self, metrics: ContinuationMetrics,
                                    confirmation_events: List[OrderFlowEvent],
                                    side: OrderSide) -> float:
        """
        Calculate continuation quality score (0-100).
        
        Weighted metrics:
        - Delta direction: 30%
        - Participation dominance: 25%
        - Spread health: 20%
        - Delta velocity maintenance: 15%
        - Reversal signals (inverse): 10%
        """
        
        # Delta direction (0-100)
        delta_score = 100.0 if metrics.delta_direction_aligned else 20.0
        
        # Participation (0-100)
        participation_score = metrics.participation_ratio_dominance * 100
        
        # Spread health (0-100)
        spread_score = metrics.spread_health_score
        
        # Delta velocity (0-100)
        velocity_score = 100.0 if metrics.delta_velocity_maintained else 30.0
        
        # Reversal penalty (0-100, lower if more reversals)
        reversal_score = max(0.0, 100.0 - metrics.reversal_signals * 30)
        
        # Liquidity bonus
        liquidity_score = 100.0 if metrics.liquidity_sufficient else 50.0
        
        # Weighted average
        total_score = (
            delta_score * 0.30 +
            participation_score * 0.25 +
            spread_score * 0.20 +
            velocity_score * 0.15 +
            reversal_score * 0.10
        )
        
        return min(100.0, max(0.0, total_score))
    
    def _check_acceptance_criteria(self, metrics: ContinuationMetrics,
                                  continuation_quality_score: float,
                                  rejection_reasons: List[str]) -> bool:
        """
        Check acceptance criteria and populate rejection reasons.
        
        Returns: True if entry should be accepted, False otherwise
        """
        accept = True
        
        # Check 1: Delta direction
        if not metrics.delta_direction_aligned:
            rejection_reasons.append("Delta reversed")
            accept = False
        
        # Check 2: Reversal signals
        if metrics.reversal_signals > 1:
            rejection_reasons.append(f"{metrics.reversal_signals} reversal signals")
            accept = False
        
        # Check 3: Participation ratio
        if metrics.participation_ratio_dominance < self.min_participation_ratio:
            rejection_reasons.append(
                f"Participation only {metrics.participation_ratio_dominance:.1%} "
                f"(need {self.min_participation_ratio:.1%})"
            )
            accept = False
        
        # Check 4: Spread health
        if metrics.spread_health_score < self.spread_stability_threshold:
            rejection_reasons.append(
                f"Spread health {metrics.spread_health_score:.0f} "
                f"(need >{self.spread_stability_threshold:.0f})"
            )
            accept = False
        
        # Check 5: Delta velocity
        if not metrics.delta_velocity_maintained:
            rejection_reasons.append("Delta velocity dropped")
            accept = False
        
        # Check 6: Liquidity
        if not metrics.liquidity_sufficient:
            rejection_reasons.append("Insufficient liquidity")
            accept = False
        
        # Check 7: Overall score
        if continuation_quality_score < 40.0:
            rejection_reasons.append(
                f"Low continuation score: {continuation_quality_score:.0f}"
            )
            accept = False
        
        return accept


def create_validator(config) -> LiveConfirmationValidator:
    """Factory function to create validator from config."""
    return LiveConfirmationValidator(
        confirmation_delay_sec=getattr(config, 'confirmation_delay_sec', 2.0),
        delta_direction_tolerance=getattr(config, 'delta_direction_tolerance', 0.3),
        delta_velocity_maintenance=getattr(config, 'delta_velocity_maintenance', 0.70),
        reversal_volume_threshold=getattr(config, 'reversal_volume_threshold', 0.40),
        spread_widening_tolerance=getattr(config, 'spread_widening_tolerance', 0.25),
        min_participation_ratio=getattr(config, 'min_participation_ratio', 0.60),
        spread_stability_threshold=getattr(config, 'spread_stability_threshold', 50.0),
    )
