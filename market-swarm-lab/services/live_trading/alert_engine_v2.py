"""
Enhanced alert engine with tape acceleration and live confirmation.

Pipeline:
1. Receive orderflow events and bars
2. Detect market regime
3. Detect order absorption
4. Validate with follow-through gate
5. Detect tape acceleration
6. Record entry (if tape acceleration + absorption positive)
7. Confirm entry (1-3 seconds later)
8. Generate alerts with enhanced signal quality
"""

import asyncio
import logging
from typing import Optional, List, Dict
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass

from data_types import (
    OrderFlowEvent, BarData, OrderFlowAlert, AlertType, AlertSeverity,
    RegimeState, AlertStats, OrderSide
)
from regime_detector import RegimeDetector
from absorption_detector import AbsorptionDetector
from followthrough_gate import FollowThroughGate
from tape_acceleration import TapeAccelerationDetector, TapeAccelerationSignal
from live_confirmation import LiveConfirmationValidator, LiveConfirmationSignal

logger = logging.getLogger(__name__)


@dataclass
class EnhancedAlertMetadata:
    """Enhanced metadata for alerts."""
    tape_acceleration_score: float = 0.0  # 0-100
    participation_ratio_buy: float = 0.0  # aggressive buy % of total
    participation_ratio_sell: float = 0.0  # aggressive sell % of total
    continuation_quality_score: float = 0.0  # 0-100
    spread_health_score: float = 0.0  # 0-100
    acceleration_trend: str = "neutral"  # "accelerating", "stable", "decelerating"
    confirmation_status: str = "pending"  # "pending", "confirmed", "rejected"


