"""
Out-of-order event buffer for live feeds.
Implements Blocker #4: Out-of-order buffer with reordering.

Rules:
- Maintain 50-250ms reorder window
- Buffer out-of-order events
- Emit in timestamp order with bounded memory
- Track reorder statistics
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Deque, Callable
from datetime import datetime
from collections import deque
import time

logger = logging.getLogger(__name__)


@dataclass
class BufferedEvent:
    """Event in the buffer."""
    event_id: str
    timestamp_seconds: float
    timestamp_ms: int
    symbol: str
    event_type: str
    data: dict
    arrival_time: float  # When it arrived at buffer
    
    @property
    def time_in_buffer_ms(self) -> float:
        """How long event has been in buffer (milliseconds)."""
        return (datetime.now().timestamp() - self.arrival_time) * 1000


@dataclass
class ReorderResult:
    """Result of reordering operation."""
    ordered_events: List[BufferedEvent]
    events_reordered: int
    max_reorder_delay_ms: float
    buffer_size_after: int


class OutOfOrderBuffer:
    """Buffer for handling out-of-order events."""
    
    # Configuration
    MIN_WINDOW_MS = 50  # Minimum reorder window
    MAX_WINDOW_MS = 250  # Maximum reorder window
    DEFAULT_WINDOW_MS = 100  # Default reorder window
    MAX_BUFFER_SIZE = 10000  # Max events in buffer (safety)
    
    def __init__(self,
                 reorder_window_ms: float = 100,
                 max_buffer_size: int = 10000):
        """Initialize buffer.
        
        Args:
            reorder_window_ms: Time window to wait for reordering (ms)
            max_buffer_size: Maximum buffer size before forcing flush
        """
        # Clamp window to valid range
        self.reorder_window_ms = max(
            self.MIN_WINDOW_MS,
            min(reorder_window_ms, self.MAX_WINDOW_MS)
        )
        self.max_buffer_size = max_buffer_size
        
        # Buffers per symbol
        self.buffers: dict = {}  # symbol -> deque of BufferedEvent
        self.last_ordered_timestamp: dict = {}  # symbol -> last ordered timestamp
        
        # Statistics
        self.events_buffered = 0
        self.events_reordered = 0
        self.events_emitted = 0
        self.buffer_overflows = 0
        self.max_buffer_depth = 0
        
        # Callbacks
        self.on_ordered_events: List[Callable] = []
    
    def add_event(self,
                  event_id: str,
                  timestamp_seconds: float,
                  symbol: str,
                  event_type: str,
                  data: dict) -> bool:
        """Add event to buffer.
        
        Args:
            event_id: Unique event ID
            timestamp_seconds: Event timestamp (seconds since epoch)
            symbol: Symbol
            event_type: Type of event (trade, depth, etc.)
            data: Event data dictionary
            
        Returns:
            True if buffered successfully
        """
        self.events_buffered += 1
        
        # Initialize buffer for symbol if needed
        if symbol not in self.buffers:
            self.buffers[symbol] = deque()
            self.last_ordered_timestamp[symbol] = 0
        
        # Create buffered event
        timestamp_ms = int(timestamp_seconds * 1000)
        buffered = BufferedEvent(
            event_id=event_id,
            timestamp_seconds=timestamp_seconds,
            timestamp_ms=timestamp_ms,
            symbol=symbol,
            event_type=event_type,
            data=data,
            arrival_time=datetime.now().timestamp()
        )
        
        # Add to buffer
        buffer = self.buffers[symbol]
        buffer.append(buffered)
        
        # Track max depth
        self.max_buffer_depth = max(self.max_buffer_depth, len(buffer))
        
        # Check for overflow
        if len(buffer) > self.max_buffer_size:
            self.buffer_overflows += 1
            logger.warning(
                f"[{symbol}] Buffer overflow: {len(buffer)} events, "
                f"forcing flush of oldest {len(buffer)//2} events"
            )
            # Force flush oldest events
            self._force_flush_oldest(symbol, len(buffer) // 2)
        
        return True
    
    def try_emit_ordered(self, symbol: str) -> ReorderResult:
        """Try to emit ordered events from buffer.
        
        Args:
            symbol: Symbol to check
            
        Returns:
            ReorderResult with ordered events
        """
        if symbol not in self.buffers:
            return ReorderResult(
                ordered_events=[],
                events_reordered=0,
                max_reorder_delay_ms=0,
                buffer_size_after=0
            )
        
        buffer = self.buffers[symbol]
        now_ms = int(datetime.now().timestamp() * 1000)
        
        # Sort buffer by timestamp
        sorted_events = sorted(buffer, key=lambda e: e.timestamp_ms)
        
        # Find events that are old enough to emit
        ordered_events = []
        reordered_count = 0
        max_delay = 0
        
        for event in sorted_events:
            time_in_buffer = now_ms - event.timestamp_ms
            
            # Only emit if older than reorder window
            if time_in_buffer >= self.reorder_window_ms:
                # Check if out of order
                if event.timestamp_seconds < self.last_ordered_timestamp[symbol]:
                    reordered_count += 1
                    logger.debug(
                        f"[{symbol}] Reordered event: "
                        f"prev={self.last_ordered_timestamp[symbol]:.3f}, "
                        f"curr={event.timestamp_seconds:.3f}"
                    )
                
                ordered_events.append(event)
                self.last_ordered_timestamp[symbol] = event.timestamp_seconds
                max_delay = max(max_delay, time_in_buffer)
            else:
                # Too recent, keep in buffer
                break
        
        # Remove emitted events from buffer
        for event in ordered_events:
            buffer.remove(event)
        
        # Update statistics
        self.events_reordered += reordered_count
        self.events_emitted += len(ordered_events)
        
        if reordered_count > 0:
            logger.info(
                f"[{symbol}] Emitted {len(ordered_events)} events, "
                f"{reordered_count} were out of order, max_delay={max_delay:.1f}ms"
            )
        
        result = ReorderResult(
            ordered_events=ordered_events,
            events_reordered=reordered_count,
            max_reorder_delay_ms=max_delay,
            buffer_size_after=len(buffer)
        )
        
        return result
    
    def _force_flush_oldest(self, symbol: str, count: int):
        """Force emit oldest events (for overflow handling)."""
        if symbol not in self.buffers:
            return
        
        buffer = self.buffers[symbol]
        
        # Sort and take oldest
        sorted_events = sorted(buffer, key=lambda e: e.timestamp_ms)
        to_flush = sorted_events[:count]
        
        for event in to_flush:
            buffer.remove(event)
        
        logger.warning(
            f"[{symbol}] Force flushed {count} events due to buffer overflow"
        )
    
    def flush_all(self, symbol: str) -> ReorderResult:
        """Flush all buffered events for a symbol.
        
        Args:
            symbol: Symbol to flush
            
        Returns:
            ReorderResult with all buffered events
        """
        if symbol not in self.buffers:
            return ReorderResult(
                ordered_events=[],
                events_reordered=0,
                max_reorder_delay_ms=0,
                buffer_size_after=0
            )
        
        buffer = self.buffers[symbol]
        
        # Sort all events
        sorted_events = sorted(buffer, key=lambda e: e.timestamp_ms)
        
        # Count out of order
        reordered_count = 0
        for event in sorted_events:
            if event.timestamp_seconds < self.last_ordered_timestamp[symbol]:
                reordered_count += 1
        
        self.events_reordered += reordered_count
        self.events_emitted += len(sorted_events)
        
        # Clear buffer
        buffer.clear()
        
        logger.info(
            f"[{symbol}] Flushed all {len(sorted_events)} events, "
            f"{reordered_count} were out of order"
        )
        
        return ReorderResult(
            ordered_events=sorted_events,
            events_reordered=reordered_count,
            max_reorder_delay_ms=0,
            buffer_size_after=0
        )
    
    def get_buffer_depth(self, symbol: str) -> int:
        """Get current buffer depth for a symbol."""
        if symbol not in self.buffers:
            return 0
        return len(self.buffers[symbol])
    
    def get_stats(self) -> dict:
        """Get buffer statistics."""
        total_in_buffers = sum(len(b) for b in self.buffers.values())
        
        return {
            'events_buffered': self.events_buffered,
            'events_reordered': self.events_reordered,
            'events_emitted': self.events_emitted,
            'buffer_overflows': self.buffer_overflows,
            'max_buffer_depth': self.max_buffer_depth,
            'current_total_buffered': total_in_buffers,
            'reorder_window_ms': self.reorder_window_ms,
        }
    
    def get_symbol_depth(self) -> dict:
        """Get per-symbol buffer depths."""
        return {
            symbol: len(buffer)
            for symbol, buffer in self.buffers.items()
        }
    
    def reset_stats(self):
        """Reset statistics."""
        self.events_buffered = 0
        self.events_reordered = 0
        self.events_emitted = 0
        self.buffer_overflows = 0
        self.max_buffer_depth = 0
