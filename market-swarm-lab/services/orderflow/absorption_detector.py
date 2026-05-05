#!/usr/bin/env python3
"""
absorption_detector.py
Reddit-style absorption detection from tick footprint candles.

Bullish absorption:
  - Aggressive sellers hit a level (large sell vol at low)
  - Price stalls / does NOT continue down
  - Buyers absorb the selling (buy delta at the low)

Bearish absorption:
  - Aggressive buyers hit a level (large buy vol at high)
  - Price stalls / does NOT continue up
  - Sellers absorb the buying (sell delta at the high)

Typical footprint signature:
  - Delta flips at the level
  - Large volume at level with minimal price continuation
  - Candle wick shows rejection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import defaultdict


ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


@dataclass(frozen=True)
class AbsorptionEvent:
    """A detected absorption event."""
    ts: str
    level_price: float
    direction: str           # bullish | bearish
    score: float             # 0.0 - 100.0 composite score
    sell_vol: int = 0
    buy_vol: int = 0
    delta: int = 0
    candle_low: float = 0.0
    candle_high: float = 0.0
    candle_close: float = 0.0
    candle_open: float = 0.0
    price_stall_ticks: float = 0.0
    touch_count: int = 0
    metadata: Dict = field(default_factory=dict)


class AbsorptionDetector:
    """
    Detect absorption events across a series of tick footprint candles.
    """

    def __init__(
        self,
        lookback_candles: int = 5,
        stall_threshold_ticks: float = 2.0,
        min_sell_ratio: float = 1.5,
        min_buy_ratio: float = 1.5,
        min_absorption_score: float = 25.0,
        prox_ticks: int = 3,
    ):
        self.lookback_candles = lookback_candles
        self.stall_threshold_ticks = stall_threshold_ticks
        self.min_sell_ratio = min_sell_ratio
        self.min_buy_ratio = min_buy_ratio
        self.min_absorption_score = min_absorption_score
        self.prox_ticks = prox_ticks
        self.events: List[AbsorptionEvent] = []
        self._tick_size = ES_TICK_SIZE

    # ─── Public API ──────────────────────────────────────────────────────

    def analyze(self, candles: List, marked_levels: Optional[List] = None) -> List[AbsorptionEvent]:
        """
        Run absorption detection on candles, optionally using known marked levels
        as the structural reference instead of auto-derived recent extremes.
        """
        if not candles or len(candles) < self.lookback_candles + 1:
            self.events = []
            return []

        self._tick_size = self._infer_tick_size(candles)
        self.events = []

        if marked_levels:
            support_levels = [lv.price for lv in marked_levels if lv.level_type in ("session_low", "opening_range_low", "vwap", "poc")]
            resistance_levels = [lv.price for lv in marked_levels if lv.level_type in ("session_high", "opening_range_high", "vwap", "poc")]
        else:
            support_levels = None
            resistance_levels = None

        for i in range(self.lookback_candles, len(candles)):
            current = candles[i]
            prior = candles[i - self.lookback_candles:i]

            # --- Bullish absorption at support ---
            self._check_bullish_absorption(current, prior, support_levels)

            # --- Bearish absorption at resistance ---
            self._check_bearish_absorption(current, prior, resistance_levels)

        # Sort by score descending
        self.events.sort(key=lambda e: e.score, reverse=True)
        return self.events

    def get_bullish_events(self) -> List[AbsorptionEvent]:
        return [e for e in self.events if e.direction == "bullish"]

    def get_bearish_events(self) -> List[AbsorptionEvent]:
        return [e for e in self.events if e.direction == "bearish"]

    def get_events_at_level(self, price: float, tolerance_ticks: int = 3) -> List[AbsorptionEvent]:
        threshold = tolerance_ticks * self._tick_size
        return [e for e in self.events if abs(e.level_price - price) <= threshold]

    # ─── Detection internals ─────────────────────────────────────────────

    def _check_bullish_absorption(self, current, prior: List, support_levels: Optional[List[float]] = None):
        """
        Bullish absorption: aggressive sellers hit level, price stalls, buyers absorb.
        """
        # Determine the reference low
        if support_levels is not None:
            near_level = None
            for lv in support_levels:
                if self._price_near(current.low, lv):
                    near_level = lv
                    break
            if near_level is None:
                return
            reference_low = near_level
        else:
            prior_lows = [c.low for c in prior]
            reference_low = min(prior_lows) if prior_lows else current.low
            if not self._price_near(current.low, reference_low):
                return

        # Must have a ladder at/near the low to inspect volume
        low_level = self._get_nearest_ladder(current.ladder, reference_low) if hasattr(current, 'ladder') else None
        if low_level is None:
            # Use candle-level if no ladder
            sell_vol = getattr(current, 'aggressive_sell_vol', 0)
            buy_vol = getattr(current, 'aggressive_buy_vol', 0)
            delta = getattr(current, 'total_delta', 0)
            if not self._has_sellers(current, sell_vol, buy_vol):
                return
        else:
            if hasattr(low_level, 'sell_vol'):
                sell_vol = low_level.sell_vol
                buy_vol = low_level.buy_vol
                delta = low_level.delta
            elif isinstance(low_level, dict):
                sell_vol = low_level.get('sell_vol', 0)
                buy_vol = low_level.get('buy_vol', 0)
                delta = low_level.get('delta', 0)
            else:
                sell_vol = buy_vol = delta = 0
            if not self._has_sellers(current, sell_vol, buy_vol):
                return

        # Stall: price does not continue down — close is off the low
        ts = getattr(current, 'ts_close', getattr(current, 'ts', ""))
        tick_size = self._tick_size
        price_stall = current.close - current.low if hasattr(current, 'close') and hasattr(current, 'low') else 0
        stall_ticks = price_stall / tick_size if tick_size > 0 else 0
        if stall_ticks < self.stall_threshold_ticks:
            return

        # Buyers absorb: buy vol is significant relative to sell vol at the low
        if sell_vol > 0 and buy_vol / sell_vol < 0.3:
            return

        # Score construction
        score = self._score_bullish_absorption(
            current, sell_vol, buy_vol, delta, stall_ticks, prior
        )

        if score < self.min_absorption_score:
            return

        self.events.append(
            AbsorptionEvent(
                ts=ts,
                level_price=reference_low,
                direction="bullish",
                score=round(score, 1),
                sell_vol=sell_vol,
                buy_vol=buy_vol,
                delta=delta,
                candle_low=current.low,
                candle_high=current.high,
                candle_close=current.close,
                candle_open=current.open_price if hasattr(current, 'open_price') else current.close,
                price_stall_ticks=round(stall_ticks, 1),
                touch_count=self._count_touches(prior, reference_low),
                metadata={
                    "absorption_type": "sellers_hit_low_buyers_absorb",
                    "wick_bottom": round(current.open_price - current.low, 2) if hasattr(current, 'open_price') else 0,
                },
            )
        )

    def _check_bearish_absorption(self, current, prior: List, resistance_levels: Optional[List[float]] = None):
        """
        Bearish absorption: aggressive buyers hit level, price stalls, sellers absorb.
        """
        if resistance_levels is not None:
            near_level = None
            for lv in resistance_levels:
                if self._price_near(current.high, lv):
                    near_level = lv
                    break
            if near_level is None:
                return
            reference_high = near_level
        else:
            prior_highs = [c.high for c in prior]
            reference_high = max(prior_highs) if prior_highs else current.high
            if not self._price_near(current.high, reference_high):
                return

        high_level = self._get_nearest_ladder(current.ladder, reference_high) if hasattr(current, 'ladder') else None
        if high_level is None:
            sell_vol = getattr(current, 'aggressive_sell_vol', 0)
            buy_vol = getattr(current, 'aggressive_buy_vol', 0)
            delta = getattr(current, 'total_delta', 0)
            if not self._has_buyers(current, buy_vol, sell_vol):
                return
        else:
            if hasattr(high_level, 'sell_vol'):
                sell_vol = high_level.sell_vol
                buy_vol = high_level.buy_vol
                delta = high_level.delta
            elif isinstance(high_level, dict):
                sell_vol = high_level.get('sell_vol', 0)
                buy_vol = high_level.get('buy_vol', 0)
                delta = high_level.get('delta', 0)
            else:
                sell_vol = buy_vol = delta = 0
            if not self._has_buyers(current, buy_vol, sell_vol):
                return

        # Stall: price does not continue up — close is off the high
        ts = getattr(current, 'ts_close', getattr(current, 'ts', ""))
        tick_size = self._tick_size
        price_stall = current.high - current.close if hasattr(current, 'high') and hasattr(current, 'close') else 0
        stall_ticks = price_stall / tick_size if tick_size > 0 else 0
        if stall_ticks < self.stall_threshold_ticks:
            return

        # Sellers absorb: sell vol significant relative to buy vol at the high
        if buy_vol > 0 and sell_vol / buy_vol < 0.3:
            return

        score = self._score_bearish_absorption(
            current, buy_vol, sell_vol, delta, stall_ticks, prior
        )

        if score < self.min_absorption_score:
            return

        self.events.append(
            AbsorptionEvent(
                ts=ts,
                level_price=reference_high,
                direction="bearish",
                score=round(score, 1),
                sell_vol=sell_vol,
                buy_vol=buy_vol,
                delta=delta,
                candle_low=current.low,
                candle_high=current.high,
                candle_close=current.close,
                candle_open=current.open_price if hasattr(current, 'open_price') else current.close,
                price_stall_ticks=round(stall_ticks, 1),
                touch_count=self._count_touches(prior, reference_high),
                metadata={
                    "absorption_type": "buyers_hit_high_sellers_absorb",
                    "wick_top": round(current.high - current.close, 2) if hasattr(current, 'high') and hasattr(current, 'close') else 0,
                },
            )
        )

    # ─── Scoring ───────────────────────────────────────────────────────────

    def _score_bullish_absorption(self, current, sell_vol: int, buy_vol: int, delta: int, stall_ticks: float, prior: List) -> float:
        """Composite 0-100 score for bullish absorption strength."""
        score = 40.0  # base

        # Volume at the level
        total_vol = sell_vol + buy_vol
        if total_vol > 50:
            score += 15
        if total_vol > 150:
            score += 10

        # Sell ratio strength
        if sell_vol > 0:
            ratio = sell_vol / max(buy_vol, 1)
            if ratio > self.min_sell_ratio:
                score += min((ratio - self.min_sell_ratio) * 10, 15)

        # Delta flip at level (bullish = negative sell delta then positive)
        if delta > 0:
            score += 10
        if sell_vol > buy_vol * 1.2 and delta > 0:
            score += 15  # heavy selling absorbed into positive delta = strong signal

        # Stall magnitude
        if stall_ticks > 4:
            score += 10
        elif stall_ticks > 2:
            score += 5

        # Wick recovery
        if hasattr(current, 'wick_bottom'):
            wick = current.wick_bottom
            if wick > 0:
                score += min(wick / self._tick_size * 2, 10)

        # Prior touch count (multiple tests = stronger level)
        prior_lows = [c.low for c in prior]
        touch_count = sum(1 for p in prior_lows if abs(p - current.low) <= self.prox_ticks * self._tick_size)
        if touch_count >= 2:
            score += 5

        return min(score, 100.0)

    def _score_bearish_absorption(self, current, buy_vol: int, sell_vol: int, delta: int, stall_ticks: float, prior: List) -> float:
        """Composite 0-100 score for bearish absorption strength."""
        score = 40.0

        total_vol = buy_vol + sell_vol
        if total_vol > 50:
            score += 15
        if total_vol > 150:
            score += 10

        if buy_vol > 0:
            ratio = buy_vol / max(sell_vol, 1)
            if ratio > self.min_buy_ratio:
                score += min((ratio - self.min_buy_ratio) * 10, 15)

        if delta < 0:
            score += 10
        if buy_vol > sell_vol * 1.2 and delta < 0:
            score += 15

        if stall_ticks > 4:
            score += 10
        elif stall_ticks > 2:
            score += 5

        if hasattr(current, 'wick_top'):
            wick = current.wick_top
            if wick > 0:
                score += min(wick / self._tick_size * 2, 10)

        prior_highs = [c.high for c in prior]
        touch_count = sum(1 for p in prior_highs if abs(p - current.high) <= self.prox_ticks * self._tick_size)
        if touch_count >= 2:
            score += 5

        return min(score, 100.0)

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _price_near(self, price: float, ref: float) -> bool:
        return abs(price - ref) <= self.prox_ticks * self._tick_size

    def _has_sellers(self, current, sell_vol: int, buy_vol: int) -> bool:
        """Aggressive sellers hit the level."""
        if sell_vol > buy_vol * self.min_sell_ratio:
            return True
        # Candle-level fallback
        if hasattr(current, 'is_red') and current.is_red:
            if sell_vol >= buy_vol:
                return True
        return sell_vol > 0 and sell_vol > buy_vol

    def _has_buyers(self, current, buy_vol: int, sell_vol: int) -> bool:
        """Aggressive buyers hit the level."""
        if buy_vol > sell_vol * self.min_buy_ratio:
            return True
        if hasattr(current, 'is_green') and current.is_green:
            if buy_vol >= sell_vol:
                return True
        return buy_vol > 0 and buy_vol > sell_vol

    def _get_nearest_ladder(self, ladder: Dict, target: float):
        """Get ladder entry closest to target price."""
        if not ladder:
            return None
        best = None
        best_dist = float("inf")
        for price, level in ladder.items():
            dist = abs(float(price) - target)
            if dist < best_dist:
                best_dist = dist
                best = level
        return best if best_dist <= self.prox_ticks * self._tick_size else None

    def _count_touches(self, prior: List, level: float) -> int:
        """Count how many recent candles touched this level."""
        count = 0
        for c in prior:
            low = getattr(c, 'low', getattr(c, 'price', 0))
            high = getattr(c, 'high', getattr(c, 'price', 0))
            if (low <= level <= high) or self._price_near(low, level) or self._price_near(high, level):
                count += 1
        return count

    def _infer_tick_size(self, candles: List) -> float:
        for c in candles:
            price = getattr(c, 'close', getattr(c, 'price', 0))
            if price > 10000:
                return NQ_TICK_SIZE
            if 1000 < price < 10000:
                return ES_TICK_SIZE
        return ES_TICK_SIZE


# ─── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to tick_footprint.json")
    parser.add_argument("--output", default="absorption_events.json")
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    # Reconstruct TickFootprintCandles from JSON
    import sys
    sys.path.insert(0, ".")
    from tick_footprint_builder import TickFootprintBuilder, TickFootprintCandle, FootprintLadder

    candles = []
    for cj in data.get("candles", []):
        c = TickFootprintCandle(
            ts_open=cj.get("ts_open", ""),
            ts_close=cj.get("ts_close", ""),
            open_price=cj.get("open", cj.get("open_price", 0)),
            high=cj.get("high", 0), low=cj.get("low", 0), close=cj.get("close", 0),
            total_vol=cj.get("total_vol", cj.get("volume", 0)),
            total_delta=cj.get("total_delta", cj.get("delta", 0)),
            aggressive_buy_vol=cj.get("aggressive_buy_vol", 0),
            aggressive_sell_vol=cj.get("aggressive_sell_vol", 0),
            ticks=cj.get("ticks", 0),
        )
        for price, ld in cj.get("ladder", {}).items():
            p = float(price)
            c.ladder[p] = FootprintLadder(
                price=p, buy_vol=ld.get("buy_vol", 0), sell_vol=ld.get("sell_vol", 0),
                total_vol=ld.get("total_vol", 0), delta=ld.get("delta", 0),
            )
        candles.append(c)

    detector = AbsorptionDetector()
    events = detector.analyze(candles)

    result = {
        "count": len(events),
        "bullish": len(detector.get_bullish_events()),
        "bearish": len(detector.get_bearish_events()),
        "events": [
            {
                "ts": e.ts, "level_price": e.level_price, "direction": e.direction,
                "score": e.score, "sell_vol": e.sell_vol, "buy_vol": e.buy_vol,
                "delta": e.delta, "stall_ticks": e.price_stall_ticks,
                "touches": e.touch_count,
            }
            for e in events[:20]
        ],
    }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Absorption events: {len(events)} (bullish={len(detector.get_bullish_events())}, bearish={len(detector.get_bearish_events())})")
