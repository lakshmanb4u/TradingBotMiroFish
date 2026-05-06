"""
Live feed health monitoring and safety checks.
Implements Blocker #6: Enhanced feed monitoring with safety guardrails.

Metrics:
- Events/sec, trades/sec
- Invalid %, duplicate %, reorder %
- Spread violations, cumulative delta
- Aggression metrics
- Safety checks for alerting
"""

import logging
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from collections import deque
import time

logger = logging.getLogger(__name__)


@dataclass
class FeedHealthMetrics:
    """Health metrics for a feed."""
    timestamp: float
    symbol: str
    
    # Event rates
    events_per_sec: float = 0.0
    trades_per_sec: float = 0.0
    depth_updates_per_sec: float = 0.0
    
    # Quality metrics
    invalid_percentage: float = 0.0
    duplicate_percentage: float = 0.0
    reorder_percentage: float = 0.0
    spread_violation_percentage: float = 0.0
    
    # Delta metrics
    cumulative_delta: float = 0.0
    delta_acceleration: float = 0.0
    aggression_ratio: float = 0.5
    
    # Spread metrics
    latest_bid: float = 0.0
    latest_ask: float = 0.0
    latest_spread_bps: float = 0.0
    avg_spread_bps: float = 0.0
    max_spread_bps: float = 0.0
    
    # Buffer metrics
    buffer_depth: int = 0
    buffer_max_depth: int = 0
    buffer_overflows: int = 0
    
    # Health flags
    feed_active: bool = True
    last_event_timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    feed_stale_seconds: float = 0.0
    
    # Alerts triggered
    alerts_triggered: int = 0


@dataclass
class SafetyCheckResult:
    """Result of a safety check."""
    check_name: str
    is_safe: bool
    reason: str
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    severity: str = "INFO"  # INFO, WARN, ERROR
    recommended_action: str = ""


