"""
Simplified sweep/reclaim detector using Bookmap orderflow JSONL.

Definitions:
  Bullish sweep: price breaks below recent support, liquidity there disappears or
                gets traded through, then reclaims back above support within window.
  Bearish sweep: price pops above resistance, liquidity there disappears or
                absorbs, then rejects back below resistance.
"""
from __future__ import annotations
import json
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Deque
from collections import deque
import statistics

@dataclass
class SweepEvent:
    ts_event: str
    symbol: str
    direction: str        # bullish_sweep | bearish_sweep
    trigger_price: float
    level: float          # support or resistance level
    sweep_distance: float
    reclaim_delay_ms: float
    liquidity_behavior: str
    confidence: float     # 0..1
    side: str = ""
    size: int = 0
    seq: int = 0

@dataclass
class WindowState:
    """Sliding window of recent events for level detection."""
    recent_prices: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    recent_bids: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    recent_asks: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    latest_bid: float = 0.0
    latest_ask: float = 0.0
    latest_bid_size: int = 0
    latest_ask_size: int = 0

class SweepDetector:
    def __init__(self, min_sweep_ticks: float = 1.0, max_sweep_ticks: float = 10.0,
                 reclaim_window_ms: float = 5000.0, tick_size: float = 0.25,
                 lookback_events: int = 100, max_pending_events: int = 30):
        self.min_sweep = min_sweep_ticks * tick_size
        self.max_sweep = max_sweep_ticks * tick_size
        self.reclaim_window_ms = reclaim_window_ms
        self.tick_size = tick_size
        self.lookback = lookback_events
        self.max_pending_events = max_pending_events
        self.state = WindowState()
        self.events: List[Dict] = []
        self.sweeps: List[SweepEvent] = []
        self._pending: Dict[str, Dict] = {}  # level -> pending sweep

    def ingest(self, raw_events: List[Dict]):
        """Process events in strict replay order (must be pre-sorted by seq/timestamp)."""
        self.events = raw_events
        for ev in raw_events:
            self._update_state(ev)
            self._check_sweep(ev)
        return self.sweeps

    def _update_state(self, ev: Dict):
        t = ev.get("event_type", "")
        p = ev.get("price", 0.0) or 0.0
        if t == "trade" and p:
            self.state.recent_prices.append(p)
        elif t == "depth":
            side = ev.get("side", "")
            if side == "bid":
                self.state.latest_bid = p
                self.state.latest_bid_size = ev.get("size", 0) or 0
                self.state.recent_bids.append(p)
            elif side == "ask":
                self.state.latest_ask = p
                self.state.latest_ask_size = ev.get("size", 0) or 0
                self.state.recent_asks.append(p)

    def _expire_pending(self, current_seq: int, current_price: float):
        """Auto-expire stale pending sweeps based on price movement or age."""
        expired = []
        for key, pending in self._pending.items():
            seq_age = current_seq - pending["seq"]
            
            # Criterion 1: Too many events passed (default 30)
            if seq_age > self.max_pending_events:
                expired.append(key)
                continue
            
            # Criterion 2: Price moved significantly away from level
            # For bullish: expire if price went much higher than support
            if pending["type"] == "bullish":
                if current_price > pending["level"] + (self.tick_size * 2):
                    expired.append(key)
                    continue
            # For bearish: expire if price went much lower than resistance
            elif pending["type"] == "bearish":
                if current_price < pending["level"] - (self.tick_size * 2):
                    expired.append(key)
                    continue
        
        for key in expired:
            del self._pending[key]

    def _check_sweep(self, ev: Dict):
        t = ev.get("event_type", "")
        p = ev.get("price", 0.0) or 0.0
        if t != "trade":
            return
        # Determine swing levels from recent prices (EXCLUDE current price)
        prices = list(self.state.recent_prices)
        if len(prices) < 10:
            return
        historical_prices = prices[:-1] if len(prices) > 1 else prices
        
        # Use most-common levels in upper/lower halves for stickier support/resistance
        window = historical_prices[-20:] if len(historical_prices) >= 20 else historical_prices
        sorted_window = sorted(window)
        median_idx = len(sorted_window) // 2
        
        # Support: most common price in lower half
        lower_half = sorted_window[:median_idx + 1]
        from collections import Counter
        support_counts = Counter(lower_half)
        support = support_counts.most_common(1)[0][0]
        
        # Resistance: most common price in upper half
        upper_half = sorted_window[median_idx:]
        resistance_counts = Counter(upper_half)
        resistance = resistance_counts.most_common(1)[0][0]
        
        # Expire stale pending sweeps before checking new ones
        self._expire_pending(ev.get("seq", 0), p)
        
        # Bullish sweep: price drops below support then reclaims
        if p < support - self.min_sweep and p > support - self.max_sweep:
            key = f"support_{support:.2f}"
            if key not in self._pending:
                self._pending[key] = {
                    "type": "bullish",
                    "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p,
                    "level": support,
                    "seq": ev.get("seq", 0),
                }
        # Bearish sweep: price pops above resistance then rejects
        if p > resistance + self.min_sweep and p < resistance + self.max_sweep:
            key = f"resistance_{resistance:.2f}"
            if key not in self._pending:
                self._pending[key] = {
                    "type": "bearish",
                    "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p,
                    "level": resistance,
                    "seq": ev.get("seq", 0),
                }
        # Check reclaims / rejects
        for key, pending in list(self._pending.items()):
            if pending["type"] == "bullish":
                if p >= pending["level"]:
                    # Reclaim confirmed
                    self.sweeps.append(SweepEvent(
                        ts_event=pending["trigger_ts"],
                        symbol=ev.get("symbol", "ES"),
                        direction="bullish_sweep",
                        trigger_price=pending["trigger_price"],
                        level=pending["level"],
                        sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                        reclaim_delay_ms=0.0,
                        liquidity_behavior=f"bid_size={self.state.latest_bid_size}",
                        confidence=0.6,
                        side=ev.get("side", ""),
                        size=ev.get("size", 0) or 0,
                        seq=pending["seq"],
                    ))
                    del self._pending[key]
            elif pending["type"] == "bearish":
                if p <= pending["level"]:
                    self.sweeps.append(SweepEvent(
                        ts_event=pending["trigger_ts"],
                        symbol=ev.get("symbol", "ES"),
                        direction="bearish_sweep",
                        trigger_price=pending["trigger_price"],
                        level=pending["level"],
                        sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                        reclaim_delay_ms=0.0,
                        liquidity_behavior=f"ask_size={self.state.latest_ask_size}",
                        confidence=0.6,
                        side=ev.get("side", ""),
                        size=ev.get("size", 0) or 0,
                        seq=pending["seq"],
                    ))
                    del self._pending[key]


def run_detector(jsonl_path: str) -> List[SweepEvent]:
    events = []
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    det = SweepDetector()
    return det.ingest(events)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="es_sweep_events.csv")
    args = parser.parse_args()
    sweeps = run_detector(args.input)
    with open(args.output, "w") as f:
        f.write("ts_event,symbol,direction,trigger_price,level,sweep_distance,liquidity_behavior,confidence,seq\n")
        for s in sweeps:
            f.write(f"{s.ts_event},{s.symbol},{s.direction},{s.trigger_price},{s.level},"
                    f"{s.sweep_distance},{s.liquidity_behavior},{s.confidence},{s.seq}\n")
    print(f"Detected {len(sweeps)} sweep events. Written to {args.output}")
