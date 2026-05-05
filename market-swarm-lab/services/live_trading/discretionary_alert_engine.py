#!/usr/bin/env python3
"""
Discretionary Alert Engine: Lightweight live observation system

Generates alerts for MANUAL review only.
NO auto-execution.
NO broker connections.
For observational validation only.

Alert conditions:
1. Regime filter (not chop, not dead tape)
2. Absorption detected
3. Reclaim/reject begins
4. Follow-through confirmed (REQUIRED)
"""

import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict

@dataclass
class SignalAlert:
    """Alert signal with full context"""
    
    # Timing
    timestamp_utc: str
    timestamp_et: str
    
    # Symbol & Direction
    symbol: str
    direction: str  # LONG/SHORT
    
    # Prices
    entry_price: float
    stop_price: float
    target1_price: float
    target2_price: float
    
    # Metrics
    confidence: float  # 0-100
    displacement_ticks: float
    delta_acceleration: str  # 'strong', 'moderate', 'weak'
    mfe_potential: float  # estimate
    mae_risk: float  # estimate
    
    # Context
    regime: str  # 'trend', 'opening', 'balance', 'close'
    reason_codes: list  # ['absorption', 'reclaim', 'delta_accel', 'breakout']
    absorption_price: float
    reclaim_price: float
    follow_through_quality: str  # 'strong', 'moderate', 'weak'
    
    # Trade quality
    trade_setup: str  # 'mechanical', 'discretionary'
    
    # For filtering
    alert_id: str = ""
    
    def __post_init__(self):
        if not self.alert_id:
            self.alert_id = f"{self.timestamp_utc}_{self.symbol}_{self.direction}"
    
    def format_whatsapp(self):
        """Format for WhatsApp alert"""
        symbol_emoji = "🟢" if self.direction == "LONG" else "🔴"
        
        msg = f"""{symbol_emoji} {self.direction} {self.symbol}

{self.timestamp_et} ET

Entry: {self.entry_price:.2f}
Stop: {self.stop_price:.2f}
T1: {self.target1_price:.2f}
T2: {self.target2_price:.2f}

Confidence: {self.confidence:.0f}%
Displacement: {self.displacement_ticks:.2f}t
Delta: {self.delta_acceleration}

Regime: {self.regime}
Follow-through: {self.follow_through_quality}"""
        
        return msg
    
    def to_dict(self):
        return asdict(self)


