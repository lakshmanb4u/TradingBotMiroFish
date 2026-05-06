"""
Event normalization for Bookmap/Rithmic streams.
Implements Blocker #1: Zero-size trade normalization.

Rules:
- Mark zero-size or negative-size trades as invalid
- Exclude invalid trades from delta/aggression calculations
- Preserve for audit trail with audit_marker
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    """Result of trade normalization."""
    is_valid: bool
    original_size: float
    normalized_size: float
    audit_marker: Optional[str] = None  # Reason if invalid
    rejection_reason: Optional[str] = None
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def __repr__(self):
        return (f"NormalizationResult(valid={self.is_valid}, "
                f"size={self.normalized_size}, marker={self.audit_marker})")


class TradeNormalizer:
    """Normalizes and validates trades from live feed."""
    
    MIN_VALID_SIZE = 0.01  # Reject if size < this
    MAX_REASONABLE_SIZE = 1_000_000  # Reject if size > this (likely corrupt)
    
    def __init__(self, min_size: float = 0.01, max_size: float = 1_000_000):
        """Initialize normalizer.
        
        Args:
            min_size: Minimum valid trade size
            max_size: Maximum reasonable trade size (sanity check)
        """
        self.min_size = min_size
        self.max_size = max_size
        self.invalid_count = 0
        self.valid_count = 0
        self.audit_log = []
    
    def normalize_trade(self, 
                       size: float, 
                       price: float,
                       symbol: str = "",
                       timestamp: Optional[float] = None) -> NormalizationResult:
        """Normalize a single trade.
        
        Args:
            size: Trade size (contracts/shares)
            price: Trade price
            symbol: Symbol for logging
            timestamp: Event timestamp
            
        Returns:
            NormalizationResult with validity flag and normalization details
        """
        timestamp = timestamp or datetime.now().timestamp()
        result = NormalizationResult(
            is_valid=True,
            original_size=size,
            normalized_size=size,
            timestamp=timestamp
        )
        
        # Check for None
        if size is None:
            result.is_valid = False
            result.audit_marker = "NULL_SIZE"
            result.rejection_reason = "Trade size is None"
            self.invalid_count += 1
            self.audit_log.append({
                'symbol': symbol,
                'reason': result.rejection_reason,
                'timestamp': timestamp,
                'severity': 'ERROR'
            })
            logger.warning(f"[{symbol}] Rejecting NULL size trade at {price}")
            return result
        
        # Check for zero or negative
        if size <= 0:
            result.is_valid = False
            result.normalized_size = 0.0
            result.audit_marker = f"INVALID_SIZE_{size}"
            result.rejection_reason = f"Size must be > 0, got {size}"
            self.invalid_count += 1
            self.audit_log.append({
                'symbol': symbol,
                'reason': result.rejection_reason,
                'timestamp': timestamp,
                'severity': 'ERROR'
            })
            logger.warning(f"[{symbol}] Rejecting non-positive size {size} at {price}")
            return result
        
        # Check for suspiciously large size
        if size > self.max_size:
            result.is_valid = False
            result.normalized_size = 0.0
            result.audit_marker = f"OVERSIZED_{size}"
            result.rejection_reason = f"Size {size} exceeds max {self.max_size}"
            self.invalid_count += 1
            self.audit_log.append({
                'symbol': symbol,
                'reason': result.rejection_reason,
                'timestamp': timestamp,
                'severity': 'WARN'
            })
            logger.warning(f"[{symbol}] Rejecting oversized {size} at {price}")
            return result
        
        # Check if below minimum (skip, don't reject - dust trades)
        if size < self.min_size:
            result.is_valid = False
            result.normalized_size = 0.0
            result.audit_marker = f"DUST_{size}"
            result.rejection_reason = f"Size {size} below minimum {self.min_size}"
            self.invalid_count += 1
            self.audit_log.append({
                'symbol': symbol,
                'reason': result.rejection_reason,
                'timestamp': timestamp,
                'severity': 'DEBUG'
            })
            logger.debug(f"[{symbol}] Skipping dust trade size {size} at {price}")
            return result
        
        # Valid trade
        result.is_valid = True
        result.normalized_size = size
        result.audit_marker = None
        self.valid_count += 1
        
        return result
    
    def get_stats(self) -> dict:
        """Get normalization statistics."""
        total = self.valid_count + self.invalid_count
        invalid_pct = (self.invalid_count / total * 100) if total > 0 else 0
        
        return {
            'total_events': total,
            'valid': self.valid_count,
            'invalid': self.invalid_count,
            'invalid_percentage': invalid_pct,
        }
    
    def reset_stats(self):
        """Reset statistics."""
        self.valid_count = 0
        self.invalid_count = 0
        self.audit_log = []
    
    def get_audit_log(self, limit: int = 100) -> list:
        """Get recent audit log entries."""
        return self.audit_log[-limit:]


class PriceNormalizer:
    """Normalizes and validates prices."""
    
    MIN_PRICE = 0.01  # Prices below this are likely errors
    MAX_PRICE = 1_000_000  # Prices above this are likely errors
    
    def __init__(self):
        self.invalid_count = 0
        self.valid_count = 0
    
    def normalize_price(self, 
                       price: float,
                       symbol: str = "") -> Tuple[bool, float, Optional[str]]:
        """Normalize a price.
        
        Args:
            price: Price to validate
            symbol: Symbol for logging
            
        Returns:
            Tuple of (is_valid, normalized_price, reason_if_invalid)
        """
        if price is None:
            logger.warning(f"[{symbol}] NULL price")
            self.invalid_count += 1
            return False, 0.0, "NULL_PRICE"
        
        if price <= 0:
            logger.warning(f"[{symbol}] Non-positive price {price}")
            self.invalid_count += 1
            return False, 0.0, f"NEGATIVE_PRICE_{price}"
        
        if price < self.MIN_PRICE:
            logger.warning(f"[{symbol}] Price {price} below minimum {self.MIN_PRICE}")
            self.invalid_count += 1
            return False, 0.0, "PRICE_TOO_LOW"
        
        if price > self.MAX_PRICE:
            logger.warning(f"[{symbol}] Price {price} exceeds max {self.MAX_PRICE}")
            self.invalid_count += 1
            return False, 0.0, "PRICE_TOO_HIGH"
        
        self.valid_count += 1
        return True, price, None
    
    def get_stats(self) -> dict:
        total = self.valid_count + self.invalid_count
        return {
            'total': total,
            'valid': self.valid_count,
            'invalid': self.invalid_count,
        }


class EventValidator:
    """Validates complete orderflow events."""
    
    def __init__(self):
        self.trade_normalizer = TradeNormalizer()
        self.price_normalizer = PriceNormalizer()
        self.symbol_count = {}  # Track per-symbol issues
    
    def validate_event(self, event_dict: dict, symbol: str) -> Tuple[bool, dict, Optional[str]]:
        """Validate a complete orderflow event.
        
        Args:
            event_dict: Event dictionary from feed
            symbol: Symbol
            
        Returns:
            Tuple of (is_valid, validated_event, rejection_reason)
        """
        rejection_reason = None
        
        # Extract fields
        event_type = event_dict.get('event_type')
        if not event_type:
            return False, event_dict, "MISSING_EVENT_TYPE"
        
        # Handle trades
        if event_type == 'trade':
            size = event_dict.get('size')
            price = event_dict.get('price')
            
            # Validate size
            size_result = self.trade_normalizer.normalize_trade(
                size, price, symbol, 
                event_dict.get('ts_event')
            )
            if not size_result.is_valid:
                return False, event_dict, size_result.rejection_reason
            
            # Validate price
            price_valid, norm_price, price_reason = self.price_normalizer.normalize_price(
                price, symbol
            )
            if not price_valid:
                return False, event_dict, price_reason
            
            # Mark as valid
            event_dict['_valid'] = True
            event_dict['_normalized_size'] = size_result.normalized_size
            return True, event_dict, None
        
        # Handle depth (bid/ask updates)
        elif event_type == 'depth':
            price = event_dict.get('price')
            size = event_dict.get('size')
            
            # Validate price
            price_valid, norm_price, price_reason = self.price_normalizer.normalize_price(
                price, symbol
            )
            if not price_valid:
                return False, event_dict, price_reason
            
            # Validate size (depth can be zero)
            if size is None or size < 0:
                return False, event_dict, "INVALID_DEPTH_SIZE"
            
            # Depth events are valid
            event_dict['_valid'] = True
            return True, event_dict, None
        
        # Unknown event type
        return False, event_dict, f"UNKNOWN_EVENT_TYPE_{event_type}"
    
    def get_per_symbol_stats(self) -> dict:
        """Get per-symbol validation statistics."""
        return {
            'trade_normalizer': self.trade_normalizer.get_stats(),
            'price_normalizer': self.price_normalizer.get_stats(),
        }
