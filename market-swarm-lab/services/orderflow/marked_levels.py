#!/usr/bin/env python3
"""
marked_levels.py
Detect structural market levels from tick-based footprint candles.

Levels detected:
- Session high / low
- Opening range (first N candles)
- VWAP (volume-weighted average price)
- POC (point of control — most traded price level)
- High-volume nodes (clusters of high volume)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict


ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


@dataclass(frozen=True)
class MarkedLevel:
    """A structural level discovered from candle analysis."""
    price: float
    level_type: str        # session_high | session_low | opening_range_high |
                           # opening_range_low | vwap | poc | high_volume_node
    strength: float = 0.0  # 0-1 relative strength
    touches: int = 0       # how many times price interacted
    volume: int = 0        # total volume at/near this level
    ts_init: str = ""
    ts_last: str = ""
    metadata: Dict = field(default_factory=dict)


class MarkedLevelsDetector:
    """Analyze a series of TickFootprintCandles and extract structural levels."""

    def __init__(
        self,
        opening_range_candles: int = 20,
        high_volume_threshold_percentile: float = 90.0,
        prox_threshold_ticks: int = 3,
    ):
        self.opening_range_candles = opening_range_candles
        self.high_volume_threshold_percentile = high_volume_threshold_percentile
        self.prox_threshold_ticks = prox_threshold_ticks
        self.levels: List[MarkedLevel] = []
        self._levels_by_price: Dict[float, MarkedLevel] = {}
        self._tick_size = ES_TICK_SIZE  # default; updated per analyze()

    # ─── Public API ──────────────────────────────────────────────────────

    def analyze(self, candles: List) -> List[MarkedLevel]:
        """
        Run full level detection on a list of candles.
        Returns sorted list of unique MarkedLevel objects.
        """
        if not candles:
            self.levels = []
            return []

        self._tick_size = self._infer_tick_size(candles)
        self.levels = []
        self._levels_by_price = {}

        # 1. Session extremes
        self._detect_session_high_low(candles)

        # 2. Opening range
        self._detect_opening_range(candles)

        # 3. VWAP
        self._detect_vwap(candles)

        # 4. POC (most traded price)
        self._detect_poc(candles)

        # 5. High-volume nodes
        self._detect_high_volume_nodes(candles)

        # Deduplicate near-levels and finalize
        self.levels = self._deduplicate_levels(self.levels)
        self._levels_by_price = {lv.price: lv for lv in self.levels}
        return self.levels

    def is_near_level(self, price: float) -> bool:
        """Return True if price is within prox_threshold_ticks of any marked level."""
        if not self.levels:
            return False
        threshold = self.prox_threshold_ticks * self._tick_size
        return any(abs(price - lv.price) <= threshold for lv in self.levels)

    def get_level_at_price(self, price: float) -> Optional[MarkedLevel]:
        """
        Return the MarkedLevel closest to price, or None if none within threshold.
        """
        if not self.levels:
            return None
        threshold = self.prox_threshold_ticks * self._tick_size
        best = None
        best_dist = float("inf")
        for lv in self.levels:
            dist = abs(price - lv.price)
            if dist <= threshold and dist < best_dist:
                best_dist = dist
                best = lv
        return best

    def get_levels_by_type(self, level_type: str) -> List[MarkedLevel]:
        return [lv for lv in self.levels if lv.level_type == level_type]

    def get_support_levels(self) -> List[MarkedLevel]:
        """Return levels likely to act as support (session low, OR low, VWAP below)."""
        return [
            lv for lv in self.levels
            if lv.level_type in ("session_low", "opening_range_low")
        ]

    def get_resistance_levels(self) -> List[MarkedLevel]:
        """Return levels likely to act as resistance (session high, OR high)."""
        return [
            lv for lv in self.levels
            if lv.level_type in ("session_high", "opening_range_high")
        ]

    # ─── Detection internals ─────────────────────────────────────────────

    def _detect_session_high_low(self, candles: List):
        highs = [c.high for c in candles if hasattr(c, 'high')]
        lows = [c.low for c in candles if hasattr(c, 'low')]
        if not highs or not lows:
            return
        session_high = max(highs)
        session_low = min(lows)
        high_ts = next(
            (c.ts_close for c in candles if getattr(c, 'high', None) == session_high), ""
        )
        low_ts = next(
            (c.ts_close for c in candles if getattr(c, 'low', None) == session_low), ""
        )
        self._add_level(
            MarkedLevel(
                price=session_high,
                level_type="session_high",
                strength=1.0,
                touches=highs.count(session_high),
                ts_init=high_ts,
                ts_last=high_ts,
                metadata={"session_metric": "absolute_high"},
            )
        )
        self._add_level(
            MarkedLevel(
                price=session_low,
                level_type="session_low",
                strength=1.0,
                touches=lows.count(session_low),
                ts_init=low_ts,
                ts_last=low_ts,
                metadata={"session_metric": "absolute_low"},
            )
        )

    def _detect_opening_range(self, candles: List):
        """First N candles define the opening range."""
        n = min(self.opening_range_candles, len(candles))
        if n < 2:
            return
        or_candles = candles[:n]
        or_high = max(c.high for c in or_candles if hasattr(c, 'high'))
        or_low = min(c.low for c in or_candles if hasattr(c, 'low'))
        or_high_ts = or_candles[-1].ts_close if hasattr(or_candles[-1], 'ts_close') else ""
        or_low_ts = or_candles[-1].ts_close if hasattr(or_candles[-1], 'ts_close') else ""

        total_vol = sum(getattr(c, 'total_vol', getattr(c, 'volume', 0)) for c in or_candles)

        self._add_level(
            MarkedLevel(
                price=or_high,
                level_type="opening_range_high",
                strength=0.85,
                touches=sum(1 for c in or_candles if abs(c.high - or_high) < self._tick_size * 2),
                volume=total_vol,
                ts_init=or_high_ts,
                ts_last=or_high_ts,
                metadata={"or_candles": n},
            )
        )
        self._add_level(
            MarkedLevel(
                price=or_low,
                level_type="opening_range_low",
                strength=0.85,
                touches=sum(1 for c in or_candles if abs(c.low - or_low) < self._tick_size * 2),
                volume=total_vol,
                ts_init=or_low_ts,
                ts_last=or_low_ts,
                metadata={"or_candles": n},
            )
        )

    def _detect_vwap(self, candles: List):
        """Calculate VWAP from all candles."""
        total_vol = 0
        total_pv = 0.0
        for c in candles:
            vol = getattr(c, 'total_vol', getattr(c, 'volume', 0))
            if vol <= 0:
                continue
            typical = (getattr(c, 'high', c.close) + getattr(c, 'low', c.close) + c.close) / 3
            total_pv += typical * vol
            total_vol += vol
        if total_vol <= 0:
            return
        vwap = total_pv / total_vol
        vwap = round(vwap / self._tick_size) * self._tick_size
        last_ts = candles[-1].ts_close if hasattr(candles[-1], 'ts_close') else ""
        # touches: how many times price crossed VWAP
        touches = 0
        for i in range(1, len(candles)):
            prev_close = candles[i - 1].close
            curr_close = candles[i].close
            if (prev_close < vwap <= curr_close) or (prev_close > vwap >= curr_close):
                touches += 1

        self._add_level(
            MarkedLevel(
                price=vwap,
                level_type="vwap",
                strength=0.75,
                touches=touches,
                volume=total_vol,
                ts_init=last_ts,
                ts_last=last_ts,
                metadata={"total_volume": total_vol},
            )
        )

    def _detect_poc(self, candles: List):
        """Point of Control = price level with highest total volume."""
        vol_by_price: Dict[float, int] = defaultdict(int)
        for c in candles:
            ladder = getattr(c, 'ladder', None)
            if not ladder:
                continue
            for price, level in ladder.items():
                if hasattr(level, 'total_vol'):
                    vol = level.total_vol
                elif isinstance(level, dict):
                    vol = level.get('total_vol', 0)
                else:
                    vol = 0
                vol_by_price[price] += vol

        if not vol_by_price:
            return
        poc_price = max(vol_by_price, key=vol_by_price.get)
        total_vol = sum(vol_by_price.values())
        last_ts = candles[-1].ts_close if hasattr(candles[-1], 'ts_close') else ""
        # % of total volume at POC
        strength = vol_by_price[poc_price] / max(total_vol, 1)

        self._add_level(
            MarkedLevel(
                price=poc_price,
                level_type="poc",
                strength=round(min(strength * 5, 1.0), 2),  # scale up
                touches=0,
                volume=vol_by_price[poc_price],
                ts_init=last_ts,
                ts_last=last_ts,
                metadata={"poc_volume": vol_by_price[poc_price]},
            )
        )

    def _detect_high_volume_nodes(self, candles: List):
        """Price levels with volume in top percentile."""
        vol_by_price: Dict[float, int] = defaultdict(int)
        for c in candles:
            ladder = getattr(c, 'ladder', None)
            if not ladder:
                continue
            for price, level in ladder.items():
                if hasattr(level, 'total_vol'):
                    vol = level.total_vol
                elif isinstance(level, dict):
                    vol = level.get('total_vol', 0)
                else:
                    vol = 0
                vol_by_price[price] += vol

        if not vol_by_price:
            return
        volumes = sorted(vol_by_price.values())
        idx = int(len(volumes) * self.high_volume_threshold_percentile / 100)
        threshold = volumes[min(idx, len(volumes) - 1)]
        last_ts = candles[-1].ts_close if hasattr(candles[-1], 'ts_close') else ""

        for price, vol in vol_by_price.items():
            if vol >= threshold and vol > 0:
                # Skip if this is already the POC
                poc_levels = [lv for lv in self.levels if lv.level_type == "poc"]
                if poc_levels and abs(price - poc_levels[0].price) < self._tick_size * 2:
                    continue
                self._add_level(
                    MarkedLevel(
                        price=price,
                        level_type="high_volume_node",
                        strength=round(vol / max(threshold, 1), 2),
                        touches=0,
                        volume=vol,
                        ts_init=last_ts,
                        ts_last=last_ts,
                    )
                )

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _add_level(self, level: MarkedLevel):
        """Add to raw list — deduplication happens later."""
        self.levels.append(level)

    def _deduplicate_levels(self, levels: List[MarkedLevel]) -> List[MarkedLevel]:
        """Merge levels that are within prox_threshold_ticks of each other."""
        if not levels:
            return []
        threshold = self.prox_threshold_ticks * self._tick_size
        sorted_levels = sorted(levels, key=lambda x: x.price)
        merged: List[MarkedLevel] = []
        for lv in sorted_levels:
            found = False
            for i, existing in enumerate(merged):
                if abs(lv.price - existing.price) <= threshold:
                    # Keep the stronger one, or merge metadata
                    if lv.strength > existing.strength:
                        merged[i] = MarkedLevel(
                            price=lv.price,
                            level_type=existing.level_type if existing.level_type in ("session_high", "session_low") else lv.level_type,
                            strength=max(lv.strength, existing.strength),
                            touches=existing.touches + lv.touches,
                            volume=existing.volume + lv.volume,
                            ts_init=existing.ts_init or lv.ts_init,
                            ts_last=lv.ts_last or existing.ts_last,
                            metadata={**existing.metadata, **lv.metadata},
                        )
                    else:
                        merged[i] = MarkedLevel(
                            price=existing.price,
                            level_type=existing.level_type if existing.level_type in ("session_high", "session_low") else lv.level_type,
                            strength=max(lv.strength, existing.strength),
                            touches=existing.touches + lv.touches,
                            volume=existing.volume + lv.volume,
                            ts_init=existing.ts_init or lv.ts_init,
                            ts_last=lv.ts_last or existing.ts_last,
                            metadata={**existing.metadata, **lv.metadata},
                        )
                    found = True
                    break
            if not found:
                merged.append(lv)
        return sorted(merged, key=lambda x: x.price)

    def _infer_tick_size(self, candles: List) -> float:
        """Infer ES vs NQ from price magnitude."""
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
    parser.add_argument("--output", default="marked_levels.json")
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    # Reconstruct TickFootprintCandle objects from JSON if needed
    from tick_footprint_builder import TickFootprintBuilder, TickFootprintCandle, FootprintLadder

    candles = []
    for cj in data.get("candles", []):
        c = TickFootprintCandle(
            ts_open=cj.get("ts_open", ""),
            ts_close=cj.get("ts_close", ""),
            open_price=cj.get("open", cj.get("open_price", 0)),
            high=cj.get("high", 0),
            low=cj.get("low", 0),
            close=cj.get("close", 0),
            total_vol=cj.get("total_vol", cj.get("volume", 0)),
            total_delta=cj.get("total_delta", cj.get("delta", 0)),
            aggressive_buy_vol=cj.get("aggressive_buy_vol", 0),
            aggressive_sell_vol=cj.get("aggressive_sell_vol", 0),
            ticks=cj.get("ticks", 0),
        )
        # ladder
        for price, ld in cj.get("ladder", {}).items():
            p = float(price)
            c.ladder[p] = FootprintLadder(
                price=p,
                buy_vol=ld.get("buy_vol", 0),
                sell_vol=ld.get("sell_vol", 0),
                total_vol=ld.get("total_vol", 0),
                delta=ld.get("delta", 0),
            )
        candles.append(c)

    detector = MarkedLevelsDetector()
    levels = detector.analyze(candles)

    result = {
        "count": len(levels),
        "levels": [
            {
                "price": lv.price,
                "type": lv.level_type,
                "strength": lv.strength,
                "touches": lv.touches,
                "volume": lv.volume,
                "ts_init": lv.ts_init,
                "ts_last": lv.ts_last,
                "metadata": lv.metadata,
            }
            for lv in levels
        ],
    }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Marked levels: {len(levels)}")
    for lv in levels:
        print(f"  {lv.level_type:20s} @ {lv.price:>8.2f}  strength={lv.strength:.2f}  touches={lv.touches}")