class DiscretionaryAlertEngine:
    """Generate alerts for manual review"""
    
    def __init__(self, alert_dir="/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live"):
        self.alert_dir = Path(alert_dir)
        self.alert_dir.mkdir(parents=True, exist_ok=True)
        
        self.alerts = []
        self.alert_log = self.alert_dir / "live_alerts.csv"
        self.latest_signal = self.alert_dir / "latest_signal.json"
        self.heartbeat_file = self.alert_dir / "heartbeat.json"
        
        # Initialize CSV with headers
        self._init_csv()
    
    def _init_csv(self):
        """Initialize alerts CSV with headers"""
        if not self.alert_log.exists():
            with open(self.alert_log, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp_utc', 'timestamp_et', 'symbol', 'direction',
                    'entry_price', 'stop_price', 'target1_price', 'target2_price',
                    'confidence', 'displacement_ticks', 'delta_acceleration',
                    'regime', 'reason_codes', 'follow_through_quality', 'alert_id'
                ])
                writer.writeheader()
    
    def validate_alert(self, signal_data):
        """
        Validate signal meets ALL alert conditions.
        
        Returns: (valid, reason)
        """
        
        # Check 1: Regime filter
        regime = signal_data.get('regime', 'unknown')
        if regime in ['balance', 'chop', 'dead_tape', 'close']:
            return False, f"Regime not tradeable: {regime}"
        
        # Check 2: Absorption detected
        if not signal_data.get('absorption_detected'):
            return False, "No absorption detected"
        
        # Check 3: Reclaim/reject started
        if not signal_data.get('reclaim_started'):
            return False, "No reclaim initiated"
        
        # Check 4: REQUIRED - Follow-through confirmed
        if not signal_data.get('follow_through_confirmed'):
            return False, "No follow-through (CRITICAL gate)"
        
        # Check 5: Minimum displacement
        displacement = signal_data.get('displacement_ticks', 0)
        if displacement < 1.5:
            return False, f"Displacement too small: {displacement:.2f}t"
        
        return True, "All conditions met"
    
    def create_alert(self, signal_data):
        """
        Create an alert from signal data.
        
        signal_data must have:
        - timestamp_utc, timestamp_et
        - symbol, direction
        - entry_price, stop_price, target1_price, target2_price
        - confidence (0-100)
        - displacement_ticks
        - delta_acceleration ('strong', 'moderate', 'weak')
        - regime
        - reason_codes (list)
        - absorption_price, reclaim_price
        - follow_through_quality
        """
        
        # Validate
        valid, reason = self.validate_alert(signal_data)
        if not valid:
            return None, reason
        
        # Create alert
        alert = SignalAlert(
            timestamp_utc=signal_data['timestamp_utc'],
            timestamp_et=signal_data['timestamp_et'],
            symbol=signal_data['symbol'],
            direction=signal_data['direction'],
            entry_price=signal_data['entry_price'],
            stop_price=signal_data['stop_price'],
            target1_price=signal_data['target1_price'],
            target2_price=signal_data['target2_price'],
            confidence=signal_data['confidence'],
            displacement_ticks=signal_data['displacement_ticks'],
            delta_acceleration=signal_data['delta_acceleration'],
            mfe_potential=signal_data.get('mfe_potential', 0),
            mae_risk=signal_data.get('mae_risk', 0),
            regime=signal_data['regime'],
            reason_codes=signal_data.get('reason_codes', []),
            absorption_price=signal_data['absorption_price'],
            reclaim_price=signal_data['reclaim_price'],
            follow_through_quality=signal_data['follow_through_quality'],
            trade_setup='manual',  # Always manual for discretionary
        )
        
        return alert, "Created"
    
    def emit_alert(self, alert):
        """
        Emit an alert: save to CSV, update latest JSON, return formatted message.
        
        Returns: (whatsapp_message, json_data)
        """
        
        # Save to CSV
        with open(self.alert_log, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp_utc', 'timestamp_et', 'symbol', 'direction',
                'entry_price', 'stop_price', 'target1_price', 'target2_price',
                'confidence', 'displacement_ticks', 'delta_acceleration',
                'regime', 'reason_codes', 'follow_through_quality', 'alert_id'
            ])
            writer.writerow({
                'timestamp_utc': alert.timestamp_utc,
                'timestamp_et': alert.timestamp_et,
                'symbol': alert.symbol,
                'direction': alert.direction,
                'entry_price': alert.entry_price,
                'stop_price': alert.stop_price,
                'target1_price': alert.target1_price,
                'target2_price': alert.target2_price,
                'confidence': alert.confidence,
                'displacement_ticks': alert.displacement_ticks,
                'delta_acceleration': alert.delta_acceleration,
                'regime': alert.regime,
                'reason_codes': ','.join(alert.reason_codes),
                'follow_through_quality': alert.follow_through_quality,
                'alert_id': alert.alert_id,
            })
        
        # Update latest JSON
        with open(self.latest_signal, 'w') as f:
            json.dump({
                'alert': alert.to_dict(),
                'message': alert.format_whatsapp(),
                'timestamp': alert.timestamp_utc,
            }, f, indent=2)
        
        # Format message
        msg = alert.format_whatsapp()
        
        return msg, alert.to_dict()
    
    def heartbeat(self):
        """Update heartbeat (called every 30s)"""
        with open(self.heartbeat_file, 'w') as f:
            json.dump({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'alerts_logged': len(self.alerts),
                'status': 'monitoring',
            }, f)
    
    def get_alert_count(self):
        """Get number of alerts logged"""
        if not self.alert_log.exists():
            return 0
        with open(self.alert_log) as f:
            return len(f.readlines()) - 1  # Exclude header


# Example usage
if __name__ == "__main__":
    engine = DiscretionaryAlertEngine()
    
    # Example signal
    signal = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'timestamp_et': datetime.now(timezone.utc).strftime('%H:%M:%S'),
        'symbol': 'ESM6',
        'direction': 'LONG',
        'entry_price': 7234.25,
        'stop_price': 7231.75,
        'target1_price': 7238.25,
        'target2_price': 7242.25,
        'confidence': 82.0,
        'displacement_ticks': 3.25,
        'delta_acceleration': 'strong',
        'mfe_potential': 4.5,
        'mae_risk': 2.5,
        'regime': 'trend',
        'reason_codes': ['absorption', 'reclaim', 'delta_accel', 'breakout'],
        'absorption_price': 7232.0,
        'reclaim_price': 7233.5,
        'follow_through_quality': 'strong',
        'follow_through_confirmed': True,  # REQUIRED
        'absorption_detected': True,
        'reclaim_started': True,
    }
    
    # Validate
    alert, msg = engine.create_alert(signal)
    if alert:
        print(f"[✓] Alert created: {msg}")
        whatsapp, json_data = engine.emit_alert(alert)
        print(f"\n{whatsapp}\n")
        print(f"[✓] Saved to {engine.alert_log}")
    else:
        print(f"[✗] Alert rejected: {msg}")