class AlertEngineV2:
    """
    Enhanced alert engine with tape acceleration and live confirmation.
    """
    
    def __init__(self, config):
        """Initialize enhanced alert engine with configuration."""
        self.config = config
        
        # Original detectors
        self.regime_detector = RegimeDetector(
            ma_short=config.regime.ma_short,
            ma_long=config.regime.ma_long,
            atr_period=config.regime.atr_period,
            volatility_threshold=config.regime.volatility_threshold
        )
        
        self.absorption_detector = AbsorptionDetector(
            time_window_ms=config.absorption.time_window_ms,
            delta_min_ratio=config.absorption.delta_min_ratio,
            volume_min_pct=config.absorption.volume_min_pct
        )
        
        self.followthrough_gate = FollowThroughGate(
            time_window_ms=config.followthrough.time_window_ms,
            min_confirmation_count=config.followthrough.min_confirmation_count,
            min_volume_ratio=config.followthrough.min_volume_ratio
        )
        
        # New detectors
        self.tape_acceleration_detector = TapeAccelerationDetector(
            market_order_accel_threshold=getattr(config, 'market_order_accel_threshold', 0.5),
            min_trade_velocity=getattr(config, 'min_trade_velocity', 2.0),
            spread_collapse_threshold=getattr(config, 'spread_collapse_threshold', 0.3),
            min_consecutive_prints=getattr(config, 'min_consecutive_prints', 3),
            post_reclaim_window_ms=getattr(config, 'post_reclaim_window_ms', 1000),
            acceleration_score_threshold=getattr(config, 'acceleration_score_threshold', 60.0)
        )
        
        self.live_confirmation_validator = LiveConfirmationValidator(
            confirmation_delay_sec=getattr(config, 'confirmation_delay_sec', 2.0),
            delta_direction_tolerance=getattr(config, 'delta_direction_tolerance', 0.3),
            delta_velocity_maintenance=getattr(config, 'delta_velocity_maintenance', 0.70),
            reversal_volume_threshold=getattr(config, 'reversal_volume_threshold', 0.40),
            spread_widening_tolerance=getattr(config, 'spread_widening_tolerance', 0.25),
            min_participation_ratio=getattr(config, 'min_participation_ratio', 0.60),
            spread_stability_threshold=getattr(config, 'spread_stability_threshold', 50.0)
        )
        
        # State
        self.regime_states: Dict[str, RegimeState] = {}
        self.pending_alerts: List[OrderFlowAlert] = []
        self.alert_callbacks: List = []
        self.recorded_entries: Dict[str, Dict] = {}  # symbol -> entry metadata
        
        # Statistics
        self.stats = AlertStats()
        self.tape_acceleration_signals: List[TapeAccelerationSignal] = []
        self.confirmation_signals: List[LiveConfirmationSignal] = []
    
    def register_alert_callback(self, callback):
        """Register callback for generated alerts."""
        self.alert_callbacks.append(callback)
    
    async def process_events(self, events: List[OrderFlowEvent], symbol: str):
        """Process incoming orderflow events."""
        if not events:
            return
        
        # Update all detectors
        self.absorption_detector.update_events(events, symbol)
        self.tape_acceleration_detector.update_events(events, symbol)
        
        logger.debug(f"Processed {len(events)} events for {symbol}")
    
    async def process_bar(self, bar: BarData) -> List[OrderFlowAlert]:
        """
        Process bar data and generate alerts with enhanced signal validation.
        
        Returns:
            List of generated alerts.
        """
        symbol = bar.symbol
        alerts = []
        
        # 1. Update regime
        regime = self.regime_detector.update(bar)
        if regime:
            self.regime_states[symbol] = regime
        else:
            regime = self.regime_states.get(symbol)
        
        # 2. Analyze bar for absorption
        absorptions = self.absorption_detector.analyze_bar(bar)
        
        # 3. Detect tape acceleration
        tape_accel_signal = self.tape_acceleration_detector.analyze_bar(bar)
        if tape_accel_signal:
            self.tape_acceleration_signals.append(tape_accel_signal)
        
        for absorption in absorptions:
            # 4. Check follow-through gate
            confirmation = self.followthrough_gate.submit_absorption(absorption)
            
            # 5. Validate with tape acceleration
            tape_acceleration_valid = False
            tape_acceleration_score = 0.0
            
            if tape_accel_signal and tape_accel_signal.side == absorption.side:
                tape_acceleration_valid = tape_accel_signal.is_accelerating
                tape_acceleration_score = tape_accel_signal.tape_acceleration_score
                
                logger.info(
                    f"Tape acceleration check for {symbol} {absorption.side.value}: "
                    f"score={tape_acceleration_score:.1f}, valid={tape_acceleration_valid}"
                )
            
            # Generate alert only if tape acceleration is positive
            if tape_acceleration_valid:
                # 6. Record entry for live confirmation
                entry_velocity = tape_accel_signal.metrics.delta_velocity
                spread_health = tape_accel_signal.metrics.spread_stability_score
                
                self.live_confirmation_validator.record_entry(
                    symbol, absorption.side, absorption.bar, entry_velocity, spread_health
                )
                
                self.recorded_entries[symbol] = {
                    'absorption': absorption,
                    'tape_accel_signal': tape_accel_signal,
                    'confirmation': confirmation,
                    'regime': regime,
                }
                
                # Create alert with enhanced metadata
                alert = self._create_enhanced_alert(
                    symbol, absorption, confirmation, regime,
                    tape_acceleration_score, tape_accel_signal
                )
                alerts.append(alert)
        
        # 7. Check for pending entry confirmations
        for symbol_check, entry_data in list(self.recorded_entries.items()):
            # Simple heuristic: check if enough time has passed
            time_since_entry = bar.timestamp - entry_data['absorption'].bar.timestamp
            
            if 1.0 <= time_since_entry <= 3.0:
                # This would be the confirmation bar
                confirmation_signal = self.live_confirmation_validator.confirm_entry(
                    symbol_check, bar, []  # no events, use bar data only
                )
                
                if confirmation_signal:
                    self.confirmation_signals.append(confirmation_signal)
                    
                    if confirmation_signal.should_accept_entry:
                        # Confirmed! Update alert
                        logger.info(
                            f"Entry confirmed for {symbol_check}: "
                            f"score={confirmation_signal.continuation_quality_score:.0f}"
                        )
                    else:
                        logger.warning(
                            f"Entry rejected for {symbol_check}: {confirmation_signal.rejection_reasons}"
                        )
                    
                    # Clean up
                    del self.recorded_entries[symbol_check]
        
        # 8. Emit alerts
        for alert in alerts:
            await self._emit_alert(alert)
            self.stats.total_alerts += 1
            self.stats.last_alert_timestamp = alert.timestamp
        
        # 9. Cleanup
        self.followthrough_gate.cleanup_expired(bar.timestamp)
        self.absorption_detector.clear_old_events(symbol, max_age_seconds=300)
        
        return alerts
    
    def _create_enhanced_alert(self, symbol: str, absorption, confirmation,
                             regime: Optional[RegimeState],
                             tape_acceleration_score: float,
                             tape_accel_signal) -> OrderFlowAlert:
        """Create enhanced alert with tape acceleration and confirmation metadata."""
        
        # Determine alert type and severity
        if confirmation and confirmation.confidence > 0.5:
            alert_type = AlertType.FOLLOW_THROUGH
            severity = AlertSeverity.HIGH
        else:
            alert_type = AlertType.ABSORPTION
            severity = AlertSeverity.MEDIUM
        
        # Adjust severity based on tape acceleration
        if tape_acceleration_score > 80:
            severity = AlertSeverity.CRITICAL
        elif tape_acceleration_score > 60:
            severity = AlertSeverity.HIGH
        
        # Create alert
        alert = OrderFlowAlert(
            alert_type=alert_type,
            severity=severity,
            symbol=symbol,
            side=absorption.side,
            price=absorption.bar.close,
            volume=absorption.absorbed_volume,
            regime=regime,
            absorption_signal=absorption,
            followthrough=confirmation,
            message=self._format_enhanced_alert_message(
                absorption, tape_acceleration_score, tape_accel_signal
            ),
            timestamp=absorption.bar.timestamp
        )
        
        # Store enhanced metadata (as JSON in message for now)
        # TODO: Add to OrderFlowAlert dataclass
        alert.metadata = {
            'tape_acceleration_score': tape_acceleration_score,
            'participation_ratio_buy': tape_accel_signal.metrics.participation_ratio_buy,
            'participation_ratio_sell': tape_accel_signal.metrics.participation_ratio_sell,
            'spread_health_score': tape_accel_signal.metrics.spread_stability_score,
            'acceleration_trend': 'accelerating' if tape_accel_signal.is_accelerating else 'stable',
        }
        
        # Update stats
        alert_key = alert_type
        if alert_key not in self.stats.by_type:
            self.stats.by_type[alert_key] = 0
        self.stats.by_type[alert_key] += 1
        
        severity_key = severity
        if severity_key not in self.stats.by_severity:
            self.stats.by_severity[severity_key] = 0
        self.stats.by_severity[severity_key] += 1
        
        if symbol not in self.stats.by_symbol:
            self.stats.by_symbol[symbol] = 0
        self.stats.by_symbol[symbol] += 1
        
        return alert
    
    def _format_enhanced_alert_message(self, absorption, tape_acceleration_score: float,
                                      tape_accel_signal) -> str:
        """Format enhanced alert message with tape acceleration."""
        lines = []
        
        lines.append(f"Volume: {absorption.absorbed_volume:,.0f}")
        lines.append(f"Ratio: {absorption.ratio:.1%}")
        lines.append(f"Confidence: {absorption.confidence:.1%}")
        lines.append(f"Tape Acceleration: {tape_acceleration_score:.0f}/100")
        
        if tape_accel_signal.metrics.consecutive_initiative_count >= 3:
            lines.append(
                f"Consecutive Initiative: {tape_accel_signal.metrics.consecutive_initiative_count}"
            )
        
        lines.append(f"Spread Health: {tape_accel_signal.metrics.spread_stability_score:.0f}/100")
        
        return " | ".join(lines)
    
    async def _emit_alert(self, alert: OrderFlowAlert):
        """Emit alert to callbacks."""
        logger.info(
            f"Generated {alert.alert_type.value} alert for {alert.symbol} at ${alert.price:.2f} "
            f"with tape acceleration {getattr(alert, 'metadata', {}).get('tape_acceleration_score', 0):.0f}/100"
        )
        
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")
    
    def get_stats(self) -> AlertStats:
        """Get alert statistics."""
        return self.stats
    
    def get_regime(self, symbol: str) -> Optional[RegimeState]:
        """Get current regime for symbol."""
        return self.regime_states.get(symbol)
    
    def get_tape_acceleration_stats(self) -> Dict:
        """Get tape acceleration statistics."""
        if not self.tape_acceleration_signals:
            return {'total': 0}
        
        accelerating = sum(1 for s in self.tape_acceleration_signals if s.is_accelerating)
        avg_score = sum(s.tape_acceleration_score for s in self.tape_acceleration_signals) / len(self.tape_acceleration_signals)
        
        return {
            'total': len(self.tape_acceleration_signals),
            'accelerating': accelerating,
            'avg_score': avg_score,
        }
    
    def get_confirmation_stats(self) -> Dict:
        """Get confirmation statistics."""
        if not self.confirmation_signals:
            return {'total': 0}
        
        accepted = sum(1 for s in self.confirmation_signals if s.should_accept_entry)
        avg_quality = sum(s.continuation_quality_score for s in self.confirmation_signals) / len(self.confirmation_signals)
        
        return {
            'total': len(self.confirmation_signals),
            'accepted': accepted,
            'rejected': len(self.confirmation_signals) - accepted,
            'avg_quality_score': avg_quality,
        }
