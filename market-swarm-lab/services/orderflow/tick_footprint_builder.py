#!/usr/bin/env python3
"""
tick_footprint_builder.py
Reddit-style tick-based footprint candles, NOT time-based.

Reddit method:
1. Build candles of fixed tick count (e.g., 10-20 ticks per candle)
2. Track OHLC + total delta per candle
3. Build delta ladder: delta at each price level within the candle
4. Detect candle color vs delta divergence (green body + neg delta)
5. Identify absorption: aggressive sellers hit low, price stalls, buyers absorb
"""
from __future__ import annotations

import json
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional, DefaultDict
from collections import defaultdict


# ─── Config ──────────────────────────────────────────────────────────────────
DEFAULT_TICKS_PER_CANDLE = 20    # Number of trades per candle
ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


# ─── Data classes ────────────────────────────────────────────────────────────
@dataclass
class FootprintLadder:
    """Delta and volume at a single price level."""
    price: float
    buy_vol: int = 0
    sell_vol: int = 0
    total_vol: int = 0
    delta: int = 0  # buy - sell

    def add_trade(self, side: str, size: int):
        self.total_vol += size
        if side == "buy":
            self.buy_vol += size
            self.delta += size
        elif side == "sell":
            self.sell_vol += size
            self.delta -= size


@dataclass
class TickFootprintCandle:
    """A candle built from N ticks, with full delta ladder."""
    ts_open: str = ""
    ts_close: str = ""
    open_price: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    total_vol: int = 0
    total_delta: int = 0
    aggressive_buy_vol: int = 0
    aggressive_sell_vol: int = 0
    ticks: int = 0
    # Reddit: delta ladder at each price level
    ladder: Dict[float, FootprintLadder] = field(default_factory=dict)
    # Reddit: candle body color
    is_green: bool = False    # close >= open
    is_red: bool = False      # close < open
    # Reddit: divergence detected
    has_divergence: bool = False
    divergence_type: str = ""   # bullish_divergence | bearish_divergence

    @property
    def body_delta(self) -> float:
        """Body size in ticks."""
        return abs(self.close - self.open_price) / self._tick_size_for(self.close)

    @property
    def wick_top(self) -> float:
        return self.high - max(self.open_price, self.close)

    @property
    def wick_bottom(self) -> float:
        return min(self.open_price, self.close) - self.low

    @staticmethod
    def _tick_size_for(price: float) -> float:
        return ES_TICK_SIZE if price < 10000 else NQ_TICK_SIZE


