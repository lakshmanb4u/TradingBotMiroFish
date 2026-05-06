"""
Main alert engine - orchestrates all detection and alert generation.
"""

import asyncio
import logging
from typing import Optional, List, Dict
from datetime import datetime
from collections import defaultdict

from data_types import (
    OrderFlowEvent, BarData, OrderFlowAlert, AlertType, AlertSeverity,
    RegimeState, AlertStats
)
from regime_detector import RegimeDetector
from absorption_detector import AbsorptionDetector
from followthrough_gate import FollowThroughGate

logger = logging.getLogger(__name__)


class AlertEngine:
    """
    Main orchestrator for orderflow alert generation.
    
    Pipeline:
    1. Receive orderflow events and bars
    2. Detect market regime
    3. Detect order absorption
    4. Validate with follow-through gate
    5. Generate alerts
    """
    
    def __init__(self, config):
        """Initialize alert engine with configuration."""
        self.config = config
        
        # Detectors
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
        
        # State
        self.regime_states: Dict[str, RegimeState] = {}
        self.pending_alerts: List[OrderFlowAlert] = []
        self.alert_callbacks: List = []
        
        # Statistics
        self.stats = AlertStats()
    
    def register_alert_callback(self, callback):
        """Register callback for generated alerts."""
        self.alert_callbacks.append(callback)
    
    async def process_events(self, events: List[OrderFlowEvent], symbol: str):
        """Process incoming orderflow events."""
        if not events:
            return
        
        # Update absorption detector
        self.absorption_detector.update_events(events, symbol)
        
        logger.debug(f"Processed {len(events)} events for {symbol}")
    
    async def process_bar(self, bar: BarData) -> List[OrderFlowAlert]:
        """
        Process bar data and generate alerts.
        
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
        
        for absorption in absorptions:
            # 3. Check follow-through gate
            confirmation = self.followthrough_gate.submit_absorption(absorption)
            
            # Generate alert (either from absorption or confirmation)
            if confirmation and confirmation.confidence > 0.5:
                alert = self._create_alert(symbol, confirmation, regime)
                alerts.append(alert)
            elif absorption.confidence > 0.6 and not regime or regime.volatility < 0.03:
                # Absorption in non-volatile regime
                alert = self._create_alert(symbol, absorption, regime)
                alerts.append(alert)
        
        # 4. Emit alerts
        for alert in alerts:
            await self._emit_alert(alert)
            self.stats.total_alerts += 1
            self.stats.last_alert_timestamp = alert.timestamp
        
        # 5. Cleanup
        self.followthrough_gate.cleanup_expired(bar.timestamp)
        self.absorption_detector.clear_old_events(symbol, max_age_seconds=300)
        
        return alerts
    
    def _create_alert(self, symbol: str, signal, regime: Optional[RegimeState]) -> OrderFlowAlert:
        """Create alert from absorption or confirmation signal."""
        
        # Determine alert type and severity
        if hasattr(signal, 'confirmations'):
            # FollowThroughConfirmation
            alert_type = AlertType.FOLLOW_THROUGH
            severity = AlertSeverity.HIGH
            absorption = signal.initial_absorption
            confidence = signal.confidence
            followthrough = signal
        else:
            # AbsorptionSignal
            alert_type = AlertType.ABSORPTION
            severity = AlertSeverity.MEDIUM
            absorption = signal
            confidence = signal.confidence
            followthrough = None
        
        # Adjust severity based on confidence
        if confidence > 0.8:
            severity = AlertSeverity.HIGH if severity == AlertSeverity.MEDIUM else AlertSeverity.CRITICAL
        elif confidence < 0.5:
            severity = AlertSeverity.LOW
        
        # Adjust for regime
        if regime:
            if regime.volatility > self.config.regime.volatility_threshold:
                severity = AlertSeverity(min(severity.value + 1, 4))
        
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
            followthrough=followthrough,
            message=self._format_alert_message(absorption, confidence, regime),
            timestamp=absorption.bar.timestamp
        )
        
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
    
    def _format_alert_message(self, absorption, confidence: float,
                             regime: Optional[RegimeState]) -> str:
        """Format alert message."""
        lines = []
        
        lines.append(f"Volume: {absorption.absorbed_volume:,.0f}")
        lines.append(f"Ratio: {absorption.ratio:.1%}")
        lines.append(f"Confidence: {confidence:.1%}")
        
        if regime:
            lines.append(f"Regime: {regime.regime_type.value}")
            lines.append(f"Trend Strength: {regime.trend_strength:.1%}")
        
        return " | ".join(lines)
    
    async def _emit_alert(self, alert: OrderFlowAlert):
        """Emit alert to callbacks."""
        logger.info(f"Generated {alert.alert_type.value} alert for {alert.symbol} at ${alert.price:.2f}")
        
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
