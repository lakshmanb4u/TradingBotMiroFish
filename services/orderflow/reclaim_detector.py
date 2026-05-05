"""
Confirms reclaim/rejection after liquidity sweep

Checks:
- Time window since sweep
- Closing price relative to level
- Volume confirmation
"""
from dataclasses import dataclass

@dataclass
class ReclaimEvent:
    timestamp: str
    direction: str
    level: float
    reclaim_bar_count: int
    volume_ratio: float

class ReclaimDetector:
    def __init__(self, max_bars: int = 5, min_volume_ratio: float = 1.2):
        self.max_bars = max_bars
        self.min_volume = min_volume_ratio

    def detect(self, df, sweep_events):
        """Process sweep events and price data"""
        reclaims = []
        # TODO: Implement reclaim logic
        return reclaims