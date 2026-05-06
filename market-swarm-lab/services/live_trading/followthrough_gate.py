"""
Follow-through confirmation gate - validates absorption signals.
"""

import logging
from typing import Optional, List, Dict, Deque
from collections import deque
from dataclasses import dataclass

from data_types import (
    AbsorptionSignal, FollowThroughConfirmation, OrderSide
)

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationState:
    """State for tracking confirmations."""
    initial_signal: AbsorptionSignal
    confirmations: List[AbsorptionSignal]
    total_volume_ratio: float
    confirmation_count: int


class FollowThroughGate:
    """
    Validates absorption signals with follow-through confirmation.
    
    Follow-through confirmation means:
    1. Initial absorption is detected
    2. Subsequent bars show continued absorption on same side
    3. Volume accumulation confirms the signal
    """
    
    def __init__(self, time_window_ms: int = 5000,
                 min_confirmation_count: int = 2,
                 min_volume_ratio: float = 0.4):
        """
        Initialize follow-through gate.
        
        Args:
            time_window_ms: Time window for confirmations
            min_confirmation_count: Minimum number of confirmation bars
            min_volume_ratio: Minimum volume ratio across confirmations
        """
        self.time_window_ms = time_window_ms
        self.min_confirmation_count = min_confirmation_count
        self.min_volume_ratio = min_volume_ratio
        
        # Track pending confirmations
        self.pending_signals: Dict[str, Deque[ConfirmationState]] = {}
    
    def submit_absorption(self, signal: AbsorptionSignal) -> Optional[FollowThroughConfirmation]:
        """
        Submit absorption signal for confirmation tracking.
        
        Returns:
            Confirmed signal if it passes gates, None otherwise.
        """
        symbol = signal.bar.symbol
        
        if symbol not in self.pending_signals:
            self.pending_signals[symbol] = deque(maxlen=100)
        
        # Check if this signal confirms any pending signals
        confirmed = self._check_confirmations(signal, symbol)
        
        # Add this signal as a new pending signal
        new_state = ConfirmationState(
            initial_signal=signal,
            confirmations=[],
            total_volume_ratio=signal.ratio,
            confirmation_count=1
        )
        self.pending_signals[symbol].append(new_state)
        
        return confirmed
    
    def _check_confirmations(self, signal: AbsorptionSignal,
                            symbol: str) -> Optional[FollowThroughConfirmation]:
        """
        Check if this signal confirms any pending signals.
        
        For a confirmation to be valid:
        1. Same symbol
        2. Same side (buy or sell)
        3. Within time window
        4. Adequate volume ratio
        """
        
        if symbol not in self.pending_signals:
            return None
        
        pending = list(self.pending_signals[symbol])
        
        for state in pending:
            # Check if compatible
            if not self._is_compatible(state, signal):
                continue
            
            # Add as confirmation
            state.confirmations.append(signal)
            state.total_volume_ratio += signal.ratio
            state.confirmation_count = len(state.confirmations) + 1
            
            # Check if confirmed
            if self._is_confirmed(state):
                # Remove from pending and return as confirmed
                self.pending_signals[symbol].remove(state)
                
                return FollowThroughConfirmation(
                    initial_absorption=state.initial_signal,
                    confirmations=state.confirmations,
                    total_volume_ratio=state.total_volume_ratio,
                    confirmation_count=state.confirmation_count,
                    confidence=self._calculate_confidence(state)
                )
        
        return None
    
    def _is_compatible(self, state: ConfirmationState,
                      signal: AbsorptionSignal) -> bool:
        """Check if signal is compatible with pending state."""
        
        initial = state.initial_signal
        
        # Must be same side
        if initial.side != signal.side:
            return False
        
        # Must be within time window
        time_diff_ms = (signal.bar.timestamp - initial.bar.timestamp) * 1000
        if time_diff_ms > self.time_window_ms:
            return False
        
        # Must not be same bar
        if signal.bar.timestamp == initial.bar.timestamp:
            return False
        
        # Price should be in similar level
        initial_price = initial.bar.close
        signal_price = signal.bar.close
        price_diff_pct = abs(signal_price - initial_price) / initial_price
        
        if price_diff_pct > 0.02:  # More than 2% away
            return False
        
        return True
    
    def _is_confirmed(self, state: ConfirmationState) -> bool:
        """Check if state has enough confirmations."""
        
        # Need minimum confirmations
        if len(state.confirmations) < (self.min_confirmation_count - 1):
            return False
        
        # Need minimum average volume ratio
        avg_ratio = state.total_volume_ratio / state.confirmation_count
        if avg_ratio < self.min_volume_ratio:
            return False
        
        return True
    
    def _calculate_confidence(self, state: ConfirmationState) -> float:
        """
        Calculate confidence score for confirmed signal.
        
        Based on:
        - Number of confirmations
        - Total volume ratio
        - Initial absorption strength
        """
        
        initial = state.initial_signal
        
        # Base confidence from initial signal
        conf = initial.confidence
        
        # Boost for number of confirmations
        conf += len(state.confirmations) * 0.1
        
        # Boost for volume ratio
        avg_ratio = state.total_volume_ratio / state.confirmation_count
        conf += (avg_ratio - self.min_volume_ratio) * 0.2
        
        # Cap at 1.0
        return min(conf, 1.0)
    
    def cleanup_expired(self, current_timestamp: float):
        """Remove expired pending signals."""
        
        for symbol in self.pending_signals:
            pending = list(self.pending_signals[symbol])
            
            alive_states = []
            for state in pending:
                elapsed_ms = (current_timestamp - state.initial_signal.bar.timestamp) * 1000
                if elapsed_ms < self.time_window_ms:
                    alive_states.append(state)
            
            self.pending_signals[symbol] = deque(alive_states, maxlen=100)
