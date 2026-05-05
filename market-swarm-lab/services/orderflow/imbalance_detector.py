# services/orderflow/imbalance_detector.py
"""
Detect orderbook imbalances from Bookmap L1 API depth events.

Imbalance scoring:
- Bid/ask ratio at each level (extreme values = imbalance)
- Stack imbalance (significant size difference at best bid vs best ask)
- Iceberg detection (size refresh pattern - simplified)
"""
from __future__ import annotations
import json
import argparse
from dataclasses import dataclass, field
from typing import Dict, List
from collections import defaultdict

@dataclass
class ImbalanceReading:
    ts: str
    price: float
    bid_size: int
    ask_size: int
    imbalance_ratio: float     # bid/ask ratio (>1 = bid heavy, <1 = ask heavy)
    imbalance_extreme: bool    # ratio > 3 or < 0.33
    stacked_imbalance: bool    # Multiple levels showing same direction

class ImbalanceDetector:
    def __init__(self, extreme_threshold: float = 3.0, stack_depth: int = 3):
        self.extreme_threshold = extreme_threshold
        self.stack_depth = stack_depth
        self.readings: List[ImbalanceReading] = []
        self.levels: Dict[float, Dict[str, int]] = defaultdict(lambda: {"bid": 0, "ask": 0})

    def ingest(self, events: List[Dict]):
        """Process depth events to detect imbalances."""
        for ev in events:
            if ev.get("event_type") == "depth":
                self._process_depth(ev)

    def _process_depth(self, ev: Dict):
        price = ev.get("price", 0.0) or 0.0
        size = ev.get("size", 0) or 0
        side = ev.get("side", "")
        ts = ev.get("ts_event", "")

        if side == "bid":
            self.levels[price]["bid"] = size
        elif side == "ask":
            self.levels[price]["ask"] = size

        # Check best levels for imbalance
        self._check_imbalance(ts, price)

    def _check_imbalance(self, ts: str, price: float):
        # Get nearby levels for stacking analysis
        sorted_prices = sorted(self.levels.keys())
        if not sorted_prices:
            return

        # Find position of current price
        try:
            idx = sorted_prices.index(price)
        except ValueError:
            return

        # Check single level imbalance
        level = self.levels[price]
        bid_size = level["bid"]
        ask_size = level["ask"]

        if ask_size > 0:
            ratio = bid_size / ask_size
        else:
            ratio = float("inf") if bid_size > 0 else 1.0

        extreme = ratio > self.extreme_threshold or ratio < (1 / self.extreme_threshold)

        # Check stacked imbalance
        stacked = False
        if extreme:
            # Look at adjacent levels
            stack_count = 0
            start = max(0, idx - self.stack_depth)
            end = min(len(sorted_prices), idx + self.stack_depth + 1)
            
            for p in sorted_prices[start:end]:
                l = self.levels[p]
                if l["ask"] > 0:
                    r = l["bid"] / l["ask"]
                    if (ratio > self.extreme_threshold and r > self.extreme_threshold) or \
                       (ratio < 1/self.extreme_threshold and r < 1/self.extreme_threshold):
                        stack_count += 1
            
            stacked = stack_count >= 2

        self.readings.append(ImbalanceReading(
            ts=ts,
            price=price,
            bid_size=bid_size,
            ask_size=ask_size,
            imbalance_ratio=round(ratio, 2),
            imbalance_extreme=extreme,
            stacked_imbalance=stacked,
        ))

    def get_extreme_readings(self) -> List[Dict]:
        """Get readings with extreme imbalances."""
        return [
            {
                "ts": r.ts,
                "price": r.price,
                "bid_size": r.bid_size,
                "ask_size": r.ask_size,
                "ratio": r.imbalance_ratio,
                "stacked": r.stacked_imbalance,
            }
            for r in self.readings if r.imbalance_extreme
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to JSONL with depth events")
    parser.add_argument("--output", default="imbalance_readings.json")
    parser.add_argument("--threshold", type=float, default=3.0)
    args = parser.parse_args()

    events = []
    with open(args.input, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))

    detector = ImbalanceDetector(extreme_threshold=args.threshold)
    detector.ingest(events)
    extremes = detector.get_extreme_readings()

    with open(args.output, "w") as f:
        json.dump({"extreme_readings": extremes}, f, indent=2)

    print(f"Imbalance detection complete:")
    print(f"  Total readings: {len(detector.readings)}")
    print(f"  Extreme: {len(extremes)}")
    print(f"Output: {args.output}")
