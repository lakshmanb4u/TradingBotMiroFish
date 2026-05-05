"""
Orchestrates sweep detection + reclaim confirmation

Outputs structured events with confidence scoring
"""
from dataclasses import dataclass
from typing import List

@dataclass
class SweepReclaimSignal:
    timestamp: str
    direction: str
    level: float
    sweep_detected: bool
    reclaim_detected: bool
    confidence: float
    source_audit: dict

class SweepReclaimStrategy:
    def __init__(self):
        self.sweep_detector = LiquiditySweepDetector()
        self.reclaim_detector = ReclaimDetector()

    def run(self, df):
        """Process normalized orderflow data"""
        signals = []
        
        # Audit tracking
        audit = {
            'bookmap_data': 'stored_parquet',
            'liquidity_fields': all(f in df.columns for f in ['liquidity_above', 'liquidity_below'])
        }
        
        # Detection pipeline
        sweeps = self.sweep_detector.detect(df)
        reclaims = self.reclaim_detector.detect(df, sweeps)
        
        # TODO: Combine into signals with confidence
        
        return signals