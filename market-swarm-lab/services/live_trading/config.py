"""
Configuration management for live orderflow alert service.
Loads from .env and provides type-safe config objects.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)


@dataclass
class RithmicConfig:
    """Rithmic feed connection configuration."""
    username: str
    password: str
    account_id: str
    environment: str = "paper"  # 'live' or 'paper'
    socket_port: int = 8000
    
    def validate(self):
        if not self.username:
            raise ValueError("RITHMIC_USERNAME is required")
        if not self.password:
            raise ValueError("RITHMIC_PASSWORD is required")
        if self.environment not in ("live", "paper"):
            raise ValueError("RITHMIC_ENVIRONMENT must be 'live' or 'paper'")


@dataclass
class BookmapConfig:
    """Bookmap API connection configuration."""
    api_key: str
    host: str = "localhost"
    port: int = 9000
    symbols: list = field(default_factory=lambda: ["ES", "NQ", "GC"])
    depth_levels: int = 20
    
    def validate(self):
        if not self.api_key:
            logger.warning("BOOKMAP_API_KEY not configured")
        if not self.symbols:
            raise ValueError("BOOKMAP_SYMBOLS cannot be empty")


@dataclass
class AlertConfig:
    """Alert generation configuration."""
    min_order_size: float = 100
    min_absorption_size: float = 500
    min_follow_through: float = 300
    regime_threshold_usd: float = 10000
    whatsapp_enabled: bool = True
    email_enabled: bool = False
    
    def validate(self):
        if self.min_order_size < 0:
            raise ValueError("ALERT_MIN_ORDER_SIZE must be >= 0")
        if self.min_absorption_size < self.min_order_size:
            raise ValueError("ALERT_MIN_ABSORPTION_SIZE must be >= ALERT_MIN_ORDER_SIZE")


@dataclass
class WhatsAppConfig:
    """WhatsApp/Twilio configuration."""
    account_sid: str
    auth_token: str
    phone_number: str
    recipient_phone: str
    enabled: bool = True
    
    def validate(self):
        if self.enabled:
            if not self.account_sid:
                raise ValueError("TWILIO_ACCOUNT_SID is required when WhatsApp is enabled")
            if not self.auth_token:
                raise ValueError("TWILIO_AUTH_TOKEN is required when WhatsApp is enabled")
            if not self.phone_number:
                raise ValueError("TWILIO_PHONE_NUMBER is required when WhatsApp is enabled")


@dataclass
class RegimeConfig:
    """Regime detection configuration."""
    ma_short: int = 5
    ma_long: int = 20
    atr_period: int = 14
    volatility_threshold: float = 0.02
    
    def validate(self):
        if self.ma_short >= self.ma_long:
            raise ValueError("Short MA must be less than Long MA")


@dataclass
class AbsorptionConfig:
    """Absorption detection configuration."""
    time_window_ms: int = 2000
    delta_min_ratio: float = 0.5  # min ratio of delta to absorbed volume
    volume_min_pct: float = 0.3   # min % of bar volume
    
    def validate(self):
        if not (0 < self.delta_min_ratio <= 1):
            raise ValueError("delta_min_ratio must be between 0 and 1")


@dataclass
class FollowThroughConfig:
    """Follow-through confirmation configuration."""
    time_window_ms: int = 5000
    min_confirmation_count: int = 2
    min_volume_ratio: float = 0.4
    
    def validate(self):
        if self.min_confirmation_count < 1:
            raise ValueError("min_confirmation_count must be >= 1")


@dataclass
class ServiceConfig:
    """Core service configuration."""
    log_level: str = "INFO"
    tick_interval_ms: int = 100
    history_depth: int = 1000
    redis_url: str = "redis://localhost:6379/0"
    debug_mode: bool = False
    mock_feed_mode: bool = False
    
    def validate(self):
        if self.tick_interval_ms < 10:
            raise ValueError("tick_interval_ms must be >= 10")


@dataclass
class MonitorConfig:
    """Monitoring and metrics configuration."""
    enabled: bool = True
    metrics_port: int = 8888
    alert_queue_max_size: int = 1000
    error_log_path: str = "./logs/errors.log"
    
    def validate(self):
        if self.metrics_port < 1024 or self.metrics_port > 65535:
            raise ValueError("metrics_port must be between 1024 and 65535")


@dataclass
class Config:
    """Master configuration object."""
    rithmic: RithmicConfig
    bookmap: BookmapConfig
    alert: AlertConfig
    whatsapp: WhatsAppConfig
    regime: RegimeConfig
    absorption: AbsorptionConfig
    followthrough: FollowThroughConfig
    service: ServiceConfig
    monitor: MonitorConfig
    
    def validate(self):
        """Validate all sub-configurations."""
        self.rithmic.validate()
        self.bookmap.validate()
        self.alert.validate()
        self.whatsapp.validate()
        self.regime.validate()
        self.absorption.validate()
        self.followthrough.validate()
        self.service.validate()
        self.monitor.validate()


def load_config(env_file: Optional[str] = None) -> Config:
    """
    Load configuration from environment variables.
    
    Args:
        env_file: Optional path to .env file. If not provided, looks for .env in cwd.
    
    Returns:
        Config object with all sub-configurations.
    """
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()
    
    # Parse Bookmap symbols
    bookmap_symbols = os.getenv("BOOKMAP_SYMBOLS", "ES,NQ,GC").split(",")
    bookmap_symbols = [s.strip() for s in bookmap_symbols]
    
    config = Config(
        rithmic=RithmicConfig(
            username=os.getenv("RITHMIC_USERNAME", ""),
            password=os.getenv("RITHMIC_PASSWORD", ""),
            account_id=os.getenv("RITHMIC_ACCOUNT_ID", ""),
            environment=os.getenv("RITHMIC_ENVIRONMENT", "paper"),
            socket_port=int(os.getenv("RITHMIC_SOCKET_PORT", "8000")),
        ),
        bookmap=BookmapConfig(
            api_key=os.getenv("BOOKMAP_API_KEY", ""),
            host=os.getenv("BOOKMAP_HOST", "localhost"),
            port=int(os.getenv("BOOKMAP_PORT", "9000")),
            symbols=bookmap_symbols,
            depth_levels=int(os.getenv("BOOKMAP_DEPTH_LEVELS", "20")),
        ),
        alert=AlertConfig(
            min_order_size=float(os.getenv("ALERT_MIN_ORDER_SIZE", "100")),
            min_absorption_size=float(os.getenv("ALERT_MIN_ABSORPTION_SIZE", "500")),
            min_follow_through=float(os.getenv("ALERT_MIN_FOLLOW_THROUGH", "300")),
            regime_threshold_usd=float(os.getenv("ALERT_REGIME_THRESHOLD_USD", "10000")),
            whatsapp_enabled=os.getenv("ALERT_WHATSAPP_ENABLED", "true").lower() == "true",
            email_enabled=os.getenv("ALERT_EMAIL_ENABLED", "false").lower() == "true",
        ),
        whatsapp=WhatsAppConfig(
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
            recipient_phone=os.getenv("ALERT_RECIPIENT_PHONE", ""),
            enabled=os.getenv("ALERT_WHATSAPP_ENABLED", "true").lower() == "true",
        ),
        regime=RegimeConfig(
            ma_short=int(os.getenv("REGIME_MA_SHORT", "5")),
            ma_long=int(os.getenv("REGIME_MA_LONG", "20")),
            atr_period=int(os.getenv("REGIME_ATR_PERIOD", "14")),
            volatility_threshold=float(os.getenv("REGIME_VOLATILITY_THRESHOLD", "0.02")),
        ),
        absorption=AbsorptionConfig(
            time_window_ms=int(os.getenv("ABSORPTION_TIME_WINDOW_MS", "2000")),
            delta_min_ratio=float(os.getenv("ABSORPTION_DELTA_MIN_RATIO", "0.5")),
            volume_min_pct=float(os.getenv("ABSORPTION_VOLUME_MIN_PCT", "0.3")),
        ),
        followthrough=FollowThroughConfig(
            time_window_ms=int(os.getenv("FOLLOWTHROUGH_TIME_WINDOW_MS", "5000")),
            min_confirmation_count=int(os.getenv("FOLLOWTHROUGH_MIN_CONFIRMATION_COUNT", "2")),
            min_volume_ratio=float(os.getenv("FOLLOWTHROUGH_MIN_VOLUME_RATIO", "0.4")),
        ),
        service=ServiceConfig(
            log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO"),
            tick_interval_ms=int(os.getenv("SERVICE_TICK_INTERVAL_MS", "100")),
            history_depth=int(os.getenv("SERVICE_HISTORY_DEPTH", "1000")),
            redis_url=os.getenv("SERVICE_REDIS_URL", "redis://localhost:6379/0"),
            debug_mode=os.getenv("SERVICE_DEBUG_MODE", "false").lower() == "true",
            mock_feed_mode=os.getenv("SERVICE_MOCK_FEED_MODE", "false").lower() == "true",
        ),
        monitor=MonitorConfig(
            enabled=os.getenv("MONITOR_ENABLED", "true").lower() == "true",
            metrics_port=int(os.getenv("MONITOR_METRICS_PORT", "8888")),
            alert_queue_max_size=int(os.getenv("MONITOR_ALERT_QUEUE_MAX_SIZE", "1000")),
            error_log_path=os.getenv("MONITOR_ERROR_LOG_PATH", "./logs/errors.log"),
        ),
    )
    
    config.validate()
    return config