class FeedHealthMonitor:
    """Monitors feed health and enforces safety checks."""
    
    # Safety thresholds
    INVALID_THRESHOLD = 0.05  # Alert if >5% invalid
    DUPLICATE_THRESHOLD = 0.02  # Alert if >2% duplicates
    REORDER_THRESHOLD = 0.01  # Alert if >1% out of order
    SPREAD_VIOLATION_THRESHOLD = 0.001  # Alert if >0.1% spread violations
    FEED_STALE_SECONDS = 5  # Alert if no events for >5s
    BUFFER_OVERFLOW_LIMIT = 100  # Alert if buffer overflows >100 times
    MAX_BUFFER_DEPTH = 1000  # Alert if buffer depth > 1000
    
    def __init__(self):
        """Initialize health monitor."""
        self.metrics_history: Dict[str, deque] = {}  # symbol -> deque of FeedHealthMetrics
        self.max_history = 3600  # Keep 1 hour of metrics
        
        # Aggregators for rate calculations
        self.event_counters: Dict[str, dict] = {}  # symbol -> {event_type -> count}
        self.last_counter_reset: Dict[str, float] = {}  # symbol -> timestamp
        
        # Safety check results
        self.safety_check_history: List[SafetyCheckResult] = []
        self.recent_failures: List[SafetyCheckResult] = []
        
        # Statistics
        self.total_checks_performed = 0
        self.total_checks_failed = 0
        self.alerts_refused = 0  # Count of alerts refused due to safety checks
    
    def record_event(self,
                    symbol: str,
                    event_type: str,
                    is_valid: bool = True,
                    is_duplicate: bool = False,
                    was_reordered: bool = False):
        """Record event for rate tracking.
        
        Args:
            symbol: Symbol
            event_type: Type of event (trade, depth, etc.)
            is_valid: Whether event passed validation
            is_duplicate: Whether event was a duplicate
            was_reordered: Whether event was out of order
        """
        if symbol not in self.event_counters:
            self.event_counters[symbol] = {
                'total': 0,
                'valid': 0,
                'invalid': 0,
                'duplicate': 0,
                'reordered': 0,
            }
            self.last_counter_reset[symbol] = datetime.now().timestamp()
        
        counters = self.event_counters[symbol]
        counters['total'] += 1
        
        if is_valid:
            counters['valid'] += 1
        else:
            counters['invalid'] += 1
        
        if is_duplicate:
            counters['duplicate'] += 1
        
        if was_reordered:
            counters['reordered'] += 1
    
    def calculate_metrics(self,
                         symbol: str,
                         cumulative_delta: float = 0.0,
                         delta_acceleration: float = 0.0,
                         aggression_ratio: float = 0.5,
                         latest_bid: float = 0.0,
                         latest_ask: float = 0.0,
                         latest_spread_bps: float = 0.0,
                         avg_spread_bps: float = 0.0,
                         max_spread_bps: float = 0.0,
                         buffer_depth: int = 0,
                         buffer_max_depth: int = 0,
                         buffer_overflows: int = 0,
                         spread_violations: int = 0) -> FeedHealthMetrics:
        """Calculate and record health metrics.
        
        Args:
            symbol: Symbol
            Various metric inputs
            
        Returns:
            FeedHealthMetrics with calculated metrics
        """
        if symbol not in self.event_counters:
            return FeedHealthMetrics(
                timestamp=datetime.now().timestamp(),
                symbol=symbol
            )
        
        counters = self.event_counters[symbol]
        now = datetime.now().timestamp()
        
        # Calculate rates
        time_delta = now - self.last_counter_reset[symbol]
        
        if time_delta > 0:
            total_events = counters['total']
            events_per_sec = total_events / time_delta
            trades_per_sec = 0  # TODO: separate trade count
            depth_updates_per_sec = 0  # TODO: separate depth count
        else:
            events_per_sec = 0
            trades_per_sec = 0
            depth_updates_per_sec = 0
        
        # Calculate percentages
        total = counters['total']
        if total > 0:
            invalid_pct = counters['invalid'] / total
            duplicate_pct = counters['duplicate'] / total
            reorder_pct = counters['reordered'] / total
        else:
            invalid_pct = 0
            duplicate_pct = 0
            reorder_pct = 0
        
        if latest_bid > 0 and latest_ask > 0:
            spread_violation_pct = spread_violations / max(total, 1)
        else:
            spread_violation_pct = 0
        
        # Create metrics
        metrics = FeedHealthMetrics(
            timestamp=now,
            symbol=symbol,
            events_per_sec=events_per_sec,
            trades_per_sec=trades_per_sec,
            depth_updates_per_sec=depth_updates_per_sec,
            invalid_percentage=invalid_pct,
            duplicate_percentage=duplicate_pct,
            reorder_percentage=reorder_pct,
            spread_violation_percentage=spread_violation_pct,
            cumulative_delta=cumulative_delta,
            delta_acceleration=delta_acceleration,
            aggression_ratio=aggression_ratio,
            latest_bid=latest_bid,
            latest_ask=latest_ask,
            latest_spread_bps=latest_spread_bps,
            avg_spread_bps=avg_spread_bps,
            max_spread_bps=max_spread_bps,
            buffer_depth=buffer_depth,
            buffer_max_depth=buffer_max_depth,
            buffer_overflows=buffer_overflows,
            last_event_timestamp=now,
        )
        
        # Store in history
        if symbol not in self.metrics_history:
            self.metrics_history[symbol] = deque(maxlen=self.max_history)
        
        self.metrics_history[symbol].append(metrics)
        
        return metrics
    
    def perform_safety_checks(self, 
                             metrics: FeedHealthMetrics) -> List[SafetyCheckResult]:
        """Perform safety checks on metrics.
        
        Args:
            metrics: FeedHealthMetrics to check
            
        Returns:
            List of SafetyCheckResult
        """
        self.total_checks_performed += 1
        results = []
        
        # Check invalid percentage
        result = SafetyCheckResult(
            check_name="INVALID_EVENTS_PERCENTAGE",
            is_safe=metrics.invalid_percentage <= self.INVALID_THRESHOLD,
            reason=f"Invalid events: {metrics.invalid_percentage*100:.2f}%",
            metric_value=metrics.invalid_percentage,
            threshold=self.INVALID_THRESHOLD,
            severity="ERROR" if metrics.invalid_percentage > 0.1 else "WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check duplicate percentage
        result = SafetyCheckResult(
            check_name="DUPLICATE_EVENTS_PERCENTAGE",
            is_safe=metrics.duplicate_percentage <= self.DUPLICATE_THRESHOLD,
            reason=f"Duplicate events: {metrics.duplicate_percentage*100:.2f}%",
            metric_value=metrics.duplicate_percentage,
            threshold=self.DUPLICATE_THRESHOLD,
            severity="WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check reorder percentage
        result = SafetyCheckResult(
            check_name="REORDERED_EVENTS_PERCENTAGE",
            is_safe=metrics.reorder_percentage <= self.REORDER_THRESHOLD,
            reason=f"Reordered events: {metrics.reorder_percentage*100:.3f}%",
            metric_value=metrics.reorder_percentage,
            threshold=self.REORDER_THRESHOLD,
            severity="WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check spread violations
        result = SafetyCheckResult(
            check_name="SPREAD_VIOLATIONS",
            is_safe=metrics.spread_violation_percentage <= self.SPREAD_VIOLATION_THRESHOLD,
            reason=f"Spread violations: {metrics.spread_violation_percentage*100:.3f}%",
            metric_value=metrics.spread_violation_percentage,
            threshold=self.SPREAD_VIOLATION_THRESHOLD,
            severity="WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check feed staleness
        stale_seconds = (datetime.now().timestamp() - metrics.last_event_timestamp)
        result = SafetyCheckResult(
            check_name="FEED_STALENESS",
            is_safe=stale_seconds < self.FEED_STALE_SECONDS,
            reason=f"No events for {stale_seconds:.1f} seconds",
            metric_value=stale_seconds,
            threshold=self.FEED_STALE_SECONDS,
            severity="ERROR" if stale_seconds > 10 else "WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check buffer depth
        result = SafetyCheckResult(
            check_name="BUFFER_DEPTH",
            is_safe=metrics.buffer_depth < self.MAX_BUFFER_DEPTH,
            reason=f"Buffer depth: {metrics.buffer_depth} events",
            metric_value=float(metrics.buffer_depth),
            threshold=float(self.MAX_BUFFER_DEPTH),
            severity="ERROR" if metrics.buffer_depth > 5000 else "WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        # Check buffer overflows
        result = SafetyCheckResult(
            check_name="BUFFER_OVERFLOWS",
            is_safe=metrics.buffer_overflows < self.BUFFER_OVERFLOW_LIMIT,
            reason=f"Buffer overflows: {metrics.buffer_overflows}",
            metric_value=float(metrics.buffer_overflows),
            threshold=float(self.BUFFER_OVERFLOW_LIMIT),
            severity="WARN"
        )
        results.append(result)
        if not result.is_safe:
            self.total_checks_failed += 1
            self.recent_failures.append(result)
        
        return results
    
    def can_alert(self, metrics: FeedHealthMetrics) -> bool:
        """Check if it's safe to send alerts.
        
        Args:
            metrics: Current feed metrics
            
        Returns:
            True if safe to alert, False otherwise
        """
        # Refuse if too many invalids
        if metrics.invalid_percentage > self.INVALID_THRESHOLD:
            self.alerts_refused += 1
            logger.warning(
                f"[{metrics.symbol}] Refusing alert: invalid% {metrics.invalid_percentage*100:.2f}% "
                f"exceeds threshold {self.INVALID_THRESHOLD*100:.1f}%"
            )
            return False
        
        # Refuse if feed is stale
        stale_seconds = datetime.now().timestamp() - metrics.last_event_timestamp
        if stale_seconds > self.FEED_STALE_SECONDS:
            self.alerts_refused += 1
            logger.warning(
                f"[{metrics.symbol}] Refusing alert: feed stale for {stale_seconds:.1f}s"
            )
            return False
        
        # Refuse if buffer overflowing
        if metrics.buffer_depth > self.MAX_BUFFER_DEPTH:
            self.alerts_refused += 1
            logger.warning(
                f"[{metrics.symbol}] Refusing alert: buffer depth {metrics.buffer_depth} "
                f"exceeds max {self.MAX_BUFFER_DEPTH}"
            )
            return False
        
        # Refuse if too many spreads spike (crossed books detected)
        if metrics.spread_violation_percentage > self.SPREAD_VIOLATION_THRESHOLD * 10:
            self.alerts_refused += 1
            logger.warning(
                f"[{metrics.symbol}] Refusing alert: spread violations spiked to "
                f"{metrics.spread_violation_percentage*100:.2f}%"
            )
            return False
        
        return True
    
    def to_json(self) -> str:
        """Export metrics and safety checks as JSON."""
        export = {
            'timestamp': datetime.now().timestamp(),
            'feed_health_stats': {
                'total_checks_performed': self.total_checks_performed,
                'total_checks_failed': self.total_checks_failed,
                'alerts_refused': self.alerts_refused,
            },
            'recent_failures': [
                {
                    'check_name': f.check_name,
                    'is_safe': f.is_safe,
                    'reason': f.reason,
                    'metric_value': f.metric_value,
                    'threshold': f.threshold,
                    'severity': f.severity,
                }
                for f in self.recent_failures[-20:]  # Last 20 failures
            ],
            'symbol_metrics': {}
        }
        
        for symbol, history in self.metrics_history.items():
            if history:
                latest = history[-1]
                export['symbol_metrics'][symbol] = asdict(latest)
        
        return json.dumps(export, indent=2)
    
    def get_stats(self) -> dict:
        """Get monitoring statistics."""
        return {
            'total_checks_performed': self.total_checks_performed,
            'total_checks_failed': self.total_checks_failed,
            'alerts_refused': self.alerts_refused,
            'symbols_monitored': len(self.metrics_history),
            'recent_failures_count': len(self.recent_failures),
        }
