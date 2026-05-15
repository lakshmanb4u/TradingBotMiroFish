#!/usr/bin/env python3
"""
v3_alert_engine_human_executable.py

V3 Alert engine for human-executable directional setups.

Design:
- 1 alert every several minutes (not milliseconds)
- Multi-minute persistence (5s+ minimum)
- Larger targets (20-60 ticks, not 2-4)
- Entry zones (not exact ticks)
- Structure-based invalidation (8+ ticks)
- Anti-noise: 30s suppression (not 5s)
- Manually executable timing
- Clear Bookmap visibility
"""

import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
PT = ZoneInfo("America/Los_Angeles")


@dataclass
class BookState:
    """Current best bid/ask with full ladder."""
    bid_ladder: Dict[float, float] = field(default_factory=dict)
    ask_ladder: Dict[float, float] = field(default_factory=dict)
    bid_ts: Optional[str] = None
    ask_ts: Optional[str] = None
    
    def update_bid(self, price: float, size: float):
        if size == 0 and price in self.bid_ladder:
            del self.bid_ladder[price]
        elif size > 0:
            self.bid_ladder[price] = size
    
    def update_ask(self, price: float, size: float):
        if size == 0 and price in self.ask_ladder:
            del self.ask_ladder[price]
        elif size > 0:
            self.ask_ladder[price] = size
    
    def get_best_bid(self) -> Optional[Tuple[float, float]]:
        active = [(p, s) for p, s in self.bid_ladder.items() if s > 0]
        if not active:
            return None
        return max(active, key=lambda x: x[0])
    
    def get_best_ask(self) -> Optional[Tuple[float, float]]:
        active = [(p, s) for p, s in self.ask_ladder.items() if s > 0]
        if not active:
            return None
        return min(active, key=lambda x: x[0])
    
    def get_recent_high(self, window_sec: float = 15.0) -> Optional[float]:
        """Get highest bid over window."""
        now = time.time()
        # Use bid_history (to be populated by engine)
        return None
    
    def get_recent_low(self, window_sec: float = 15.0) -> Optional[float]:
        """Get lowest ask over window."""
        now = time.time()
        # Use ask_history (to be populated by engine)
        return None
    
    def is_valid(self) -> bool:
        return self.get_best_bid() is not None and self.get_best_ask() is not None
    
    def is_crossed(self) -> bool:
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if not best_bid or not best_ask:
            return False
        return best_bid[0] >= best_ask[0]
    
    def spread_ticks(self) -> float:
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if not best_bid or not best_ask:
            return 0
        return (best_ask[0] - best_bid[0]) / 0.25
    
    def imbalance_ratio_buy(self) -> float:
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if not best_bid or not best_ask or best_ask[1] == 0:
            return 0.0
        return best_bid[1] / best_ask[1]
    
    def imbalance_ratio_sell(self) -> float:
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if not best_bid or not best_ask or best_bid[1] == 0:
            return 0.0
        return best_ask[1] / best_bid[1]
    
    def event_age_ms(self, ts_recv: str) -> float:
        try:
            dt = datetime.fromisoformat(ts_recv.replace('Z', '+00:00'))
            now = datetime.now(UTC)
            age = (now - dt).total_seconds() * 1000
            return max(0.0, age)
        except:
            return 0.0


@dataclass
class V3Alert:
    """Human-executable directional alert."""
    direction: str  # BUY or SELL
    ts_pdt: str
    entry_zone_low: float
    entry_zone_high: float
    invalidation: float
    target1: float
    target2: float
    current_bid: float
    current_ask: float
    spread_ticks: float
    persistence_ms: float
    imbalance_ratio: float
    liquidity_context: str
    reason: str
    expected_hold_minutes: float
    human_score: float  # 0-100 suitability


