"""
Core data types for live orderflow alert service.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
from datetime import datetime
import uuid


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class RegimeType(Enum):
    """Market regime types."""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"


class AlertType(Enum):
    """Alert classification types."""
    ABSORPTION = "ABSORPTION"
    FOLLOW_THROUGH = "FOLLOW_THROUGH"
    REGIME_CHANGE = "REGIME_CHANGE"
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    BREAKDOWN_CONFIRMED = "BREAKDOWN_CONFIRMED"


class AlertSeverity(Enum):
    """Alert severity levels."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class PriceLevelData:
    """Data for a single price level."""
    price: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    bid_orders: int = 0
    ask_orders: int = 0
    cumulative_delta: float = 0.0
    
    @property
    def total_volume(self) -> float:
        return self.bid_volume + self.ask_volume
    
    @property
    def delta(self) -> float:
        return self.bid_volume - self.ask_volume


@dataclass
class OrderFlowEvent:
    """Single order flow event (order or trade)."""
    timestamp: float  # Unix timestamp in seconds
    symbol: str
    price: float
    size: float
    side: OrderSide
    order_id: Optional[str] = None
    is_market_order: bool = False
    signature: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def __hash__(self):
        return hash(self.signature)


@dataclass
class BarData:
    """OHLCV bar with orderflow information."""
    timestamp: float  # Unix timestamp
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    delta: float = 0.0
    
    # Orderflow features
    price_levels: dict = field(default_factory=dict)  # price -> PriceLevelData
    large_orders: List[OrderFlowEvent] = field(default_factory=list)
    
    @property
    def cumulative_delta(self) -> float:
        return self.bid_volume - self.ask_volume
    
    @property
    def time_as_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)


@dataclass
class AbsorptionSignal:
    """Absorption detection result."""
    bar: BarData
    side: OrderSide
    absorbed_volume: float
    absorption_orders: List[OrderFlowEvent]
    ratio: float  # absorbed_volume / total_bar_volume
    confidence: float  # 0.0 to 1.0
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def __hash__(self):
        return hash((self.bar.timestamp, self.bar.symbol, hash(self.side)))


@dataclass
class FollowThroughConfirmation:
    """Follow-through confirmation signal."""
    initial_absorption: AbsorptionSignal
    confirmations: List[AbsorptionSignal] = field(default_factory=list)
    total_volume_ratio: float = 0.0
    confirmation_count: int = 0
    confidence: float = 0.0
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class RegimeState:
    """Current market regime state."""
    regime_type: RegimeType
    trend_strength: float  # 0.0 to 1.0
    volatility: float
    support_price: float
    resistance_price: float
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class OrderFlowAlert:
    """Final alert to be sent."""
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    alert_type: AlertType = AlertType.ABSORPTION
    severity: AlertSeverity = AlertSeverity.MEDIUM
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    price: float = 0.0
    volume: float = 0.0
    regime: Optional[RegimeState] = None
    absorption_signal: Optional[AbsorptionSignal] = None
    followthrough: Optional[FollowThroughConfirmation] = None
    message: str = ""
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    # Delivery tracking
    whatsapp_sent: bool = False
    email_sent: bool = False
    sent_at: Optional[float] = None
    
    def format_for_whatsapp(self) -> str:
        """Format alert for WhatsApp delivery."""
        lines = [
            f"🚨 {self.alert_type.value}",
            f"Symbol: {self.symbol}",
            f"Side: {self.side.value}",
            f"Price: ${self.price:.2f}",
            f"Volume: {self.volume:,.0f}",
            f"Severity: {self.severity.name}",
        ]
        
        if self.regime:
            lines.append(f"Regime: {self.regime.regime_type.value}")
            lines.append(f"Volatility: {self.regime.volatility:.2f}")
        
        if self.message:
            lines.append(f"Note: {self.message}")
        
        lines.append(f"⏰ {datetime.fromtimestamp(self.timestamp).strftime('%H:%M:%S')}")
        
        return "\n".join(lines)
    
    def __hash__(self):
        return hash(self.alert_id)


@dataclass
class AlertStats:
    """Statistics for alert generation."""
    total_alerts: int = 0
    by_type: dict = field(default_factory=dict)  # AlertType -> count
    by_severity: dict = field(default_factory=dict)  # AlertSeverity -> count
    by_symbol: dict = field(default_factory=dict)  # symbol -> count
    whatsapp_sent: int = 0
    email_sent: int = 0
    last_alert_timestamp: Optional[float] = None
