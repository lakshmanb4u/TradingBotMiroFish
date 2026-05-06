"""
Live Orderflow Alert Service Package

A production-ready service for real-time orderflow analysis, regime detection,
absorption pattern identification, and WhatsApp alert delivery.
"""

__version__ = "1.0.0"
__author__ = "Market Research Team"

from .config import load_config
from .data_types import (
    OrderFlowEvent, BarData, AbsorptionSignal, FollowThroughConfirmation,
    RegimeState, OrderFlowAlert, AlertType, AlertSeverity, OrderSide
)
from .live_service import LiveOrderflowService
from .alert_engine import AlertEngine
from .replay_harness import ReplayHarness

__all__ = [
    'load_config',
    'OrderFlowEvent',
    'BarData',
    'AbsorptionSignal',
    'FollowThroughConfirmation',
    'RegimeState',
    'OrderFlowAlert',
    'AlertType',
    'AlertSeverity',
    'OrderSide',
    'LiveOrderflowService',
    'AlertEngine',
    'ReplayHarness',
]
