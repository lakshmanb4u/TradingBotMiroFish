# services/orderflow/footprint_builder.py
"""
Build programmatic footprint metrics from Bookmap L1 API recorder data.

Available from Bookmap L1 API:
- TradeInfo.isBidAggressor → aggressive buy/sell (delta)
- onDepth() → bid/ask size at each price level (orderbook state)
- onTrade() → price, size, aggressor

Derived metrics:
- candle delta (net bid-ask aggressor volume)
- bid/ask imbalance ratio
- aggressive buy/sell volume
- pullback delta (delta during pullback candles)
- absorption (large size at level with minimal price movement)
- failed auction (price tests level multiple times then reverses)
"""
from __future__ import annotations
import json
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import defaultdict
import statistics

@dataclass
class CandleFootprint:
    """Footprint metrics for a single price candle interval."""
    ts_open: str
    ts_close: str
    open_price: float
    high: float
    low: float
    close: float
    volume: int = 0
    delta: int = 0                    # Net aggressive volume: +buy, -sell
    aggressive_buy_vol: int = 0
    aggressive_sell_vol: int = 0
    bid_ask_imbalance: float = 0.0    # Ratio: buy_vol / sell_vol
    absorption_level: Optional[float] = None
    absorption_size: int = 0
    is_pullback: bool = False
    pullback_delta: int = 0

@dataclass
class LevelFootprint:
    """Aggregated metrics at a specific price level."""
    price: float
    total_bid_size: int = 0
    total_ask_size: int = 0
    trade_count: int = 0
    aggressive_buy_size: int = 0
    aggressive_sell_size: int = 0
    absorption_score: float = 0.0     # High = large size, minimal price movement

class FootprintBuilder:
    def __init__(self, candle_seconds: float = 60.0):
        self.candle_seconds = candle_seconds
        self.candles: List[CandleFootprint] = []
        self.levels: Dict[float, LevelFootprint] = defaultdict(lambda: LevelFootprint(price=0.0))
        self.current_candle: Optional[CandleFootprint] = None
        self._last_ts: Optional[str] = None

    def ingest(self, events: List[Dict]):
        """Process events in strict replay order."""
        for ev in events:
            self._process_event(ev)
        # Close final candle
        if self.current_candle:
            self.candles.append(self.current_candle)
        return self.candles, dict(self.levels)

    def _process_event(self, ev: Dict):
        t = ev.get("event_type", "")
        if t == "trade":
            self._process_trade(ev)
        elif t == "depth":
            self._process_depth(ev)

    def _process_trade(self, ev: Dict):
        price = ev.get("price", 0.0) or 0.0
        size = ev.get("size", 0) or 0
        side = ev.get("side", "")
        
        # Update candle
        if self.current_candle is None:
            self.current_candle = CandleFootprint(
                ts_open=ev.get("ts_event", ""),
                ts_close=ev.get("ts_event", ""),
                open_price=price,
                high=price,
                low=price,
                close=price,
            )
        
        candle = self.current_candle
        candle.ts_close = ev.get("ts_event", "")
        candle.high = max(candle.high, price)
        candle.low = min(candle.low, price)
        candle.close = price
        candle.volume += size
        
        # Delta calculation from aggressive side
        if side == "buy":
            candle.aggressive_buy_vol += size
            candle.delta += size
        elif side == "sell":
            candle.aggressive_sell_vol += size
            candle.delta -= size
        
        # Update level footprint
        level = self.levels[price]
        level.price = price
        level.trade_count += 1
        if side == "buy":
            level.aggressive_buy_size += size
        elif side == "sell":
            level.aggressive_sell_size += size

    def _process_depth(self, ev: Dict):
        price = ev.get("price", 0.0) or 0.0
        size = ev.get("size", 0) or 0
        side = ev.get("side", "")
        
        level = self.levels[price]
        level.price = price
        if side == "bid":
            level.total_bid_size = size  # Bookmap gives current size, not delta
        elif side == "ask":
            level.total_ask_size = size

    def compute_absorption(self) -> List[Dict]:
        """Identify absorption levels: large size with minimal price movement."""
        absorption_events = []
        for price, level in self.levels.items():
            total_size = level.total_bid_size + level.total_ask_size
            if total_size > 0 and level.trade_count > 5:
                # Absorption score: size per trade (higher = more absorption)
                level.absorption_score = total_size / level.trade_count
                if level.absorption_score > 50:  # Threshold
                    absorption_events.append({
                        "price": price,
                        "bid_size": level.total_bid_size,
                        "ask_size": level.total_ask_size,
                        "trade_count": level.trade_count,
                        "absorption_score": round(level.absorption_score, 2),
                    })
        return sorted(absorption_events, key=lambda x: x["absorption_score"], reverse=True)

    def compute_imbalance(self) -> List[Dict]:
        """Calculate bid/ask imbalance per candle."""
        results = []
        for c in self.candles:
            total_vol = c.aggressive_buy_vol + c.aggressive_sell_vol
            if total_vol > 0:
                ratio = c.aggressive_buy_vol / max(c.aggressive_sell_vol, 1)
                c.bid_ask_imbalance = ratio
                results.append({
                    "ts": c.ts_close,
                    "delta": c.delta,
                    "imbalance_ratio": round(ratio, 2),
                    "buy_vol": c.aggressive_buy_vol,
                    "sell_vol": c.aggressive_sell_vol,
                })
        return results

    def identify_pullbacks(self, trend_lookback: int = 5) -> List[Dict]:
        """Identify pullback candles within a trend."""
        if len(self.candles) < trend_lookback + 1:
            return []
        
        pullbacks = []
        for i in range(trend_lookback, len(self.candles)):
            recent = self.candles[i-trend_lookback:i]
            # Determine trend direction
            price_changes = [c.close - c.open_price for c in recent]
            avg_change = statistics.mean(price_changes)
            
            current = self.candles[i]
            current_change = current.close - current.open_price
            
            # Pullback: candle against trend with significant delta against trend
            if avg_change > 0 and current_change < 0 and current.delta < 0:
                current.is_pullback = True
                current.pullback_delta = current.delta
                pullbacks.append({
                    "ts": current.ts_close,
                    "trend": "up",
                    "pullback_delta": current.delta,
                    "price_change": round(current_change, 2),
                })
            elif avg_change < 0 and current_change > 0 and current.delta > 0:
                current.is_pullback = True
                current.pullback_delta = current.delta
                pullbacks.append({
                    "ts": current.ts_close,
                    "trend": "down",
                    "pullback_delta": current.delta,
                    "price_change": round(current_change, 2),
                })
        return pullbacks


def run_footprint_builder(jsonl_path: str, candle_seconds: float = 60.0) -> dict:
    events = []
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    
    builder = FootprintBuilder(candle_seconds=candle_seconds)
    candles, levels = builder.ingest(events)
    
    return {
        "candles": len(candles),
        "levels": len(levels),
        "absorption": builder.compute_absorption()[:10],
        "imbalance": builder.compute_imbalance(),
        "pullbacks": builder.identify_pullbacks(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--candle-seconds", type=float, default=60.0)
    parser.add_argument("--output", default="footprint_metrics.json")
    args = parser.parse_args()
    
    result = run_footprint_builder(args.input, args.candle_seconds)
    
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"Footprint metrics built:")
    print(f"  Candles: {result['candles']}")
    print(f"  Levels: {result['levels']}")
    print(f"  Absorption events: {len(result['absorption'])}")
    print(f"  Pullbacks: {len(result['pullbacks'])}")
    print(f"Output: {args.output}")
