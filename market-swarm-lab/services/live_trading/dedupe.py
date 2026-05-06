"""
Event deduplication for live feeds.
Implements Blocker #3: Event deduplication.

Rules:
- Cache events using (timestamp, symbol, price, size, side, sequence)
- Track within 1-minute rolling window
- Mark duplicates with duplicate_marker
- Remove from delta/aggression calculations
"""

import logging
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, Tuple
from datetime import datetime, timedelta
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


@dataclass
class EventFingerprint:
    """Fingerprint to identify unique events."""
    timestamp_ms: int  # Millisecond precision
    symbol: str
    price: float
    size: float
    side: str  # BUY/SELL
    sequence: int
    
    def to_key(self) -> str:
        """Convert to cache key."""
        return f"{self.timestamp_ms}|{self.symbol}|{self.price}|{self.size}|{self.side}|{self.sequence}"
    
    def to_hash(self) -> str:
        """Generate hash for fast comparison."""
        key = self.to_key()
        return hashlib.md5(key.encode()).hexdigest()


@dataclass
class DedupeResult:
    """Result of deduplication check."""
    is_duplicate: bool
    fingerprint: EventFingerprint
    previous_occurrence: Optional[float] = None  # Timestamp of first occurrence
    occurrences: int = 1  # How many times seen
    audit_marker: Optional[str] = None
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def __repr__(self):
        return (f"DedupeResult(duplicate={self.is_duplicate}, "
                f"occurrences={self.occurrences}, marker={self.audit_marker})")


class EventDeduplicator:
    """Deduplicates events from live feed."""
    
    # Configuration
    WINDOW_SIZE_SECONDS = 60  # Keep 60-second rolling window
    HASH_CACHE_SIZE = 10000  # Max fingerprints in cache
    
    def __init__(self, window_seconds: float = 60):
        """Initialize deduplicator.
        
        Args:
            window_seconds: Rolling window to track duplicates (seconds)
        """
        self.window_seconds = window_seconds
        self.window_ms = int(window_seconds * 1000)
        
        # Cache: (fingerprint_hash -> deque of timestamps)
        self.fingerprint_cache: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)  # Max 100 occurrences per fingerprint
        )
        
        # Per-symbol cache for fast lookup
        self.symbol_cache: Dict[str, Set[str]] = defaultdict(set)
        
        # Statistics
        self.total_events = 0
        self.duplicate_events = 0
        self.unique_events = 0
        self.duplicate_audit_log = []
        self.cache_size = 0
    
    def get_fingerprint(self,
                       timestamp_seconds: float,
                       symbol: str,
                       price: float,
                       size: float,
                       side: str,
                       sequence: int = 0) -> EventFingerprint:
        """Create fingerprint from event data.
        
        Args:
            timestamp_seconds: Event timestamp (seconds since epoch)
            symbol: Symbol
            price: Event price
            size: Event size
            side: BUY or SELL
            sequence: Sequence number from feed
            
        Returns:
            EventFingerprint
        """
        timestamp_ms = int(timestamp_seconds * 1000)
        return EventFingerprint(
            timestamp_ms=timestamp_ms,
            symbol=symbol,
            price=price,
            size=size,
            side=side.upper(),
            sequence=sequence
        )
    
    def check_duplicate(self, fingerprint: EventFingerprint) -> DedupeResult:
        """Check if event is a duplicate.
        
        Args:
            fingerprint: Event fingerprint
            
        Returns:
            DedupeResult with duplicate status
        """
        self.total_events += 1
        
        fingerprint_hash = fingerprint.to_hash()
        now_ms = int(datetime.now().timestamp() * 1000)
        
        # Get recent occurrences of this fingerprint
        occurrences = self.fingerprint_cache[fingerprint_hash]
        
        # Clean old occurrences outside window
        while occurrences and (now_ms - occurrences[0]) > self.window_ms:
            occurrences.popleft()
        
        # Check for duplicate
        if occurrences:
            # This event already appeared in the window
            first_occurrence = occurrences[0]
            self.duplicate_events += 1
            
            # Log duplicate
            self.duplicate_audit_log.append({
                'symbol': fingerprint.symbol,
                'fingerprint': fingerprint.to_key(),
                'first_seen_ms': first_occurrence,
                'duplicate_at_ms': now_ms,
                'occurrences': len(occurrences) + 1,  # Including this one
                'age_ms': now_ms - first_occurrence,
            })
            
            logger.warning(
                f"[{fingerprint.symbol}] Duplicate event detected: "
                f"price={fingerprint.price}, size={fingerprint.size}, "
                f"side={fingerprint.side}, occurred {len(occurrences)} times already"
            )
            
            result = DedupeResult(
                is_duplicate=True,
                fingerprint=fingerprint,
                previous_occurrence=first_occurrence / 1000.0,
                occurrences=len(occurrences) + 1,
                audit_marker=f"DUPLICATE_#{len(occurrences)+1}"
            )
            
            # Add this occurrence
            occurrences.append(now_ms)
            
            return result
        
        # New unique event
        self.unique_events += 1
        occurrences.append(now_ms)
        
        # Update symbol cache
        self.symbol_cache[fingerprint.symbol].add(fingerprint_hash)
        
        # Track cache size
        total_hashes = sum(len(v) for v in self.symbol_cache.values())
        self.cache_size = total_hashes
        
        # Prune if cache gets too large
        if self.cache_size > self.HASH_CACHE_SIZE:
            self._prune_cache()
        
        result = DedupeResult(
            is_duplicate=False,
            fingerprint=fingerprint,
            occurrences=1,
            audit_marker=None
        )
        
        return result
    
    def _prune_cache(self):
        """Remove old entries from cache to limit memory."""
        now_ms = int(datetime.now().timestamp() * 1000)
        pruned = 0
        
        for symbol in list(self.symbol_cache.keys()):
            hashes_to_remove = []
            
            for fp_hash in list(self.symbol_cache[symbol]):
                occurrences = self.fingerprint_cache[fp_hash]
                
                # Clean old occurrences
                while occurrences and (now_ms - occurrences[0]) > self.window_ms:
                    occurrences.popleft()
                
                # Remove if empty
                if not occurrences:
                    hashes_to_remove.append(fp_hash)
                    pruned += 1
            
            for fp_hash in hashes_to_remove:
                self.symbol_cache[symbol].discard(fp_hash)
                if fp_hash in self.fingerprint_cache:
                    del self.fingerprint_cache[fp_hash]
        
        logger.debug(f"Cache pruned: removed {pruned} old entries, "
                    f"size now {self.cache_size}")
    
    def mark_processed(self, fingerprint: EventFingerprint):
        """Mark event as processed (optional tracking)."""
        pass  # Can be used for additional bookkeeping
    
    def get_stats(self) -> dict:
        """Get deduplication statistics."""
        total = self.total_events
        if total == 0:
            return {
                'total_events': 0,
                'unique': 0,
                'duplicates': 0,
                'duplicate_percentage': 0,
                'cache_size': 0,
            }
        
        return {
            'total_events': total,
            'unique': self.unique_events,
            'duplicates': self.duplicate_events,
            'duplicate_percentage': (self.duplicate_events / total * 100),
            'cache_size': self.cache_size,
            'cache_entries': len(self.fingerprint_cache),
        }
    
    def get_symbol_stats(self, symbol: str) -> dict:
        """Get per-symbol deduplication statistics."""
        count = len(self.symbol_cache.get(symbol, set()))
        return {
            'symbol': symbol,
            'unique_fingerprints': count,
        }
    
    def get_duplicate_audit_log(self, limit: int = 100) -> list:
        """Get recent duplicate events from audit log."""
        return self.duplicate_audit_log[-limit:]
    
    def reset_stats(self):
        """Reset statistics."""
        self.total_events = 0
        self.duplicate_events = 0
        self.unique_events = 0
        self.duplicate_audit_log = []


