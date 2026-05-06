"""
Spread validation for Bookmap/Rithmic streams.
Implements Blocker #2: Spread validation.

Rules:
- Reject if bid >= ask (invalid spread)
- Detect stale quotes (no update >100ms)
- Filter crossed books (bid > ask)
- Track best bid/ask for anomaly detection
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class SpreadSnapshot:
    """Snapshot of bid/ask at a point in time."""
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: float
    symbol: str
    
    @property
    def spread(self) -> float:
        """Spread in price units."""
        return max(0, self.ask - self.bid)
    
    @property
    def spread_bps(self) -> float:
        """Spread in basis points."""
        if self.bid <= 0:
            return 0
        return (self.spread / self.bid) * 10000
    
    @property
    def is_valid(self) -> bool:
        """Check if spread is valid."""
        return self.ask > self.bid and self.bid > 0 and self.ask > 0


@dataclass
class SpreadValidationResult:
    """Result of spread validation."""
    is_valid: bool
    bid: float
    ask: float
    spread: float
    rejection_reason: Optional[str] = None
    anomaly_type: Optional[str] = None  # CROSSED, INVERTED, STALE, etc.
    age_ms: Optional[float] = None  # Age of quote in milliseconds
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


class SpreadValidator:
    """Validates and monitors bid/ask spreads."""
    
    # Thresholds
    MAX_STALE_AGE_MS = 100  # Consider quote stale if >100ms old
    MAX_NORMAL_SPREAD_BPS = 500  # Alert if spread > 500 bps (very wide)
    MAX_DEPTH_SKEW = 10  # Alert if bid/ask size ratio > 10:1
    
    def __init__(self, 
                 max_stale_age_ms: float = 100,
                 max_spread_bps: float = 500):
        """Initialize validator.
        
        Args:
            max_stale_age_ms: Consider quote stale if older than this (ms)
            max_spread_bps: Alert if spread exceeds this (basis points)
        """
        self.max_stale_age_ms = max_stale_age_ms
        self.max_spread_bps = max_spread_bps
        
        # Track per-symbol state
        self.last_bid: Dict[str, float] = {}
        self.last_ask: Dict[str, float] = {}
        self.last_update: Dict[str, float] = {}  # timestamp in seconds
        self.bid_size: Dict[str, float] = {}
        self.ask_size: Dict[str, float] = {}
        
        # History for anomaly detection
        self.spread_history: Dict[str, deque] = {}  # symbol -> deque of SpreadSnapshot
        self.max_history = 1000
        
        # Statistics
        self.valid_spreads = 0
        self.invalid_spreads = 0
        self.stale_spreads = 0
        self.crossed_spreads = 0
        self.wide_spreads = 0
    
    def validate_spread(self,
                       bid: float,
                       ask: float,
                       bid_size: float,
                       ask_size: float,
                       symbol: str,
                       timestamp: Optional[float] = None) -> SpreadValidationResult:
        """Validate a bid/ask spread.
        
        Args:
            bid: Bid price
            ask: Ask price
            bid_size: Bid volume
            ask_size: Ask volume
            symbol: Symbol
            timestamp: Timestamp (seconds since epoch)
            
        Returns:
            SpreadValidationResult with validation details
        """
        timestamp = timestamp or datetime.now().timestamp()
        result = SpreadValidationResult(
            is_valid=True,
            bid=bid,
            ask=ask,
            spread=ask - bid,
            timestamp=timestamp
        )
        
        # Check for None
        if bid is None or ask is None:
            result.is_valid = False
            result.rejection_reason = "NULL_SPREAD"
            result.anomaly_type = "NULL"
            self.invalid_spreads += 1
            logger.warning(f"[{symbol}] NULL spread: bid={bid}, ask={ask}")
            return result
        
        # Check for negative or zero prices
        if bid <= 0 or ask <= 0:
            result.is_valid = False
            result.rejection_reason = "NEGATIVE_PRICES"
            result.anomaly_type = "INVALID_PRICE"
            self.invalid_spreads += 1
            logger.warning(f"[{symbol}] Non-positive prices: bid={bid}, ask={ask}")
            return result
        
        # Check for crossed book (ask <= bid)
        if ask <= bid:
            result.is_valid = False
            result.rejection_reason = "CROSSED_BOOK"
            result.anomaly_type = "CROSSED"
            result.spread = bid - ask  # Show inversion
            self.crossed_spreads += 1
            self.invalid_spreads += 1
            logger.warning(f"[{symbol}] Crossed book: bid={bid}, ask={ask}")
            return result
        
        # Check for inverted (bid > ask by more than rounding)
        if bid > ask:
            result.is_valid = False
            result.rejection_reason = "INVERTED_BOOK"
            result.anomaly_type = "INVERTED"
            self.invalid_spreads += 1
            logger.warning(f"[{symbol}] Inverted book: bid={bid}, ask={ask}")
            return result
        
        # Check for staleness
        age_ms = (datetime.now().timestamp() - timestamp) * 1000
        result.age_ms = age_ms
        
        if age_ms > self.max_stale_age_ms and symbol in self.last_update:
            # This is a stale quote being replayed
            result.is_valid = False
            result.rejection_reason = f"STALE_QUOTE_{int(age_ms)}ms"
            result.anomaly_type = "STALE"
            self.stale_spreads += 1
            self.invalid_spreads += 1
            logger.warning(f"[{symbol}] Stale quote: age={age_ms:.1f}ms, bid={bid}, ask={ask}")
            return result
        
        # Check for extremely wide spreads
        spread_bps = (result.spread / bid) * 10000
        if spread_bps > self.max_spread_bps:
            # This is technically valid but very unusual
            self.wide_spreads += 1
            logger.warning(f"[{symbol}] Wide spread: {spread_bps:.0f} bps, bid={bid}, ask={ask}")
        
        # Check for size skew
        if bid_size > 0 and ask_size > 0:
            skew = max(bid_size, ask_size) / min(bid_size, ask_size)
            if skew > self.MAX_DEPTH_SKEW:
                logger.warning(f"[{symbol}] Size skew: {skew:.1f}x, "
                              f"bid_size={bid_size}, ask_size={ask_size}")
        
        # Valid spread
        result.is_valid = True
        self.valid_spreads += 1
        
        # Update tracking
        self.last_bid[symbol] = bid
        self.last_ask[symbol] = ask
        self.bid_size[symbol] = bid_size
        self.ask_size[symbol] = ask_size
        self.last_update[symbol] = timestamp
        
        # Store in history
        if symbol not in self.spread_history:
            self.spread_history[symbol] = deque(maxlen=self.max_history)
        
        snapshot = SpreadSnapshot(
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            timestamp=timestamp,
            symbol=symbol
        )
        self.spread_history[symbol].append(snapshot)
        
        return result
    
    def get_best_bid_ask(self, symbol: str) -> Optional[Tuple[float, float]]:
        """Get last known best bid/ask for a symbol."""
        if symbol not in self.last_bid:
            return None
        return (self.last_bid[symbol], self.last_ask[symbol])
    
    def get_spread_statistics(self, symbol: str) -> Optional[dict]:
        """Get statistical summary of spreads for a symbol."""
        if symbol not in self.spread_history:
            return None
        
        history = self.spread_history[symbol]
        if len(history) < 2:
            return None
        
        spreads = [s.spread_bps for s in history]
        sizes = [s.bid_size + s.ask_size for s in history]
        
        return {
            'count': len(history),
            'avg_spread_bps': sum(spreads) / len(spreads),
            'min_spread_bps': min(spreads),
            'max_spread_bps': max(spreads),
            'avg_size': sum(sizes) / len(sizes),
            'latest_bid': history[-1].bid,
            'latest_ask': history[-1].ask,
            'latest_spread_bps': history[-1].spread_bps,
        }
    
    def detect_crossed_book_flash(self, symbol: str, window_seconds: float = 1.0) -> bool:
        """Detect if there have been crossed books recently."""
        if symbol not in self.spread_history:
            return False
        
        now = datetime.now().timestamp()
        history = self.spread_history[symbol]
        
        # Check recent history
        for snapshot in history:
            if now - snapshot.timestamp < window_seconds:
                if snapshot.ask <= snapshot.bid:
                    return True
        
        return False
    
    def get_stats(self) -> dict:
        """Get validation statistics."""
        total = self.valid_spreads + self.invalid_spreads
        if total == 0:
            return {
                'total_spreads': 0,
                'valid': 0,
                'invalid': 0,
                'invalid_percentage': 0,
                'crossed': 0,
                'stale': 0,
                'wide_spreads': 0,
            }
        
        return {
            'total_spreads': total,
            'valid': self.valid_spreads,
            'invalid': self.invalid_spreads,
            'invalid_percentage': (self.invalid_spreads / total * 100),
            'crossed': self.crossed_spreads,
            'stale': self.stale_spreads,
            'wide_spreads': self.wide_spreads,
        }
    
    def reset_stats(self):
        """Reset statistics."""
        self.valid_spreads = 0
        self.invalid_spreads = 0
        self.stale_spreads = 0
        self.crossed_spreads = 0
        self.wide_spreads = 0
