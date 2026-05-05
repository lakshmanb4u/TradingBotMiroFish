# services/orderflow/delta_profile.py
"""
Delta profile analysis for orderflow confirmation.

Analyzes delta (aggressive buy - sell volume) patterns:
- Delta divergence (price moves one way, delta moves other way)
- Delta flip (delta changes sign within candle)
- Cumulative delta trend
- Delta at key levels
"""
from __future__ import annotations
import json
import argparse
from dataclasses import dataclass
from typing import List, Dict
import statistics

@dataclass
class DeltaProfile:
    """Delta analysis for a time window or level."""
    ts_start: str
    ts_end: str
    net_delta: int
    max_delta: int
    min_delta: int
    delta_divergence: bool = False
    divergence_type: str = ""  # bullish_divergence | bearish_divergence
    confidence: float = 0.0

class DeltaProfiler:
    def __init__(self, window_events: int = 20):
        self.window_events = window_events
        self.profiles: List[DeltaProfile] = []
    
    def analyze_candles(self, candles: List[Dict]) -> List[DeltaProfile]:
        """Analyze delta patterns across candles."""
        for i in range(len(candles)):
            window = candles[max(0, i-self.window_events):i+1]
            profile = self._analyze_window(window)
            self.profiles.append(profile)
        return self.profiles
    
    def _analyze_window(self, window: List[Dict]) -> DeltaProfile:
        if not window:
            return DeltaProfile("", "", 0, 0, 0)
        
        deltas = [c.get("delta", 0) for c in window]
        prices = [c.get("close", 0) for c in window]
        
        net_delta = sum(deltas)
        max_delta = max(deltas) if deltas else 0
        min_delta = min(deltas) if deltas else 0
        
        # Detect divergence: price trend vs delta trend
        price_change = prices[-1] - prices[0] if len(prices) > 1 else 0
        delta_trend = deltas[-1] - deltas[0] if len(deltas) > 1 else 0
        
        divergence = False
        div_type = ""
        confidence = 0.0
        
        if price_change > 0 and delta_trend < 0:
            divergence = True
            div_type = "bearish_divergence"
            confidence = min(abs(delta_trend) / max(abs(net_delta), 1), 1.0)
        elif price_change < 0 and delta_trend > 0:
            divergence = True
            div_type = "bullish_divergence"
            confidence = min(abs(delta_trend) / max(abs(net_delta), 1), 1.0)
        
        return DeltaProfile(
            ts_start=window[0].get("ts", ""),
            ts_end=window[-1].get("ts", ""),
            net_delta=net_delta,
            max_delta=max_delta,
            min_delta=min_delta,
            delta_divergence=divergence,
            divergence_type=div_type,
            confidence=round(confidence, 2),
        )
    
    def find_delta_flips(self, candles: List[Dict]) -> List[Dict]:
        """Find candles where delta flipped sign (indicating absorption/battle)."""
        flips = []
        for i in range(1, len(candles)):
            prev_delta = candles[i-1].get("delta", 0)
            curr_delta = candles[i].get("delta", 0)
            
            if prev_delta * curr_delta < 0:  # Sign changed
                flip_type = "buy_to_sell" if prev_delta > 0 else "sell_to_buy"
                flips.append({
                    "ts": candles[i].get("ts", ""),
                    "flip_type": flip_type,
                    "prev_delta": prev_delta,
                    "curr_delta": curr_delta,
                    "magnitude": abs(curr_delta - prev_delta),
                })
        return flips


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to footprint_metrics.json from footprint_builder")
    parser.add_argument("--output", default="delta_profile.json")
    args = parser.parse_args()
    
    with open(args.input, "r") as f:
        data = json.load(f)
    
    profiler = DeltaProfiler()
    profiles = profiler.analyze_candles(data.get("imbalance", []))
    flips = profiler.find_delta_flips(data.get("imbalance", []))
    
    result = {
        "profiles": [
            {
                "ts_start": p.ts_start,
                "ts_end": p.ts_end,
                "net_delta": p.net_delta,
                "delta_divergence": p.delta_divergence,
                "divergence_type": p.divergence_type,
                "confidence": p.confidence,
            }
            for p in profiles
        ],
        "flips": flips,
        "divergence_count": sum(1 for p in profiles if p.delta_divergence),
    }
    
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"Delta profile complete:")
    print(f"  Divergences: {result['divergence_count']}")
    print(f"  Flips: {len(result['flips'])}")
    print(f"Output: {args.output}")
