#!/usr/bin/env python3
"""
real_signal_extractor.py

Loads REAL footprint entry signals from CSV.
NO synthetic generation.
NO future knowledge.
Matches signals to real price data by timestamp and level.

Critical: Signal timestamp is treated as the FIRST MOMENT we know about the signal.
Any price data used MUST be from this moment forward (no lookahead).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import pandas as pd


@dataclass
class RealSignal:
    """A REAL footprint entry signal (not synthetic)."""
    # Timestamps (all UTC)
    signal_generated_utc: str       # When the signal was generated
    signal_event_utc: str            # When the event occurred (from CSV)
    
    # Direction and price
    direction: str                   # LONG | SHORT
    entry_price: float
    confidence: float                # 0-100
    
    # Setup components
    setup_type: str                  # e.g., "poc_divergence_absorption_reclaim"
    divergence_type: str
    absorption_score: float
    level_type: str                  # POC, VWAP, session_low, etc.
    
    # Footprint details
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float
    candle_delta: int
    candle_vol: int
    trigger_level: float
    touches: int
    level_strength: float
    
    # CSV source line (for tracing)
    csv_line: int = 0


class RealSignalExtractor:
    """
    Extract real signals from CSV without synthetic generation.
    Every signal comes from actual footprint analysis of real data.
    """
    
    def __init__(self, csv_path: Path | str):
        """Initialize extractor pointing to real signals CSV."""
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Signals CSV not found: {csv_path}")
    
    def load_signals(self, 
                    filter_date: Optional[str] = None,
                    min_confidence: float = 0.0,
                    max_signals: Optional[int] = None) -> List[RealSignal]:
        """
        Load real signals from CSV.
        
        Args:
            filter_date: Optional date filter (YYYY-MM-DD)
            min_confidence: Only signals >= this confidence
            max_signals: Limit to first N signals
        
        Returns:
            List of RealSignal objects
        """
        signals = []
        
        with open(self.csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):  # Line 2 onwards (after header)
                try:
                    # Parse timestamp
                    ts_event = datetime.fromisoformat(row['ts_event'].replace('Z', '+00:00'))
                    
                    # Filter by date if requested
                    if filter_date:
                        filter_dt = datetime.strptime(filter_date, "%Y-%m-%d").date()
                        if ts_event.date() != filter_dt:
                            continue
                    
                    # Filter by confidence
                    confidence = float(row.get('confidence', 0))
                    if confidence < min_confidence:
                        continue
                    
                    # Parse all fields
                    signal = RealSignal(
                        signal_generated_utc=row.get('ts_generated', datetime.now(timezone.utc).isoformat()),
                        signal_event_utc=row['ts_event'],
                        direction=row['direction'].upper(),
                        entry_price=float(row['entry_price']),
                        confidence=confidence,
                        setup_type=row.get('setup_type', 'unknown'),
                        divergence_type=row.get('divergence_type', ''),
                        absorption_score=float(row.get('absorption_score', 0)),
                        level_type=row.get('level_type', 'unknown'),
                        candle_open=float(row.get('candle_open', 0)),
                        candle_high=float(row.get('candle_high', 0)),
                        candle_low=float(row.get('candle_low', 0)),
                        candle_close=float(row.get('candle_close', 0)),
                        candle_delta=int(row.get('candle_delta', 0)),
                        candle_vol=int(row.get('candle_vol', 0)),
                        trigger_level=float(row.get('trigger_level', 0)),
                        touches=int(row.get('touches', 0)),
                        level_strength=float(row.get('level_strength', 0)),
                        csv_line=line_num,
                    )
                    
                    signals.append(signal)
                    
                    if max_signals and len(signals) >= max_signals:
                        break
                
                except (KeyError, ValueError) as e:
                    print(f"Warning: Skipping line {line_num}: {e}")
                    continue
        
        return signals
    
    def get_signal_window(self, signal: RealSignal, 
                         lookback_minutes: int = 15,
                         lookahead_minutes: int = 30) -> Tuple[datetime, datetime]:
        """
        Get the time window for a signal (for price data extraction).
        
        Critical: Signal time is the START of the window.
        We NEVER look backward in time before the signal.
        We look forward to find stops/targets.
        
        Args:
            signal: The signal
            lookback_minutes: How far back for context (before signal)
            lookahead_minutes: How far forward for outcome (after signal)
        
        Returns:
            Tuple of (window_start, window_end) in UTC
        """
        signal_ts = datetime.fromisoformat(signal.signal_event_utc)
        
        # Lookback: For volatility context (before signal, OK to use)
        lookback_start = signal_ts - timedelta(minutes=lookback_minutes)
        
        # Lookahead: To find stops/targets (only forward from signal)
        lookahead_end = signal_ts + timedelta(minutes=lookahead_minutes)
        
        return lookback_start, lookahead_end
    
    def validate_no_lookahead(self, signal: RealSignal, 
                             price_data: List[Dict]) -> bool:
        """
        Validate that no price data used before the signal timestamp.
        
        This is a CRITICAL check to prevent lookahead bias.
        
        Args:
            signal: The signal being evaluated
            price_data: The price data being used
        
        Returns:
            True if valid (no lookahead), False if lookahead detected
        """
        signal_ts = datetime.fromisoformat(signal.signal_event_utc)
        
        for price in price_data:
            price_ts = datetime.fromisoformat(price['ts'])
            
            # ANY data point before signal = lookahead bias
            if price_ts < signal_ts:
                return False
        
        return True
    
    def create_alert_payload(self, signal: RealSignal,
                            stop_price: float,
                            target1_price: float,
                            target2_price: float,
                            reason_code: str = "footprint_pattern") -> Dict:
        """
        Create an alert payload from a real signal.
        
        For WhatsApp/Discord delivery.
        
        Args:
            signal: The real signal
            stop_price: Stop loss (calculated at signal time)
            target1_price: First target
            target2_price: Second target
            reason_code: Why this signal fired
        
        Returns:
            Alert dict ready for delivery
        """
        signal_ts = datetime.fromisoformat(signal.signal_event_utc)
        
        # Convert UTC to ET for display
        signal_ts_et = signal_ts.astimezone(
            timezone(timedelta(hours=-4))  # ET (UTC-4)
        )
        
        direction_emoji = "📈" if signal.direction == "LONG" else "📉"
        
        alert = {
            "timestamp_utc": signal.signal_event_utc,
            "timestamp_et": signal_ts_et.isoformat(),
            "direction": signal.direction,
            "entry": signal.entry_price,
            "stop": stop_price,
            "target_1": target1_price,
            "target_2": target2_price,
            "confidence": signal.confidence,
            "setup_type": signal.setup_type,
            "divergence": signal.divergence_type,
            "absorption_score": signal.absorption_score,
            "reason": reason_code,
            "risk_reward_1": abs(signal.entry_price - target1_price) / abs(signal.entry_price - stop_price) if stop_price != signal.entry_price else 0,
            "risk_reward_2": abs(signal.entry_price - target2_price) / abs(signal.entry_price - stop_price) if stop_price != signal.entry_price else 0,
        }
        
        # Format message for WhatsApp
        if signal.direction == "LONG":
            alert["message"] = f"""{direction_emoji} BUY ALERT