class V3HumanExecutableEngine:
    """V3 engine for human-executable alerts."""
    
    def __init__(self):
        """Initialize."""
        self.book = BookState()
        
        # Thresholds
        self.imbalance_threshold = 2.5  # Higher threshold for cleaner signals
        self.persistence_threshold_ms = 5000  # 5 seconds minimum
        self.spread_threshold_ticks = 4  # Prefer tight spreads
        self.event_age_threshold_ms = 2000
        
        # Anti-noise: 30 second suppression (vs 5s in V2)
        self.buy_suppression_until = 0.0
        self.sell_suppression_until = 0.0
        self.suppression_duration_sec = 30.0  # Much longer suppression
        self.suppression_override_imbalance = 8.0  # High threshold to override
        
        # Cooldowns
        self.last_buy_alert_time = 0.0
        self.last_sell_alert_time = 0.0
        self.cooldown_sec = 120.0  # Longer cooldown for human execution
        
        # Persistence tracking (same as V2)
        self.bid_dominance_start: Optional[float] = None
        self.ask_dominance_start: Optional[float] = None
        
        # Price history for structure detection
        self.bid_history: List[Tuple[float, float]] = []  # (price, time)
        self.ask_history: List[Tuple[float, float]] = []
        self.recent_bids: List[float] = []
        self.recent_asks: List[float] = []
        
        self.alerts: List[V3Alert] = []
        self.max_alerts = 5  # Much fewer alerts (quality focus)
        
        logger.info("V3HumanExecutableEngine initialized (5s persistence, 30s suppression, larger targets)")
    
    def _is_bid_dominant(self) -> bool:
        if not self.book.is_valid():
            return False
        return self.book.imbalance_ratio_buy() >= self.imbalance_threshold
    
    def _is_ask_dominant(self) -> bool:
        if not self.book.is_valid():
            return False
        return self.book.imbalance_ratio_sell() >= self.imbalance_threshold
    
    def _bid_advancing(self, window_sec: float = 10.0) -> bool:
        """Is bid moving up in dominance period?"""
        if len(self.recent_bids) < 3:
            return False
        recent_5 = self.recent_bids[-5:] if len(self.recent_bids) >= 5 else self.recent_bids
        if not recent_5:
            return False
        return recent_5[-1] > recent_5[0]
    
    def _ask_declining(self, window_sec: float = 10.0) -> bool:
        """Is ask moving down in dominance period?"""
        if len(self.recent_asks) < 3:
            return False
        recent_5 = self.recent_asks[-5:] if len(self.recent_asks) >= 5 else self.recent_asks
        if not recent_5:
            return False
        return recent_5[-1] < recent_5[0]
    
    def _detect_liquidity_context(self, direction: str) -> str:
        """Detect liquidity structure (sweeps, absorption, etc)."""
        # Placeholder - in production would analyze detailed orderflow
        if direction == "BUY":
            if len(self.recent_bids) >= 3:
                if self.recent_bids[-1] > self.recent_bids[-2]:
                    return "absorption_after_rejection"
                elif min(self.recent_bids[-3:]) < min(self.recent_bids[-10:] if len(self.recent_bids) >= 10 else self.recent_bids):
                    return "sweep_continuation"
            return "sustained_dominance"
        else:
            if len(self.recent_asks) >= 3:
                if self.recent_asks[-1] < self.recent_asks[-2]:
                    return "absorption_after_rejection"
                elif max(self.recent_asks[-3:]) > max(self.recent_asks[-10:] if len(self.recent_asks) >= 10 else self.recent_asks):
                    return "sweep_continuation"
            return "sustained_dominance"
    
    def _check_buy_persistence(self) -> Tuple[bool, float]:
        """Check if bid dominance has persisted >= 5 seconds."""
        now = time.time()
        
        if not self._is_bid_dominant():
            self.bid_dominance_start = None
            return False, 0.0
        
        if self.bid_dominance_start is None:
            self.bid_dominance_start = now
            return False, 0.0
        
        persistence_ms = (now - self.bid_dominance_start) * 1000
        
        if persistence_ms >= self.persistence_threshold_ms:
            if self._bid_advancing():
                return True, persistence_ms
        
        return False, persistence_ms
    
    def _check_sell_persistence(self) -> Tuple[bool, float]:
        """Check if ask dominance has persisted >= 5 seconds."""
        now = time.time()
        
        if not self._is_ask_dominant():
            self.ask_dominance_start = None
            return False, 0.0
        
        if self.ask_dominance_start is None:
            self.ask_dominance_start = now
            return False, 0.0
        
        persistence_ms = (now - self.ask_dominance_start) * 1000
        
        if persistence_ms >= self.persistence_threshold_ms:
            if self._ask_declining():
                return True, persistence_ms
        
        return False, persistence_ms
    
    def _should_buy_suppression_block(self) -> bool:
        now = time.time()
        if now < self.buy_suppression_until:
            imbalance = self.book.imbalance_ratio_buy()
            if imbalance < self.suppression_override_imbalance:
                return True
        return False
    
    def _should_sell_suppression_block(self) -> bool:
        now = time.time()
        if now < self.sell_suppression_until:
            imbalance = self.book.imbalance_ratio_sell()
            if imbalance < self.suppression_override_imbalance:
                return True
        return False
    
    def _should_cooldown_block(self, direction: str) -> bool:
        now = time.time()
        if direction == "BUY":
            return (now - self.last_buy_alert_time) < self.cooldown_sec
        elif direction == "SELL":
            return (now - self.last_sell_alert_time) < self.cooldown_sec
        return False
    
    def _calculate_human_score(self, direction: str, persistence_ms: float, imbalance: float, spread: float) -> float:
        """Score alert for human execution suitability (0-100)."""
        score = 50.0
        
        # Persistence bonus (major factor)
        if persistence_ms >= 10000:  # 10s+
            score += 30
        elif persistence_ms >= 5000:  # 5s+
            score += 15
        
        # Imbalance bonus
        if imbalance >= 4.0:
            score += 15
        elif imbalance >= 2.5:
            score += 5
        
        # Spread penalty
        if spread <= 2:
            score += 10
        elif spread > 4:
            score -= 5
        
        return min(100.0, max(0.0, score))
    
    def evaluate_buy(self, ts_recv: str) -> Tuple[bool, str, float]:
        """Evaluate BUY alert."""
        
        if not self.book.is_valid():
            return False, "INVALID_BOOK", 0.0
        
        if self.book.is_crossed():
            return False, "CROSSED_BOOK", 0.0
        
        if self.book.spread_ticks() > self.spread_threshold_ticks:
            return False, f"SPREAD_WIDE({self.book.spread_ticks():.1f})", 0.0
        
        age_ms = self.book.event_age_ms(ts_recv)
        if age_ms > self.event_age_threshold_ms:
            return False, f"STALE({age_ms:.0f}ms)", 0.0
        
        imbalance = self.book.imbalance_ratio_buy()
        if imbalance < self.imbalance_threshold:
            return False, f"IMBAL_LOW({imbalance:.2f})", 0.0
        
        persistent, persistence_ms = self._check_buy_persistence()
        if not persistent:
            return False, f"NO_PERSIST({persistence_ms:.0f}ms)", persistence_ms
        
        if self._should_buy_suppression_block():
            return False, "SUPPRESSED(30s)", persistence_ms
        
        if self._should_cooldown_block("BUY"):
            return False, "COOLDOWN(120s)", persistence_ms
        
        best_bid = self.book.get_best_bid()
        best_ask = self.book.get_best_ask()
        if best_bid[0] % 0.25 != 0 or best_ask[0] % 0.25 != 0:
            return False, "TICK_MISALIGN", persistence_ms
        
        return True, "PASS", persistence_ms
    
    def evaluate_sell(self, ts_recv: str) -> Tuple[bool, str, float]:
        """Evaluate SELL alert."""
        
        if not self.book.is_valid():
            return False, "INVALID_BOOK", 0.0
        
        if self.book.is_crossed():
            return False, "CROSSED_BOOK", 0.0
        
        if self.book.spread_ticks() > self.spread_threshold_ticks:
            return False, f"SPREAD_WIDE({self.book.spread_ticks():.1f})", 0.0
        
        age_ms = self.book.event_age_ms(ts_recv)
        if age_ms > self.event_age_threshold_ms:
            return False, f"STALE({age_ms:.0f}ms)", 0.0
        
        imbalance = self.book.imbalance_ratio_sell()
        if imbalance < self.imbalance_threshold:
            return False, f"IMBAL_LOW({imbalance:.2f})", 0.0
        
        persistent, persistence_ms = self._check_sell_persistence()
        if not persistent:
            return False, f"NO_PERSIST({persistence_ms:.0f}ms)", persistence_ms
        
        if self._should_sell_suppression_block():
            return False, "SUPPRESSED(30s)", persistence_ms
        
        if self._should_cooldown_block("SELL"):
            return False, "COOLDOWN(120s)", persistence_ms
        
        best_bid = self.book.get_best_bid()
        best_ask = self.book.get_best_ask()
        if best_bid[0] % 0.25 != 0 or best_ask[0] % 0.25 != 0:
            return False, "TICK_MISALIGN", persistence_ms
        
        return True, "PASS", persistence_ms
    
    def process_event(self, obj: Dict) -> Optional[V3Alert]:
        """Process one event."""
        
        side = obj.get('side')
        price = obj.get('price')
        size = obj.get('size')
        ts_recv = obj.get('ts_recv')
        
        if not all([side, price is not None, size is not None, ts_recv]):
            return None
        
        now = time.time()
        if side == 'bid':
            self.book.update_bid(price, size)
            self.book.bid_ts = ts_recv
            self.bid_history.append((price, now))
            self.recent_bids.append(price)
            if len(self.bid_history) > 500:
                self.bid_history.pop(0)
            if len(self.recent_bids) > 100:
                self.recent_bids.pop(0)
        elif side == 'ask':
            self.book.update_ask(price, size)
            self.book.ask_ts = ts_recv
            self.ask_history.append((price, now))
            self.recent_asks.append(price)
            if len(self.ask_history) > 500:
                self.ask_history.pop(0)
            if len(self.recent_asks) > 100:
                self.recent_asks.pop(0)
        else:
            return None
        
        # Evaluate BUY
        buy_passes, buy_reason, buy_persistence = self.evaluate_buy(ts_recv)
        if buy_passes and len(self.alerts) < self.max_alerts:
            best_bid = self.book.get_best_bid()
            best_ask = self.book.get_best_ask()
            
            dt = datetime.fromisoformat(ts_recv.replace('Z', '+00:00'))
            dt_pdt = dt.astimezone(PT)
            
            # Entry zone: best_ask to 1 tick above
            entry_low = best_ask[0]
            entry_high = best_ask[0] + 0.25
            
            # Stop: 8 ticks below entry
            stop = entry_low - 2.00
            
            # Targets: 20-60 ticks (structure-based)
            target1 = entry_low + 5.00  # 20 ticks
            target2 = entry_low + 15.00  # 60 ticks
            
            imbalance = self.book.imbalance_ratio_buy()
            liquidity_ctx = self._detect_liquidity_context("BUY")
            human_score = self._calculate_human_score("BUY", buy_persistence, imbalance, self.book.spread_ticks())
            expected_hold = buy_persistence / 60000  # Convert to minutes, then estimate 2-3x hold
            
            alert = V3Alert(
                direction="BUY",
                ts_pdt=dt_pdt.isoformat(),
                entry_zone_low=entry_low,
                entry_zone_high=entry_high,
                invalidation=stop,
                target1=target1,
                target2=target2,
                current_bid=best_bid[0],
                current_ask=best_ask[0],
                spread_ticks=self.book.spread_ticks(),
                persistence_ms=buy_persistence,
                imbalance_ratio=imbalance,
                liquidity_context=liquidity_ctx,
                reason=buy_reason,
                expected_hold_minutes=max(5.0, expected_hold * 3),
                human_score=human_score
            )
            
            self.alerts.append(alert)
            self.last_buy_alert_time = now
            self.sell_suppression_until = now + self.suppression_duration_sec
            return alert
        
        # Evaluate SELL
        sell_passes, sell_reason, sell_persistence = self.evaluate_sell(ts_recv)
        if sell_passes and len(self.alerts) < self.max_alerts:
            best_bid = self.book.get_best_bid()
            best_ask = self.book.get_best_ask()
            
            dt = datetime.fromisoformat(ts_recv.replace('Z', '+00:00'))
            dt_pdt = dt.astimezone(PT)
            
            # Entry zone: best_bid to 1 tick below
            entry_high = best_bid[0]
            entry_low = best_bid[0] - 0.25
            
            # Stop: 8 ticks above entry
            stop = entry_high + 2.00
            
            # Targets: 20-60 ticks (structure-based)
            target1 = entry_high - 5.00  # 20 ticks
            target2 = entry_high - 15.00  # 60 ticks
            
            imbalance = self.book.imbalance_ratio_sell()
            liquidity_ctx = self._detect_liquidity_context("SELL")
            human_score = self._calculate_human_score("SELL", sell_persistence, imbalance, self.book.spread_ticks())
            expected_hold = sell_persistence / 60000
            
            alert = V3Alert(
                direction="SELL",
                ts_pdt=dt_pdt.isoformat(),
                entry_zone_low=entry_low,
                entry_zone_high=entry_high,
                invalidation=stop,
                target1=target1,
                target2=target2,
                current_bid=best_bid[0],
                current_ask=best_ask[0],
                spread_ticks=self.book.spread_ticks(),
                persistence_ms=sell_persistence,
                imbalance_ratio=imbalance,
                liquidity_context=liquidity_ctx,
                reason=sell_reason,
                expected_hold_minutes=max(5.0, expected_hold * 3),
                human_score=human_score
            )
            
            self.alerts.append(alert)
            self.last_sell_alert_time = now
            self.buy_suppression_until = now + self.suppression_duration_sec
            return alert
        
        return None
    
    def print_alerts(self):
        """Print all alerts."""
        
        if not self.alerts:
            print("\n❌ No alerts generated\n")
            return
        
        print("\n" + "="*140)
        print("V3 HUMAN-EXECUTABLE DIRECTIONAL ALERTS")
        print("="*140 + "\n")
        
        for i, alert in enumerate(self.alerts, 1):
            print(f"\n[Alert {i}/{len(self.alerts)}] {'⭐' if alert.human_score >= 70 else '✓'}")
            print(f"  Time:              {alert.ts_pdt}")
            print(f"  Direction:         {alert.direction}")
            print(f"  Entry Zone:        {alert.entry_zone_low:.2f} - {alert.entry_zone_high:.2f}")
            print(f"  Stop (Invalid):    {alert.invalidation:.2f} ({abs(alert.invalidation - alert.entry_zone_high) / 0.25:.0f}t)")
            print(f"  Target 1:          {alert.target1:.2f} ({abs(alert.target1 - alert.entry_zone_high) / 0.25:.0f}t)")
            print(f"  Target 2:          {alert.target2:.2f} ({abs(alert.target2 - alert.entry_zone_high) / 0.25:.0f}t)")
            print(f"  Current Bid/Ask:   {alert.current_bid:.2f} / {alert.current_ask:.2f}")
            print(f"  Spread:            {alert.spread_ticks:.1f} ticks")
            print(f"  Persistence:       {alert.persistence_ms/1000:.1f} seconds")
            print(f"  Imbalance:         {alert.imbalance_ratio:.2f}x")
            print(f"  Liquidity Context: {alert.liquidity_context}")
            print(f"  Expected Hold:     {alert.expected_hold_minutes:.0f} minutes")
            print(f"  Human Score:       {alert.human_score:.0f}/100")
            print(f"  Reason:            {alert.reason}")
        
        print("\n" + "="*140)
        print(f"Total alerts: {len(self.alerts)}")
        print("="*140 + "\n")
    
    def export_alerts_csv(self):
        """Export alerts as CSV."""
        
        out_dir = Path.home() / ".openclaw" / "workspace" / "state" / "orderflow" / "live"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "v3_human_alerts.csv"
        
        with open(csv_path, 'w') as f:
            f.write("timestamp_pdt,direction,entry_zone_low,entry_zone_high,invalidation,target1,target2,bid,ask,spread_ticks,persistence_ms,imbalance,liquidity_context,expected_hold_min,human_score,reason\n")
            for alert in self.alerts:
                f.write(
                    f"{alert.ts_pdt},{alert.direction},"
                    f"{alert.entry_zone_low:.2f},{alert.entry_zone_high:.2f},"
                    f"{alert.invalidation:.2f},{alert.target1:.2f},{alert.target2:.2f},"
                    f"{alert.current_bid:.2f},{alert.current_ask:.2f},"
                    f"{alert.spread_ticks:.1f},{alert.persistence_ms:.0f},"
                    f"{alert.imbalance_ratio:.2f},{alert.liquidity_context},"
                    f"{alert.expected_hold_minutes:.0f},{alert.human_score:.0f},"
                    f"{alert.reason}\n"
                )
        
        print(f"✅ Exported {len(self.alerts)} alerts to {csv_path}")
        return csv_path