class SequenceTracker:
    """Tracks event sequences to detect gaps and replays."""
    
    def __init__(self):
        self.last_sequence: Dict[str, int] = {}
        self.gaps: Dict[str, list] = defaultdict(list)  # symbol -> list of gaps
        self.replays: Dict[str, int] = defaultdict(int)  # symbol -> replay count
    
    def track_sequence(self, 
                      symbol: str,
                      sequence: int) -> Tuple[bool, Optional[int]]:
        """Track sequence number.
        
        Args:
            symbol: Symbol
            sequence: Sequence number from feed
            
        Returns:
            Tuple of (is_out_of_order, expected_sequence)
        """
        if symbol not in self.last_sequence:
            self.last_sequence[symbol] = sequence
            return False, None
        
        last_seq = self.last_sequence[symbol]
        
        if sequence < last_seq:
            # Out of order or replay
            self.replays[symbol] += 1
            logger.warning(f"[{symbol}] Sequence went backwards: "
                          f"{last_seq} -> {sequence}")
            return True, last_seq + 1
        
        if sequence > last_seq + 1:
            # Gap detected
            gap = sequence - last_seq - 1
            self.gaps[symbol].append({
                'from': last_seq,
                'to': sequence,
                'gap_size': gap,
                'timestamp': datetime.now().timestamp(),
            })
            logger.warning(f"[{symbol}] Sequence gap detected: "
                          f"{last_seq} -> {sequence} (gap={gap})")
        
        self.last_sequence[symbol] = sequence
        return False, None
    
    def get_stats(self, symbol: str) -> dict:
        """Get sequence statistics for a symbol."""
        return {
            'symbol': symbol,
            'last_sequence': self.last_sequence.get(symbol, -1),
            'gap_count': len(self.gaps.get(symbol, [])),
            'replay_count': self.replays.get(symbol, 0),
        }