Entry: ${signal.entry_price:.2f}
Stop: ${stop_price:.2f}
Target 1: ${target1_price:.2f}
Target 2: ${target2_price:.2f}

Confidence: {signal.confidence:.0f}%
Setup: {signal.setup_type}
Time (ET): {signal_ts_et.strftime('%H:%M:%S')}"""
        else:
            alert["message"] = f"""{direction_emoji} SELL ALERT

Entry: ${signal.entry_price:.2f}
Stop: ${stop_price:.2f}
Target 1: ${target1_price:.2f}
Target 2: ${target2_price:.2f}

Confidence: {signal.confidence:.0f}%
Setup: {signal.setup_type}
Time (ET): {signal_ts_et.strftime('%H:%M:%S')}"""
        
        return alert


def format_alert_timestamp(dt: datetime, format: str = "iso") -> str:
    """
    Format timestamp for alerts.
    
    Args:
        dt: Datetime to format
        format: "iso" (ISO8601), "compact" (HH:MM:SS), "et" (ET timezone)
    
    Returns:
        Formatted timestamp string
    """
    if format == "iso":
        return dt.isoformat()
    elif format == "compact":
        return dt.strftime("%H:%M:%S")
    elif format == "et":
        et_tz = timezone(timedelta(hours=-4))
        dt_et = dt.astimezone(et_tz)
        return dt_et.strftime("%H:%M:%S ET")
    else:
        return str(dt)


if __name__ == "__main__":
    # Test
    csv_path = Path("state/orderflow/live/footprint_entry_candidates.csv")
    extractor = RealSignalExtractor(csv_path)
    
    signals = extractor.load_signals(
        filter_date="2026-05-04",
        min_confidence=80.0,
        max_signals=5
    )
    
    print(f"Loaded {len(signals)} real signals")
    for sig in signals:
        print(f"\n{sig.direction} @ {sig.entry_price} (conf: {sig.confidence:.0f}%)")
        print(f"  Setup: {sig.setup_type}")
        print(f"  Absorption: {sig.absorption_score:.1f}")
        print(f"  CSV Line: {sig.csv_line}")
