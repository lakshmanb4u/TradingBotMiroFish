"""
Detects liquidity sweeps from normalized orderflow data

Key logic:
- Identifies price levels with resting liquidity
- Detects when liquidity is removed/traded through
- Confirms reclaim/rejection patterns
"""
from dataclasses import dataclass

@dataclass
class SweepEvent:
    timestamp: str
    direction: str  # 'bullish' or 'bearish'
    level: float
    confidence: float

class LiquiditySweepDetector:
    def __init__(self, window_bars: int = 3):
        self.window = window_bars

    def detect(self, df):
        """Process normalized orderflow dataframe"""
        events = []
        # TODO: Implement sweep detection logic
        return events