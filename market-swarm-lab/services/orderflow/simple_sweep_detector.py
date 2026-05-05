#!/usr/bin/env python3
"""
services/orderflow/simple_sweep_detector.py
Minimal replay-safe sweep/reclaim detector.

Bullish sweep:
  - price breaks below recent support
  - liquidity below gets traded through or disappears
  - reclaim back above support within configurable window

Bearish sweep:
  - price breaks above resistance
  - liquidity above disappears or absorbs
  - reject back below resistance within configurable window

No delta logic yet. No imbalance logic yet. No MBO logic yet.
"""
import dataclasses
from typing import List, Optional, Iterator
import pandas as pd


@dataclasses.dataclass
class SweepEvent:
    timestamp_ns: int
    level: float
    direction: str          # "bullish" or "bearish"
    sweep_distance: float   # how far price went past level
    reclaim_delay_ns: int   # time to reclaim (0 if not reclaimed)
    liquidity_behavior: str # "absorbed", "disappeared", "traded_through"
    confidence: str         # "high", "medium", "low"


class SimpleSweepDetector:
    def __init__(self, lookback_bars: int = 10, reclaim_window_ns: int = 60_000_000_000):
        """
        Args:
            lookback_bars: number of prior bars to establish support/resistance
            reclaim_window_ns: nanoseconds allowed for reclaim after sweep
        """
        self.lookback_bars = lookback_bars
        self.reclaim_window_ns = reclaim_window_ns
        self.history: List[dict] = []
        self.events: List[SweepEvent] = []

    def _support(self, prices: List[float]) -> float:
        return min(prices)

    def _resistance(self, prices: List[float]) -> float:
        return max(prices)

    def on_bar(self, ts_ns: int, open_p: float, high: float, low: float, close: float,
               bid: Optional[float] = None, ask: Optional[float] = None,
               bid_size: Optional[float] = None, ask_size: Optional[float] = None):
        """
        Process one OHLC bar (or trade/quote tick).
        For tick-level data, pass high=low=close=price.
        """
        bar = {
            "ts_ns": ts_ns,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
        }
        self.history.append(bar)

        if len(self.history) < self.lookback_bars + 1:
            return None  # not enough history

        # Establish levels from lookback period (excluding current bar)
        recent_lows = [b["low"] for b in self.history[-self.lookback_bars - 1:-1]]
        recent_highs = [b["high"] for b in self.history[-self.lookback_bars - 1:-1]]
        support = self._support(recent_lows)
        resistance = self._resistance(recent_highs)

        current = bar

        # Bullish sweep: low breaks below support, then close reclaims above
        if current["low"] < support:
            swept = True
            sweep_dist = support - current["low"]

            # Liquidity behavior inference
            liq_behavior = "traded_through"
            if bid is not None and bid > support:
                liq_behavior = "absorbed"  # bid stayed above support -> absorbed
            elif bid_size is not None and bid_size == 0:
                liq_behavior = "disappeared"

            # Reclaim check: current bar close reclaims
            reclaimed = current["close"] > support
            reclaim_delay = 0 if reclaimed else None  # not yet reclaimed

            if reclaimed:
                evt = SweepEvent(
                    timestamp_ns=ts_ns,
                    level=support,
                    direction="bullish",
                    sweep_distance=round(sweep_dist, 4),
                    reclaim_delay_ns=0,
                    liquidity_behavior=liq_behavior,
                    confidence="high" if sweep_dist > 0.25 else "medium",
                )
                self.events.append(evt)
                return evt

        # Bearish sweep: high breaks above resistance, then close rejects below
        if current["high"] > resistance:
            sweep_dist = current["high"] - resistance

            liq_behavior = "traded_through"
            if ask is not None and ask < resistance:
                liq_behavior = "absorbed"
            elif ask_size is not None and ask_size == 0:
                liq_behavior = "disappeared"

            reclaimed = current["close"] < resistance
            if reclaimed:
                evt = SweepEvent(
                    timestamp_ns=ts_ns,
                    level=resistance,
                    direction="bearish",
                    sweep_distance=round(sweep_dist, 4),
                    reclaim_delay_ns=0,
                    liquidity_behavior=liq_behavior,
                    confidence="high" if sweep_dist > 0.25 else "medium",
                )
                self.events.append(evt)
                return evt

        return None

    def run(self, df: pd.DataFrame) -> List[SweepEvent]:
        """
        Run detector over a DataFrame.
        Expected columns: timestamp_ns, price, bid, ask, bid_size, ask_size (all optional except timestamp_ns, price)
        """
        for _, row in df.iterrows():
            self.on_bar(
                ts_ns=int(row["timestamp_ns"]),
                open_p=row.get("price", row["price"]),
                high=row.get("price", row["price"]),
                low=row.get("price", row["price"]),
                close=row.get("price", row["price"]),
                bid=row.get("bid"),
                ask=row.get("ask"),
                bid_size=row.get("bid_size"),
                ask_size=row.get("ask_size"),
            )
        return self.events

    def to_dataframe(self) -> pd.DataFrame:
        if not self.events:
            return pd.DataFrame()
        records = [dataclasses.asdict(e) for e in self.events]
        return pd.DataFrame(records)