# ─── Builder ─────────────────────────────────────────────────────────────────
class TickFootprintBuilder:
    def __init__(self, ticks_per_candle: int = DEFAULT_TICKS_PER_CANDLE):
        self.ticks_per_candle = ticks_per_candle
        self.candles: List[TickFootprintCandle] = []
        self.current_candle: Optional[TickFootprintCandle] = None
        self._current_tick_count = 0

    def ingest(self, events: List[Dict]) -> List[TickFootprintCandle]:
        """Process trade events into tick-based footprint candles."""
        for ev in events:
            if ev.get("event_type") != "trade":
                continue
            self._process_trade(ev)
        # Close final candle
        if self.current_candle:
            self._finalize_candle(self.current_candle)
            self.candles.append(self.current_candle)
        return self.candles

    def _process_trade(self, ev: Dict):
        price = ev.get("price", 0.0) or 0.0
        size = ev.get("size", 0) or 0
        side = ev.get("side", "")
        ts = ev.get("ts_event", "")

        # Start new candle if needed
        if self.current_candle is None:
            self.current_candle = self._new_candle(price, ts)

        c = self.current_candle
        c.ts_close = ts
        c.high = max(c.high, price)
        c.low = min(c.low, price)
        c.close = price
        c.total_vol += size
        c.ticks += 1

        # Update delta
        if side == "buy":
            c.total_delta += size
            c.aggressive_buy_vol += size
        elif side == "sell":
            c.total_delta -= size
            c.aggressive_sell_vol += size

        # Build ladder
        if price not in c.ladder:
            c.ladder[price] = FootprintLadder(price=price)
        c.ladder[price].add_trade(side, size)

        # Check if candle is complete
        if c.ticks >= self.ticks_per_candle:
            self._finalize_candle(c)
            self.candles.append(c)
            self.current_candle = self._new_candle(price, ts)

    def _new_candle(self, price: float, ts: str) -> TickFootprintCandle:
        return TickFootprintCandle(
            ts_open=ts,
            ts_close=ts,
            open_price=price,
            high=price,
            low=price,
            close=price,
        )

    def _finalize_candle(self, c: TickFootprintCandle):
        """Finalize candle metadata after all ticks added."""
        c.is_green = c.close >= c.open_price
        c.is_red = c.close < c.open_price
        # Reddit divergence: green candle + negative delta, or red candle + positive delta
        if c.is_green and c.total_delta < 0:
            c.has_divergence = True
            c.divergence_type = "green_body_negative_delta"
        elif c.is_red and c.total_delta > 0:
            c.has_divergence = True
            c.divergence_type = "red_body_positive_delta"

    # ─── Reddit Analysis Helpers ─────────────────────────────────────────────
    def find_divergence_candles(self) -> List[TickFootprintCandle]:
        """Return candles with candle-color vs delta divergence."""
        return [c for c in self.candles if c.has_divergence]

    def find_absorption(self, lookback_candles: int = 5) -> List[Dict]:
        """
        Reddit absorption criteria:
        - Price approaches a marked level
        - Aggressive sellers hit the low (large sell volume at level)
        - Price does NOT continue down
        - Buyers absorb sellers (ladder shows buy delta at level)
        """
        results = []
        for i in range(lookback_candles, len(self.candles)):
            current = self.candles[i]
            prior = self.candles[i - lookback_candles:i]
            
            # Find the low of recent candles
            lowest_price = min(c.low for c in prior)
            
            # Check if price tested the low or near it
            price_near_low = abs(current.low - lowest_price) < current._tick_size_for(current.close) * 2
            
            if not price_near_low:
                continue
            
            # Check ladder at the low
            low_level = min(current.ladder.keys()) if current.ladder else current.low
            ladder = current.ladder.get(low_level)
            if ladder is None:
                continue
            
            # Reddit: aggressive sellers hit low
            sellers_hit = ladder.sell_vol > ladder.buy_vol * 1.5
            
            # Reddit: price does NOT continue down (candle closes off low)
            price_stalls = current.close > current.low + (current._tick_size_for(current.close) * 2)
            
            # Reddit: buyers absorb = ladder shows buy pressure at low
            buyers_absorb = ladder.buy_vol > ladder.sell_vol * 0.5
            
            if sellers_hit and price_stalls and buyers_absorb:
                results.append({
                    "ts": current.ts_close,
                    "price_level": low_level,
                    "sell_vol": ladder.sell_vol,
                    "buy_vol": ladder.buy_vol,
                    "delta": ladder.delta,
                    "low": current.low,"close": current.close,
                    "candle_type": "red" if current.is_red else "green" if current.is_green else "doji",
                    "absorption_confidence": round(min(ladder.sell_vol, ladder.buy_vol) / max(ladder.sell_vol, 1), 2),
                })
        return results

    def get_largest_delta_levels(self, candle_idx: int = -1) -> List[Dict]:
        """Return price levels with largest delta magnitude."""
        if not self.candles:
            return []
        if candle_idx == -1:
            candle_idx = len(self.candles) - 1
        c = self.candles[candle_idx]
        levels = sorted(c.ladder.values(), key=lambda x: abs(x.delta), reverse=True)
        return [{"price": l.price, "delta": l.delta, "buy_vol": l.buy_vol,
                 "sell_vol": l.sell_vol} for l in levels[:5]]

    def get_biggest_delta_candles(self, n: int = 5) -> List[Dict]:
        """Return candles with largest total delta magnitude."""
        sorted_candles = sorted(self.candles, key=lambda c: abs(c.total_delta), reverse=True)
        return [{
            "ts": c.ts_close,
            "open": c.open_price, "close": c.close, "delta": c.total_delta,
            "vol": c.total_vol, "ticks": c.ticks,
            "divergence": c.divergence_type if c.has_divergence else "none"
        } for c in sorted_candles[:n]]


# ─── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to JSONL with trade events")
    parser.add_argument("--ticks-per-candle", type=int, default=DEFAULT_TICKS_PER_CANDLE)
    parser.add_argument("--output", default="tick_footprint.json")
    args = parser.parse_args()

    events = []
    with open(args.input, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("event_type") == "trade":
                events.append(ev)

    builder = TickFootprintBuilder(ticks_per_candle=args.ticks_per_candle)
    candles = builder.ingest(events)

    divergence = builder.find_divergence_candles()
    absorption = builder.find_absorption()
    biggest_delta = builder.get_biggest_delta_candles(5)
    latest_ladder = builder.get_largest_delta_levels()

    result = {
        "candles_built": len(candles),
        "divergence_candles": len(divergence),
        "absorption_events": len(absorption),
        "latest_ladder": latest_ladder,
        "biggest_delta_candles": biggest_delta,
        "sample_divergences": [
            {"ts": c.ts_close, "open": c.open_price, "close": c.close,
             "delta": c.total_delta, "type": c.divergence_type}
            for c in (divergence[:5] if divergence else [])
        ],
        "sample_absorption": absorption[:5],
    }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Tick footprint built:")
    print(f"  Candles: {len(candles)}")
    print(f"  Divergence candles: {len(divergence)}")
    print(f"  Absorption events: {len(absorption)}")
    print(f"Output: {args.output}")
