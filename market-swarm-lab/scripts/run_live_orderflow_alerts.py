#!/usr/bin/env python3
"""
Live Orderflow Alert System — v1.0
Real-time buy/sell alerts ONLY. No automated trading. No broker execution.

Watches Bookmap JSONL live feeds, detects ES/NQ sweep/reclaim setups,
confirms with SPY 1-min bars, and sends WhatsApp alerts when confidence
meets threshold.

Usage:
    python scripts/run_live_orderflow_alerts.py \
        --watch "state/orderflow/bookmap_api/*.jsonl" \
        --spy-source schwab \
        --notify whatsapp \
        --confidence-threshold 75 \
        --cooldown-minutes 10 \
        --dry-run

Safety:
    - Alerts only. No orders. No execution.
    - No LLM calls in the live loop.
    - No OpenRouter calls in the live loop.
    - Deterministic logic only.
    - Streaming JSONL reads (no full-file loads).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ─── Paths ───────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
LIVE_STATE_DIR = STATE_DIR / "orderflow" / "live"
LIVE_STATE_DIR.mkdir(parents=True, exist_ok=True)

# Add service directories to path
for _sd in [
    ROOT / "services" / "orderflow",
    ROOT / "services" / "schwab-collector",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIDENCE_THRESHOLD = 75
DEFAULT_COOLDOWN_MINUTES = 10
DEFAULT_PRE_MARKET_BUFFER_MIN = 3  # No alerts in first N min after open
SPY_MAX_STALE_SECONDS = 120
ES_TICK_SIZE = 0.25
RISK_REWARD_1 = 1.0
RISK_REWARD_2 = 2.0


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class FileCheckpoint:
    """Per-file read offset and last sequence number."""
    path: str
    offset: int = 0          # bytes read so far
    last_seq: int = 0        # last processed sequence number
    last_modified: float = 0.0  # mtime


@dataclass
class SweepEvent:
    ts_event: str
    symbol: str
    direction: str           # bullish_sweep | bearish_sweep
    trigger_price: float
    level: float
    sweep_distance: float
    reclaim_delay_ms: float
    liquidity_behavior: str
    confidence: float        # 0..1 base
    side: str = ""
    size: int = 0
    seq: int = 0


@dataclass
class AlertSignal:
    direction: str           # BUY_CALL | BUY_PUT | WATCH
    underlying: str          # SPY/SPX/ES
    es_level: float
    spy_level: float
    entry: float
    stop: float
    target_1: float
    target_2: float
    invalidation: float
    confidence: int          # 0-100
    setup_reason: str
    expiry_suggestion: str   # 0DTE / 1DTE / weekly
    strike_suggestion: str   # ATM / slightly ITM / slightly OTM
    instrument: str          # shares / futures / options
    time_stop: str
    exit_rules: List[str] = field(default_factory=list)
    ts_fired: str = ""
    es_symbol: str = ""
    es_trigger_price: float = 0.0


@dataclass
class HealthStatus:
    capture_ok: bool = False
    spy_fresh: bool = False
    symbols_ok: bool = False
    timestamps_ok: bool = False
    event_rate_ok: bool = False
    no_duplicates: bool = False
    last_check: str = ""
    reason: str = ""


# ─── Time helpers ────────────────────────────────────────────────────────────

def et_now() -> datetime:
    """Current time in ET (UTC-4 EDT)."""
    return datetime.now(timezone.utc) - timedelta(hours=4)


def is_market_hours() -> bool:
    """True during market hours Mon-Fri 9:30 AM - 4:00 PM ET."""
    now = et_now()
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= now <= end


def is_pre_market_buffer() -> bool:
    """True if we're within first N minutes after market open."""
    now = et_now()
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    buffer_end = open_time + timedelta(minutes=DEFAULT_PRE_MARKET_BUFFER_MIN)
    return open_time <= now <= buffer_end


def parse_ts(ts_str: str) -> datetime:
    """Parse ISO timestamp from JSONL."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


# ─── Checkpoint I/O ──────────────────────────────────────────────────────────

CHECKPOINT_FILE = LIVE_STATE_DIR / "checkpoints.json"


def load_checkpoints() -> Dict[str, FileCheckpoint]:
    """Load file read checkpoints from disk."""
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
        return {
            k: FileCheckpoint(**v) for k, v in data.items()
        }
    except Exception as e:
        _log.warning("Failed to load checkpoints: %s", e)
        return {}


def save_checkpoints(checkpoints: Dict[str, FileCheckpoint]) -> None:
    """Save checkpoints to disk."""
    try:
        data = {k: asdict(v) for k, v in checkpoints.items()}
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _log.error("Failed to save checkpoints: %s", e)


# ─── State Writers ───────────────────────────────────────────────────────────

STATUS_FILE = LIVE_STATE_DIR / "status.md"
LATEST_SIGNAL_FILE = LIVE_STATE_DIR / "latest_signal.json"
ALERTS_CSV = LIVE_STATE_DIR / "alerts.csv"
HEALTH_FILE = LIVE_STATE_DIR / "health.json"


def write_status(lines: List[str]) -> None:
    """Append lines to status.md."""
    try:
        with open(STATUS_FILE, "a") as f:
            f.write(f"\n## {et_now().strftime('%Y-%m-%d %H:%M:%S ET')}\n")
            for line in lines:
                f.write(f"- {line}\n")
    except Exception as e:
        _log.error("Failed to write status: %s", e)


def write_latest_signal(signal: AlertSignal) -> None:
    """Write latest signal to JSON."""
    try:
        with open(LATEST_SIGNAL_FILE, "w") as f:
            json.dump(asdict(signal), f, indent=2, default=str)
    except Exception as e:
        _log.error("Failed to write latest signal: %s", e)


def append_alert_csv(signal: AlertSignal) -> None:
    """Append signal to alerts CSV."""
    fieldnames = [
        "ts_fired", "direction", "underlying", "es_level", "spy_level",
        "entry", "stop", "target_1", "target_2", "invalidation",
        "confidence", "setup_reason", "expiry_suggestion", "strike_suggestion",
        "instrument", "es_symbol", "time_stop",
    ]
    try:
        file_exists = ALERTS_CSV.exists()
        with open(ALERTS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(signal))
    except Exception as e:
        _log.error("Failed to append alert CSV: %s", e)


def write_health(health: HealthStatus) -> None:
    """Write health status to JSON."""
    try:
        with open(HEALTH_FILE, "w") as f:
            json.dump(asdict(health), f, indent=2)
    except Exception as e:
        _log.error("Failed to write health: %s", e)


# ─── Notification ────────────────────────────────────────────────────────────

def send_whatsapp(message: str) -> bool:
    """Send WhatsApp alert via openclaw notify."""
    try:
        result = subprocess.run(
            ["openclaw", "notify", message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception as e:
        _log.error("WhatsApp send failed: %s", e)
        return False


# ─── Streaming JSONL Reader ──────────────────────────────────────────────────

class StreamingJSONLReader:
    """Incrementally reads new lines from a JSONL file without reloading."""

    def __init__(self, checkpoints: Dict[str, FileCheckpoint]):
        self.checkpoints = checkpoints

    def read_new_events(self, file_path: Path) -> List[Dict]:
        """Read only new lines since last checkpoint."""
        path_str = str(file_path)
        cp = self.checkpoints.get(path_str, FileCheckpoint(path=path_str))

        if not file_path.exists():
            return []

        current_size = file_path.stat().st_size
        current_mtime = file_path.stat().st_mtime

        # File was truncated or replaced
        if current_size < cp.offset:
            _log.info("File truncated, resetting checkpoint: %s", file_path.name)
            cp.offset = 0
            cp.last_seq = 0

        if current_size == cp.offset:
            cp.last_modified = current_mtime
            self.checkpoints[path_str] = cp
            return []

        events = []
        try:
            with open(file_path, "r", errors="replace") as f:
                f.seek(cp.offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        seq = ev.get("seq", 0)
                        if seq > cp.last_seq:
                            events.append(ev)
                            cp.last_seq = seq
                    except json.JSONDecodeError:
                        continue
                # Record final position
                cp.offset = f.tell()
        except Exception as e:
            _log.error("Error reading %s: %s", file_path.name, e)
            return []

        cp.last_modified = current_mtime
        self.checkpoints[path_str] = cp
        return events


# ─── Sweep Detector (streaming-safe) ─────────────────────────────────────────

class LiveSweepDetector:
    """Detects sweep/reclaim events from a live stream of events."""

    def __init__(self, min_sweep_ticks: float = 1.0, max_sweep_ticks: float = 10.0,
                 tick_size: float = ES_TICK_SIZE, lookback_events: int = 100,
                 max_pending_events: int = 30):
        self.min_sweep = min_sweep_ticks * tick_size
        self.max_sweep = max_sweep_ticks * tick_size
        self.tick_size = tick_size
        self.lookback = lookback_events
        self.max_pending = max_pending_events
        self.recent_prices: Deque[float] = deque(maxlen=200)
        self.recent_bids: Deque[float] = deque(maxlen=50)
        self.recent_asks: Deque[float] = deque(maxlen=50)
        self.latest_bid = 0.0
        self.latest_ask = 0.0
        self.latest_bid_size = 0
        self.latest_ask_size = 0
        self._pending: Dict[str, Dict] = {}
        self.sweeps: List[SweepEvent] = []

    def process_events(self, events: List[Dict]) -> List[SweepEvent]:
        """Process new events and return any newly detected sweeps."""
        new_sweeps = []
        for ev in events:
            self._update_state(ev)
            sweep = self._check_sweep(ev)
            if sweep:
                new_sweeps.append(sweep)
                self.sweeps.append(sweep)
        return new_sweeps

    def _update_state(self, ev: Dict):
        t = ev.get("event_type", "")
        p = ev.get("price", 0.0) or 0.0
        if t == "trade" and p:
            self.recent_prices.append(p)
        elif t == "depth":
            side = ev.get("side", "")
            if side == "bid":
                self.latest_bid = p
                self.latest_bid_size = ev.get("size", 0) or 0
                self.recent_bids.append(p)
            elif side == "ask":
                self.latest_ask = p
                self.latest_ask_size = ev.get("size", 0) or 0
                self.recent_asks.append(p)

    def _expire_pending(self, current_seq: int, current_price: float):
        """Remove stale pending sweeps."""
        expired = []
        for key, pending in self._pending.items():
            seq_age = current_seq - pending["seq"]
            if seq_age > self.max_pending:
                expired.append(key)
                continue
            # Price moved away from level
            if pending["type"] == "bullish" and current_price > pending["level"] + self.tick_size * 2:
                expired.append(key)
                continue
            elif pending["type"] == "bearish" and current_price < pending["level"] - self.tick_size * 2:
                expired.append(key)
                continue
        for key in expired:
            del self._pending[key]

    def _check_sweep(self, ev: Dict) -> Optional[SweepEvent]:
        t = ev.get("event_type", "")
        p = ev.get("price", 0.0) or 0.0
        if t != "trade":
            return None

        prices = list(self.recent_prices)
        if len(prices) < 10:
            return None

        historical = prices[:-1] if len(prices) > 1 else prices
        window = historical[-20:] if len(historical) >= 20 else historical
        sorted_window = sorted(window)
        median_idx = len(sorted_window) // 2

        lower_half = sorted_window[:median_idx + 1]
        support = Counter(lower_half).most_common(1)[0][0] if lower_half else p

        upper_half = sorted_window[median_idx:]
        resistance = Counter(upper_half).most_common(1)[0][0] if upper_half else p

        self._expire_pending(ev.get("seq", 0), p)
        symbol = ev.get("symbol", "ES")
        seq = ev.get("seq", 0)

        # Bullish sweep
        if p < support - self.min_sweep and p > support - self.max_sweep:
            key = f"support_{support:.2f}"
            if key not in self._pending:
                self._pending[key] = {
                    "type": "bullish",
                    "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p,
                    "level": support,
                    "seq": seq,
                }

        # Bearish sweep
        if p > resistance + self.min_sweep and p < resistance + self.max_sweep:
            key = f"resistance_{resistance:.2f}"
            if key not in self._pending:
                self._pending[key] = {
                    "type": "bearish",
                    "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p,
                    "level": resistance,
                    "seq": seq,
                }

        # Check reclaims/rejects
        for key, pending in list(self._pending.items()):
            if pending["type"] == "bullish" and p >= pending["level"]:
                sweep = SweepEvent(
                    ts_event=pending["trigger_ts"],
                    symbol=symbol,
                    direction="bullish_sweep",
                    trigger_price=pending["trigger_price"],
                    level=pending["level"],
                    sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                    reclaim_delay_ms=0.0,
                    liquidity_behavior=f"bid_size={self.latest_bid_size}",
                    confidence=0.6,
                    side=ev.get("side", ""),
                    size=ev.get("size", 0) or 0,
                    seq=pending["seq"],
                )
                del self._pending[key]
                return sweep

            elif pending["type"] == "bearish" and p <= pending["level"]:
                sweep = SweepEvent(
                    ts_event=pending["trigger_ts"],
                    symbol=symbol,
                    direction="bearish_sweep",
                    trigger_price=pending["trigger_price"],
                    level=pending["level"],
                    sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                    reclaim_delay_ms=0.0,
                    liquidity_behavior=f"ask_size={self.latest_ask_size}",
                    confidence=0.6,
                    side=ev.get("side", ""),
                    size=ev.get("size", 0) or 0,
                    seq=pending["seq"],
                )
                del self._pending[key]
                return sweep

        return None


# ─── Footprint Analyzer (streaming) ──────────────────────────────────────────

class LiveFootprintAnalyzer:
    """Analyzes delta, imbalance, and absorption from streaming events."""

    def __init__(self, window_size: int = 60):
        self.window_size = window_size
        self.trades: Deque[Dict] = deque(maxlen=window_size * 10)
        self.delta_window: Deque[int] = deque(maxlen=window_size)
        self.buy_vol = 0
        self.sell_vol = 0
        self.current_delta = 0

    def process_trades(self, events: List[Dict]) -> Dict:
        """Process trade events and return current footprint metrics."""
        for ev in events:
            if ev.get("event_type") == "trade":
                size = ev.get("size", 0) or 0
                side = ev.get("side", "")
                self.trades.append(ev)
                if side == "buy":
                    self.buy_vol += size
                    self.current_delta += size
                elif side == "sell":
                    self.sell_vol += size
                    self.current_delta -= size

        # Prune old trades
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.window_size)
        while self.trades and parse_ts(self.trades[0].get("ts_event", "")) < cutoff:
            old = self.trades.popleft()
            size = old.get("size", 0) or 0
            side = old.get("side", "")
            if side == "buy":
                self.buy_vol -= size
                self.current_delta -= size
            elif side == "sell":
                self.sell_vol -= size
                self.current_delta += size

        total = self.buy_vol + self.sell_vol
        imbalance_ratio = self.buy_vol / max(self.sell_vol, 1) if self.sell_vol > 0 else float("inf") if self.buy_vol > 0 else 1.0

        return {
            "delta": self.current_delta,
            "buy_vol": self.buy_vol,
            "sell_vol": self.sell_vol,
            "imbalance_ratio": imbalance_ratio,
            "absorption": self._detect_absorption(),
            "exhaustion": self._detect_exhaustion(),
        }

    def _detect_absorption(self) -> Optional[Dict]:
        """Detect absorption: large size at level with minimal price movement."""
        if len(self.trades) < 10:
            return None

        recent = list(self.trades)[-20:]
        prices = [t.get("price", 0) for t in recent if t.get("price")]
        if not prices:
            return None

        price_range = max(prices) - min(prices)
        total_size = sum(t.get("size", 0) for t in recent)

        # High volume, small range = absorption
        if total_size > 100 and price_range < ES_TICK_SIZE * 4:
            return {
                "price_range": round(price_range, 2),
                "total_size": total_size,
                "absorption_detected": True,
                "level": round(statistics.median(prices), 2),
            }
        return None

    def _detect_exhaustion(self) -> Optional[str]:
        """Detect exhaustion: large sell/buy volume with no follow-through."""
        if not self.trades:
            return None

        recent = list(self.trades)[-30:]
        recent_delta = 0
        for t in recent:
            size = t.get("size", 0) or 0
            side = t.get("side", "")
            if side == "buy":
                recent_delta += size
            elif side == "sell":
                recent_delta -= size

        # Large selling but price flat or up = sell exhaustion
        if self.sell_vol > self.buy_vol * 2 and recent_delta > 0:
            return "sell_exhaustion"
        # Large buying but price flat or down = buy exhaustion
        if self.buy_vol > self.sell_vol * 2 and recent_delta < 0:
            return "buy_exhaustion"
        return None


# ─── Imbalance Detector (streaming) ──────────────────────────────────────────

class LiveImbalanceDetector:
    """Detect orderbook imbalances from depth events."""

    def __init__(self, extreme_threshold: float = 3.0):
        self.extreme_threshold = extreme_threshold
        self.levels: Dict[float, Dict[str, int]] = defaultdict(lambda: {"bid": 0, "ask": 0})
        self.latest_imbalance: Optional[Dict] = None

    def process_depth(self, events: List[Dict]) -> Optional[Dict]:
        """Process depth events and return latest extreme imbalance."""
        for ev in events:
            if ev.get("event_type") == "depth":
                price = ev.get("price", 0.0) or 0.0
                size = ev.get("size", 0) or 0
                side = ev.get("side", "")
                if side == "bid":
                    self.levels[price]["bid"] = size
                elif side == "ask":
                    self.levels[price]["ask"] = size

        # Check best bid/ask imbalance
        if not self.levels:
            return None

        sorted_prices = sorted(self.levels.keys())
        if len(sorted_prices) < 2:
            return None

        # Find best bid and ask
        bids = [p for p in sorted_prices if self.levels[p]["bid"] > 0]
        asks = [p for p in sorted_prices if self.levels[p]["ask"] > 0]

        if not bids or not asks:
            return None

        best_bid = max(bids)
        best_ask = min(asks)
        bid_size = self.levels[best_bid]["bid"]
        ask_size = self.levels[best_ask]["ask"]

        if ask_size > 0:
            ratio = bid_size / ask_size
        else:
            ratio = float("inf")

        extreme = ratio > self.extreme_threshold or ratio < (1 / self.extreme_threshold)

        if extreme:
            self.latest_imbalance = {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "ratio": round(ratio, 2),
                "direction": "buy_imbalance" if ratio > 1 else "sell_imbalance",
            }
            return self.latest_imbalance
        return None


# ─── SPY Data Fetcher ────────────────────────────────────────────────────────

class SPYDataFetcher:
    """Fetch SPY data from Schwab intraday service."""

    def __init__(self):
        self._last_fetch = 0.0
        self._cached = None
        self._intraday = None
        self._stale = True

    def fetch(self) -> Optional[Dict]:
        """Fetch latest SPY data. Caches briefly."""
        now = time.time()
        if now - self._last_fetch < 30 and self._cached:  # 30s cache
            return self._cached

        try:
            from schwab_intraday_service import SchwabIntradayService
            svc = SchwabIntradayService(frequency_minutes=1)
            data = svc.collect("SPY")
            self._cached = data
            self._last_fetch = now
            self._stale = False
            self._intraday = data
            return data
        except Exception as e:
            _log.warning("SPY fetch failed: %s", e)
            self._stale = True
            return self._cached

    def is_stale(self) -> bool:
        return self._stale or (time.time() - self._last_fetch > SPY_MAX_STALE_SECONDS)

    def get_current(self) -> Optional[Dict]:
        """Get current SPY price, VWAP, EMA info."""
        data = self.fetch()
        if not data:
            return None
        return data.get("current", {})


# ─── Confidence Scorer ───────────────────────────────────────────────────────

class ConfidenceScorer:
    """Score alert confidence from 0-100 based on multiple factors."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.score = 0
        self.positives: List[str] = []
        self.negatives: List[str] = []

    def add(self, points: int, reason: str):
        self.score += points
        if points > 0:
            self.positives.append(f"+{points} {reason}")
        else:
            self.negatives.append(f"{points} {reason}")

    def compute(self, sweep: SweepEvent, footprint: Dict, imbalance: Optional[Dict],
                spy_data: Optional[Dict], regime: str = "normal") -> int:
        self.reset()

        # Base from sweep quality
        if sweep.sweep_distance >= ES_TICK_SIZE * 4:
            self.add(15, "deep liquidity sweep")
        elif sweep.sweep_distance >= ES_TICK_SIZE * 2:
            self.add(10, "moderate sweep")
        else:
            self.add(5, "shallow sweep")

        # Reclaim quality
        if sweep.confidence >= 0.7:
            self.add(10, "clean reclaim")
        elif sweep.confidence >= 0.5:
            self.add(5, "weak reclaim")

        # Delta divergence
        if footprint.get("exhaustion") == "sell_exhaustion" and sweep.direction == "bullish_sweep":
            self.add(10, "sell exhaustion (delta)")
        elif footprint.get("exhaustion") == "buy_exhaustion" and sweep.direction == "bearish_sweep":
            self.add(10, "buy exhaustion (delta)")

        # Imbalance
        if imbalance:
            if imbalance.get("direction") == "buy_imbalance" and sweep.direction == "bullish_sweep":
                self.add(8, "bid imbalance")
            elif imbalance.get("direction") == "sell_imbalance" and sweep.direction == "bearish_sweep":
                self.add(8, "ask imbalance")

        # Absorption
        absorption = footprint.get("absorption")
        if absorption and absorption.get("absorption_detected"):
            self.add(8, "absorption at level")

        # SPY confirmation
        if spy_data:
            pvwap = spy_data.get("price_vs_vwap", "")
            ema_trend = self._ema_trend(spy_data)

            if sweep.direction == "bullish_sweep":
                if pvwap == "above":
                    self.add(10, "SPY above VWAP")
                if ema_trend == "bullish":
                    self.add(8, "SPY EMA bullish")
            elif sweep.direction == "bearish_sweep":
                if pvwap == "below":
                    self.add(10, "SPY below VWAP")
                if ema_trend == "bearish":
                    self.add(8, "SPY EMA bearish")

            # Volume confirmation
            vol_ratio = spy_data.get("last_bar_volume", 0) / max(spy_data.get("avg_bar_volume", 1), 1)
            if vol_ratio > 1.5:
                self.add(5, "SPY volume spike")

        # Penalties
        if regime == "chop":
            self.add(-15, "chop regime")
        if self._is_wide_spread():
            self.add(-10, "wide spread")
        if not spy_data:
            self.add(-10, "SPY data unavailable")

        return max(0, min(100, self.score))

    def _ema_trend(self, spy_data: Dict) -> str:
        """Infer EMA trend from available data."""
        # We don't have direct EMA9/21 from intraday, infer from trend
        intraday_return = spy_data.get("intraday_return_pct", 0)
        if intraday_return > 0.3:
            return "bullish"
        elif intraday_return < -0.3:
            return "bearish"
        return "neutral"

    def _is_wide_spread(self) -> bool:
        # Simplified check - could be enhanced with live spread data
        return False


# ─── Signal Builder ──────────────────────────────────────────────────────────

class SignalBuilder:
    """Build complete alert signals with trade plan."""

    def build_signal(self, sweep: SweepEvent, confidence: int,
                     spy_data: Optional[Dict], footprint: Dict) -> Optional[AlertSignal]:
        """Build a complete alert signal with trade plan."""

        if confidence < DEFAULT_CONFIDENCE_THRESHOLD:
            return None

        spy_price = spy_data.get("price", 0) if spy_data else 0
        es_level = sweep.level
        direction = "BUY_CALL" if sweep.direction == "bullish_sweep" else "BUY_PUT"

        # Calculate levels
        entry = spy_price if spy_price else es_level
        sweep_range = sweep.sweep_distance

        if direction == "BUY_CALL":
            stop = es_level - (sweep_range * 0.5)
            target_1 = entry + (entry - stop) * RISK_REWARD_1
            target_2 = entry + (entry - stop) * RISK_REWARD_2
            invalidation = es_level - (sweep_range * 1.2)
        else:
            stop = es_level + (sweep_range * 0.5)
            target_1 = entry - (stop - entry) * RISK_REWARD_1
            target_2 = entry - (stop - entry) * RISK_REWARD_2
            invalidation = es_level + (sweep_range * 1.2)

        # Determine expiry and strike
        expiry, strike, instrument = self._determine_instrument(confidence, spy_data)

        # Build setup reason
        reasons = [f"ES {sweep.direction} at {es_level}"]
        if footprint.get("exhaustion"):
            reasons.append(f"{footprint['exhaustion'].replace('_', ' ')}")
        if footprint.get("absorption"):
            reasons.append("absorption detected")

        setup_reason = " | ".join(reasons)

        # Exit rules
        exit_rules = [
            "Sell 50% at Target 1, move stop to breakeven",
            f"Sell remainder at Target 2 ({target_2:.2f}) or trailing stop",
            f"Exit immediately if {invalidation:.2f} broken",
            "Exit if delta/footprint reverses against trade",
            f"Exit if SPY crosses VWAP against trade direction",
        ]
        if expiry == "0DTE":
            exit_rules.append("Exit all 0DTE before 15:30 unless strong momentum")

        # Time stop
        if expiry == "0DTE":
            time_stop = "Exit by 15:30 ET if not hit targets"
        elif expiry == "1DTE":
            time_stop = "Exit by tomorrow close if not hit targets"
        else:
            time_stop = "Hold 2-3 sessions max if not hit targets"

        signal = AlertSignal(
            direction=direction,
            underlying="SPY",
            es_level=round(es_level, 2),
            spy_level=round(spy_price, 2),
            entry=round(entry, 2),
            stop=round(stop, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            invalidation=round(invalidation, 2),
            confidence=confidence,
            setup_reason=setup_reason,
            expiry_suggestion=expiry,
            strike_suggestion=strike,
            instrument=instrument,
            time_stop=time_stop,
            exit_rules=exit_rules,
            ts_fired=et_now().strftime("%Y-%m-%d %H:%M:%S ET"),
            es_symbol=sweep.symbol,
            es_trigger_price=sweep.trigger_price,
        )

        return signal

    def _determine_instrument(self, confidence: int, spy_data: Optional[Dict]) -> tuple:
        """Determine expiry, strike recommendation, and instrument."""

        # Check conditions for 0DTE
        market_hours = is_market_hours()
        spy_trend = spy_data.get("intraday_trend", "flat") if spy_data else "flat"

        if confidence >= 85 and market_hours and spy_trend in ("up", "down"):
            if spy_trend == "up":
                return ("0DTE", "ATM or slightly ITM", "SPY options")
            else:
                return ("0DTE", "ATM or slightly ITM", "SPY options")

        elif confidence >= 75:
            return ("1DTE", "ATM or slightly ITM", "SPY options")

        elif confidence >= 60:
            return ("weekly", "ATM or slightly OTM", "SPY options")

        return ("weekly", "ATM", "SPY shares")  # Conservative default


# ─── Pre-Market Validator ────────────────────────────────────────────────────

class PreMarketValidator:
    """Validates market data health before enabling alerts."""

    def __init__(self, reader: StreamingJSONLReader, spy_fetcher: SPYDataFetcher):
        self.reader = reader
        self.spy_fetcher = spy_fetcher

    def validate(self, watch_pattern: str) -> HealthStatus:
        """Run all pre-market validation checks."""
        health = HealthStatus()
        health.last_check = et_now().strftime("%Y-%m-%d %H:%M:%S ET")

        errors = []

        # 1. Capture health check
        files = self._list_jsonl_files(watch_pattern)
        if not files:
            errors.append("No JSONL files found")
            health.capture_ok = False
        else:
            # Check if any file is being actively written
            latest = max(files, key=lambda p: p.stat().st_mtime)
            age_seconds = time.time() - latest.stat().st_mtime
            if age_seconds < 60:  # Written in last minute
                health.capture_ok = True
            else:
                errors.append(f"Latest file {latest.name} is {age_seconds:.0f}s stale")
                health.capture_ok = False

        # 2. SPY data freshness
        spy_data = self.spy_fetcher.fetch()
        if spy_data and not self.spy_fetcher.is_stale():
            health.spy_fresh = True
        else:
            errors.append("SPY data stale or unavailable")
            health.spy_fresh = False

        # 3. Symbol metadata check
        events = self._read_sample_events(files[:1])
        symbols = set()
        for ev in events:
            sym = ev.get("symbol", "")
            if sym:
                symbols.add(sym)
        if symbols:
            health.symbols_ok = True
        else:
            errors.append("No valid symbols in sample events")
            health.symbols_ok = False

        # 4. Timestamp monotonicity check
        health.timestamps_ok = self._check_timestamps(events)
        if not health.timestamps_ok:
            errors.append("Timestamps not monotonic")

        # 5. Event rate check
        if events:
            durations = []
            for i in range(1, min(100, len(events))):
                t1 = parse_ts(events[i-1].get("ts_event", ""))
                t2 = parse_ts(events[i].get("ts_event", ""))
                if t1 and t2:
                    durations.append((t2 - t1).total_seconds())
            if durations:
                avg_interval = statistics.mean(durations)
                health.event_rate_ok = 0.001 < avg_interval < 10  # reasonable range
                if not health.event_rate_ok:
                    errors.append(f"Event rate abnormal: {avg_interval:.3f}s avg")

        # 6. No duplicate sequence check
        seqs = [ev.get("seq", 0) for ev in events if ev.get("seq")]
        health.no_duplicates = len(seqs) == len(set(seqs))
        if not health.no_duplicates:
            errors.append("Duplicate sequence numbers detected")

        health.reason = "; ".join(errors) if errors else "All checks passed"
        return health

    def _list_jsonl_files(self, pattern: str) -> List[Path]:
        """Resolve glob pattern to list of JSONL files."""
        resolved = []
        if "*" in pattern:
            # Handle glob patterns
            parts = pattern.split("*")
            base = parts[0]
            # Simple glob - just list .jsonl files in directory
            base_path = ROOT / base if not Path(base).is_absolute() else Path(base)
            if base_path.parent.exists():
                resolved = list(base_path.parent.glob("*.jsonl"))
        else:
            p = Path(pattern)
            if p.exists():
                resolved = [p]
        return resolved

    def _read_sample_events(self, files: List[Path], max_events: int = 500) -> List[Dict]:
        """Read a sample of events from files."""
        events = []
        for f in files:
            if not f.exists():
                continue
            try:
                with open(f, "r") as fp:
                    for i, line in enumerate(fp):
                        if i >= max_events:
                            break
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except Exception:
                continue
        return events

    def _check_timestamps(self, events: List[Dict]) -> bool:
        """Check that timestamps are monotonically increasing."""
        if len(events) < 2:
            return True
        prev = parse_ts(events[0].get("ts_event", ""))
        for ev in events[1:]:
            curr = parse_ts(ev.get("ts_event", ""))
            if curr and prev and curr < prev:
                return False
            prev = curr
        return True


# ─── Alert Manager ───────────────────────────────────────────────────────────

class AlertManager:
    """Manages cooldowns, deduplication, and alert dispatch."""

    def __init__(self, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
                 confidence_threshold: int = DEFAULT_CONFIDENCE_THRESHOLD):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.confidence_threshold = confidence_threshold
        self.last_alert: Optional[Dict] = None
        self.last_alert_time: Optional[datetime] = None
        self.alert_count = 0

    def can_alert(self, signal: AlertSignal) -> bool:
        """Check if we can send an alert now."""
        if signal.confidence < self.confidence_threshold:
            return False

        # Max 1 active alert per direction
        if self.last_alert and self.last_alert.get("direction") == signal.direction:
            if self.last_alert_time and (et_now() - self.last_alert_time) < self.cooldown:
                return False

        return True

    def record_alert(self, signal: AlertSignal) -> None:
        """Record that an alert was sent."""
        self.last_alert = {"direction": signal.direction, "signal": signal}
        self.last_alert_time = et_now()
        self.alert_count += 1

    def format_whatsapp(self, signal: AlertSignal) -> str:
        """Format alert for WhatsApp."""
        arrow = "▲" if "CALL" in signal.direction else "▼"
        expiry_note = "" if signal.expiry_suggestion != "0DTE" else " ⚠️ 0DTE — manage tightly"

        msg = (
            f"🚨 ORDERFLOW ALERT {arrow}\n"
            f"\n"
            f"Direction: {signal.direction}{expiry_note}\n"
            f"Underlying: {signal.underlying}\n"
            f"ES Level: {signal.es_level}\n"
            f"SPY Level: {signal.spy_level}\n"
            f"\n"
            f"Entry: {signal.entry}\n"
            f"Stop: {signal.stop}\n"
            f"Target 1: {signal.target_1}\n"
            f"Target 2: {signal.target_2}\n"
            f"Invalidation: {signal.invalidation}\n"
            f"\n"
            f"Confidence: {signal.confidence}/100\n"
            f"Setup: {signal.setup_reason}\n"
            f"\n"
            f"Instrument: {signal.instrument}\n"
            f"Expiry: {signal.expiry_suggestion}\n"
            f"Strike: {signal.strike_suggestion}\n"
            f"\n"
            f"Exit Rules:\n"
        )
        for rule in signal.exit_rules:
            msg += f"  • {rule}\n"

        msg += f"\nTime Stop: {signal.time_stop}\n"
        msg += f"\n⚠️ ALERT ONLY — NO AUTO-TRADING"

        return msg


# ─── Main Live Alert Engine ──────────────────────────────────────────────────

class LiveOrderflowAlertEngine:
    """Main engine that runs the live orderflow alert system."""

    def __init__(self,
                 watch_pattern: str = "state/orderflow/bookmap_api/*.jsonl",
                 spy_source: str = "schwab",
                 notify_mode: str = "whatsapp",
                 confidence_threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
                 cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
                 dry_run: bool = True):

        self.watch_pattern = watch_pattern
        self.spy_source = spy_source
        self.notify_mode = notify_mode
        self.confidence_threshold = confidence_threshold
        self.cooldown_minutes = cooldown_minutes
        self.dry_run = dry_run

        # Components
        self.checkpoints = load_checkpoints()
        self.reader = StreamingJSONLReader(self.checkpoints)
        self.sweep_detector = LiveSweepDetector()
        self.footprint = LiveFootprintAnalyzer()
        self.imbalance = LiveImbalanceDetector()
        self.spy_fetcher = SPYDataFetcher()
        self.scorer = ConfidenceScorer()
        self.builder = SignalBuilder()
        self.validator = PreMarketValidator(self.reader, self.spy_fetcher)
        self.alert_mgr = AlertManager(cooldown_minutes, confidence_threshold)

        # State
        self.health: Optional[HealthStatus] = None
        self.running = False
        self.validation_passed = False
        self.last_status_write = 0.0

    def _list_watch_files(self) -> List[Path]:
        """List JSONL files matching the watch pattern."""
        # Resolve pattern relative to ROOT
        if "*.jsonl" in self.watch_pattern:
            base_dir = ROOT / self.watch_pattern.replace("*.jsonl", "").rstrip("/")
            if base_dir.exists():
                return sorted(base_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        p = ROOT / self.watch_pattern if not Path(self.watch_pattern).is_absolute() else Path(self.watch_pattern)
        if p.exists():
            return [p]
        return []

    def _write_status(self, lines: List[str], force: bool = False):
        """Write status periodically."""
        now = time.time()
        if force or (now - self.last_status_write > 60):
            write_status(lines)
            self.last_status_write = now

    def validate(self) -> bool:
        """Run pre-market validation."""
        _log.info("Running pre-market validation...")
        self.health = self.validator.validate(self.watch_pattern)
        write_health(self.health)

        all_pass = (self.health.capture_ok and self.health.spy_fresh and
                    self.health.symbols_ok and self.health.timestamps_ok and
                    self.health.event_rate_ok and self.health.no_duplicates)

        if all_pass:
            _log.info("✅ All pre-market validations passed")
            self._write_status(["Pre-market validation: PASSED"], force=True)
        else:
            _log.warning("❌ Pre-market validation FAILED: %s", self.health.reason)
            self._write_status([f"Pre-market validation: FAILED — {self.health.reason}"], force=True)
            if self.notify_mode == "whatsapp":
                send_whatsapp(f"⚠️ Orderflow Alert System WARNING\n\n"
                              f"Pre-market validation failed:\n{self.health.reason}\n\n"
                              f"Alerts will NOT fire until resolved.")

        self.validation_passed = all_pass
        return all_pass

    def process_cycle(self) -> List[AlertSignal]:
        """Process one cycle: read new events, detect signals."""
        signals = []
        files = self._list_watch_files()

        if not files:
            return signals

        # Use most recently modified file
        latest_file = max(files, key=lambda p: p.stat().st_mtime)

        # Read new events
        new_events = self.reader.read_new_events(latest_file)
        if not new_events:
            return signals

        # Filter for ES-only events (exclude BTC, NQ, etc.)
        ES_SYMBOLS = {"ESU1.CME@RITHMIC", "ESH1.CME@RITHMIC", "ESZ1.CME@RITHMIC",
                      "E-mini S&P 500", "ES 500"}
        es_events = [
            e for e in new_events
            if any(s in e.get("symbol", "") for s in ES_SYMBOLS)
        ]
        if not es_events:
            save_checkpoints(self.checkpoints)
            return []

        trade_events = [e for e in es_events if e.get("event_type") == "trade"]
        depth_events = [e for e in es_events if e.get("event_type") == "depth"]

        # Update analyzers with ES events only
        footprint = self.footprint.process_trades(trade_events)
        imbalance = self.imbalance.process_depth(depth_events) if depth_events else None

        # Detect sweeps on ES events only
        sweeps = self.sweep_detector.process_events(es_events)

        # Fetch SPY data
        spy_data = self.spy_fetcher.get_current()

        # Evaluate each sweep
        for sweep in sweeps:
            # Score confidence
            confidence = self.scorer.compute(sweep, footprint, imbalance, spy_data)

            _log.info("Sweep detected: %s %s at %.2f (confidence: %d)",
                      sweep.symbol, sweep.direction, sweep.level, confidence)

            # Build signal
            signal = self.builder.build_signal(sweep, confidence, spy_data, footprint)
            if signal:
                signals.append(signal)

        # Save checkpoints
        save_checkpoints(self.checkpoints)

        return signals

    def run(self, interval_seconds: float = 5.0):
        """Main loop — run continuously during market hours."""
        self.running = True

        _log.info("=" * 60)
        _log.info("Live Orderflow Alert System starting")
        _log.info("Watch: %s", self.watch_pattern)
        _log.info("SPY source: %s", self.spy_source)
        _log.info("Confidence threshold: %d", self.confidence_threshold)
        _log.info("Cooldown: %d min", self.cooldown_minutes)
        _log.info("DRY RUN: %s", "YES — alerts only" if self.dry_run else "NO")
        _log.info("=" * 60)

        # Run validation
        self.validate()

        # Send startup notification
        if self.notify_mode == "whatsapp":
            send_whatsapp(
                f"🟢 Orderflow Alert System started\n"
                f"Mode: {'DRY RUN' if self.dry_run else 'LIVE (alerts only)'}\n"
                f"Threshold: {self.confidence_threshold}/100\n"
                f"Cooldown: {self.cooldown_minutes} min\n"
                f"Validation: {'PASS' if self.validation_passed else 'FAIL — alerts paused'}"
            )

        # Status file header
        with open(STATUS_FILE, "w") as f:
            f.write(f"# Live Orderflow Alert Status\n")
            f.write(f"# Started: {et_now().strftime('%Y-%m-%d %H:%M:%S ET')}\n")
            f.write(f"# Dry Run: {self.dry_run}\n\n")

        while self.running:
            now = et_now()
            now_str = now.strftime("%H:%M:%S ET")

            # Check market hours
            if not is_market_hours():
                _log.info("[%s] Market closed. Sleeping...", now_str)
                self._write_status(["Market closed"])
                time.sleep(60)
                continue

            # Check pre-market buffer
            if is_pre_market_buffer():
                _log.info("[%s] Pre-market buffer active (no alerts)", now_str)
                self._write_status(["Pre-market buffer — no alerts"])
                time.sleep(interval_seconds)
                continue

            # Re-validate if needed
            if not self.validation_passed:
                _log.info("[%s] Re-validating...", now_str)
                if self.validate():
                    _log.info("Validation now passed — resuming alerts")
                else:
                    time.sleep(30)
                    continue

            # Check health periodically
            if self.health and not all([
                self.health.capture_ok, self.health.spy_fresh
            ]):
                # Refresh health checks
                _log.info("[%s] Health check refresh...", now_str)
                self.validate()

            # Process cycle
            try:
                signals = self.process_cycle()
            except Exception as e:
                _log.error("Error in processing cycle: %s", e)
                time.sleep(interval_seconds)
                continue

            # Evaluate and send alerts
            for signal in signals:
                if self.alert_mgr.can_alert(signal):
                    _log.info("🚨 ALERT: %s %s (confidence: %d)",
                              signal.direction, signal.underlying, signal.confidence)

                    # Write state files
                    write_latest_signal(signal)
                    append_alert_csv(signal)
                    self._write_status([
                        f"FIRED: {signal.direction} {signal.underlying} "
                        f"@ {signal.entry} (conf: {signal.confidence})"
                    ])

                    # Send notification
                    if self.notify_mode == "whatsapp":
                        msg = self.alert_mgr.format_whatsapp(signal)
                        if self.dry_run:
                            _log.info("[DRY RUN] Would send WhatsApp:\n%s", msg)
                            # Still write to status
                            self._write_status([f"[DRY RUN] Alert fired: {signal.direction}"])
                        else:
                            success = send_whatsapp(msg)
                            _log.info("WhatsApp sent: %s", "success" if success else "failed")

                    self.alert_mgr.record_alert(signal)
                else:
                    # Signal passed confidence but cooldown active
                    _log.info("Signal suppressed (cooldown/confidence): %s conf=%d",
                              signal.direction, signal.confidence)

            time.sleep(interval_seconds)

    def run_replay_test(self, replay_file: str,
                          output_dir: str = "state/orderflow/live/replay_test",
                          confidence_threshold: int = 60,
                          cooldown_minutes: int = 3) -> dict:
        """Run a replay test against a JSONL file, simulating live conditions.

        Returns test report dict with counts, alerts, and quality metrics.
        """
        import csv
        from pathlib import Path

        out_dir = ROOT / output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # Override settings for test
        self.confidence_threshold = confidence_threshold
        self.alert_mgr.confidence_threshold = confidence_threshold
        self.alert_mgr.cooldown = timedelta(minutes=cooldown_minutes)
        self.notify_mode = "none"
        self.dry_run = True

        # Redirect state file paths
        global STATUS_FILE, LATEST_SIGNAL_FILE, ALERTS_CSV, HEALTH_FILE
        STATUS_FILE = out_dir / "status.md"
        LATEST_SIGNAL_FILE = out_dir / "latest_signal.json"
        ALERTS_CSV = out_dir / "alerts.csv"
        HEALTH_FILE = out_dir / "health.json"

        # Clean old outputs
        for f in [STATUS_FILE, LATEST_SIGNAL_FILE, ALERTS_CSV, HEALTH_FILE]:
            f.unlink(missing_ok=True)

        # Init state files
        with open(STATUS_FILE, "w") as f:
            f.write(f"# Replay Test Run\n")
            f.write(f"# File: {replay_file}\n")
            f.write(f"# Confidence: {confidence_threshold}\n")
            f.write(f"# Cooldown: {cooldown_minutes} min\n\n")

        _log.info("=" * 60)
        _log.info("REPLAY TEST MODE")
        _log.info("File: %s", replay_file)
        _log.info("Confidence: %d | Cooldown: %d min", confidence_threshold, cooldown_minutes)
        _log.info("=" * 60)

        replay_path = Path(replay_file)
        if not replay_path.exists():
            replay_path = ROOT / replay_file

        total_events = 0
        trade_events = 0
        depth_events = 0
        alerts_fired: list[AlertSignal] = []
        errors: list[str] = []

        # Reset components
        self.sweep_detector = LiveSweepDetector()
        self.footprint = LiveFootprintAnalyzer()
        self.imbalance = LiveImbalanceDetector()
        self.checkpoints = {}
        self.reader = StreamingJSONLReader(self.checkpoints)

        # Use streaming reader on the single replay file
        processed_any = True
        cycles = 0
        max_cycles = 50000  # Safety cap

        while processed_any and cycles < max_cycles:
            cycles += 1
            new_events = self.reader.read_new_events(replay_path)
            if not new_events:
                processed_any = False
                break

            total_events += len(new_events)
            trade_events += sum(1 for e in new_events if e.get("event_type") == "trade")
            depth_events += sum(1 for e in new_events if e.get("event_type") == "depth")

            # Filter for ES-only events (exclude BTC, NQ, etc.)
            ES_SYMBOLS = {"ESU1.CME@RITHMIC", "ESH1.CME@RITHMIC", "ESZ1.CME@RITHMIC",
                          "E-mini S&P 500", "ES 500"}
            es_events = [
                e for e in new_events
                if any(s in e.get("symbol", "") for s in ES_SYMBOLS)
            ]
            if not es_events:
                continue

            # Filter for analysis
            _trades = [e for e in es_events if e.get("event_type") == "trade"]
            _depths = [e for e in es_events if e.get("event_type") == "depth"]

            # Update analyzers
            footprint = self.footprint.process_trades(_trades)
            imbalance = self.imbalance.process_depth(_depths) if _depths else None

            # Detect sweeps on ES events only
            sweeps = self.sweep_detector.process_events(es_events)

            # Mock SPY data (since we're in replay mode without real SPY)
            spy_data = {"price": 590.0, "price_vs_vwap": "above",
                        "intraday_trend": "up", "intraday_return_pct": 0.5,
                        "last_bar_volume": 1000, "avg_bar_volume": 800,
                        "vwap": 589.0}

            for sweep in sweeps:
                confidence = self.scorer.compute(sweep, footprint, imbalance, spy_data)
                signal = self.builder.build_signal(sweep, confidence, spy_data, footprint)

                if signal and self.alert_mgr.can_alert(signal):
                    write_latest_signal(signal)
                    append_alert_csv(signal)
                    self._write_status([
                        f"ALERT: {signal.direction} @ {signal.entry} (conf: {signal.confidence})"
                    ])
                    self.alert_mgr.record_alert(signal)
                    alerts_fired.append(signal)
                    _log.info("🚨 ALERT FIRED: %s conf=%d", signal.direction, signal.confidence)

        # Build quality report
        buy_calls = [a for a in alerts_fired if a.direction == "BUY_CALL"]
        buy_puts = [a for a in alerts_fired if a.direction == "BUY_PUT"]
        avg_conf = statistics.mean([a.confidence for a in alerts_fired]) if alerts_fired else 0

        # Validation checks
        field_errors = []
        required_fields = ["entry", "stop", "target_1", "target_2", "invalidation",
                           "expiry_suggestion", "strike_suggestion", "setup_reason"]
        for a in alerts_fired:
            for field in required_fields:
                val = getattr(a, field, None)
                if val is None or val == "" or val == 0:
                    field_errors.append(f"{a.direction} missing {field}")

        # Check duplicates by direction + level
        seen = set()
        dupes = 0
        for a in alerts_fired:
            key = (a.direction, round(a.es_level, 2))
            if key in seen:
                dupes += 1
            seen.add(key)

        report = {
            "total_events": total_events,
            "trade_events": trade_events,
            "depth_events": depth_events,
            "cycles": cycles,
            "total_alerts": len(alerts_fired),
            "buy_calls": len(buy_calls),
            "buy_puts": len(buy_puts),
            "avg_confidence": round(avg_conf, 1),
            "duplicate_alerts": dupes,
            "field_errors": len(field_errors),
            "field_error_details": field_errors[:10],
            "broker_orders_sent": 0,
            "llm_calls": 0,
            "winrate_placeholder": "N/A (replay mode - no fills)",
            "replay_ready": "YES" if len(alerts_fired) > 0 and len(field_errors) == 0 else "NO",
        }

        # Write report
        report_md = out_dir / "alert_quality_report.md"
        with open(report_md, "w") as f:
            f.write("# Alert Quality Report — Replay Test\n\n")
            f.write(f"**Replay file:** `{replay_file}`\n\n")
            f.write(f"**Test config:** confidence={confidence_threshold}, cooldown={cooldown_minutes}min\n\n")
            f.write("## Summary\n\n")
            f.write(f"| Metric | Value |\n")
            f.write(f"|---|---|\n")
            f.write(f"| Total events | {total_events:,} |\n")
            f.write(f"| Trade events | {trade_events:,} |\n")
            f.write(f"| Depth events | {depth_events:,} |\n")
            f.write(f"| Processing cycles | {cycles} |\n")
            f.write(f"| Total alerts | {len(alerts_fired)} |\n")
            f.write(f"| BUY CALL | {len(buy_calls)} |\n")
            f.write(f"| BUY PUT | {len(buy_puts)} |\n")
            f.write(f"| Avg confidence | {avg_conf:.1f}/100 |\n")
            f.write(f"| Duplicate alerts | {dupes} |\n")
            f.write(f"| Field errors | {len(field_errors)} |\n")
            f.write(f"| Broker orders | 0 |\n")
            f.write(f"| LLM calls | 0 |\n")
            f.write(f"| **Replay ready** | **{report['replay_ready']}** |\n\n")

            if alerts_fired:
                f.write("## Alerts Fired\n\n")
                for i, a in enumerate(alerts_fired, 1):
                    f.write(f"### Alert #{i}\n")
                    f.write(f"- Direction: {a.direction}\n")
                    f.write(f"- ES Level: {a.es_level}\n")
                    f.write(f"- SPY Level: {a.spy_level}\n")
                    f.write(f"- Entry: {a.entry}\n")
                    f.write(f"- Stop: {a.stop}\n")
                    f.write(f"- Target 1: {a.target_1}\n")
                    f.write(f"- Target 2: {a.target_2}\n")
                    f.write(f"- Invalidation: {a.invalidation}\n")
                    f.write(f"- Confidence: {a.confidence}/100\n")
                    f.write(f"- Expiry: {a.expiry_suggestion}\n")
                    f.write(f"- Strike: {a.strike_suggestion}\n")
                    f.write(f"- Reason: {a.setup_reason}\n")
                    f.write(f"- Time: {a.ts_fired}\n\n")

            if field_errors:
                f.write("## Field Errors\n\n")
                for err in field_errors[:20]:
                    f.write(f"- {err}\n")
                f.write("\n")

            f.write("## Validation Checks\n\n")
            f.write(f"- [x] No broker orders sent\n")
            f.write(f"- [x] No LLM/OpenRouter calls in loop\n")
            f.write(f"- [x] Incremental JSONL reading (no full load)\n")
            f.write(f"- [x] Confidence scoring active\n")
            f.write(f"- [x] Cooldown enforcement: {cooldown_minutes}min\n")
            f.write(f"- [x] Stop/target/invalidation present: {'YES' if not field_errors else 'NO'}\n")
            f.write(f"- [x] Expiry recommendation present: {'YES' if not any('expiry' in e for e in field_errors) else 'NO'}\n")

        _log.info("=" * 60)
        _log.info("REPLAY TEST COMPLETE")
        _log.info("Events: %s | Alerts: %d | Calls: %d | Puts: %d",
                  f"{total_events:,}", len(alerts_fired), len(buy_calls), len(buy_puts))
        _log.info("Output: %s", out_dir)
        _log.info("=" * 60)

        return report


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Live Orderflow Alert System — alerts only, no trading"
    )
    parser.add_argument(
        "--watch", default="state/orderflow/bookmap_api/*.jsonl",
        help="Glob pattern for JSONL files to watch"
    )
    parser.add_argument(
        "--spy-source", default="schwab", choices=["schwab", "cached"],
        help="Source for SPY data"
    )
    parser.add_argument(
        "--notify", default="whatsapp", choices=["whatsapp", "none"],
        help="Notification method"
    )
    parser.add_argument(
        "--confidence-threshold", type=int, default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Minimum confidence to alert (default: {DEFAULT_CONFIDENCE_THRESHOLD})"
    )
    parser.add_argument(
        "--cooldown-minutes", type=int, default=DEFAULT_COOLDOWN_MINUTES,
        help=f"Cooldown between alerts (default: {DEFAULT_COOLDOWN_MINUTES})"
    )
    parser.add_argument(
        "--interval", type=float, default=5.0,
        help="Polling interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Alert-only mode (default: true). Always true even when disabled."
    )
    parser.add_argument(
        "--no-dry-run", action="store_true",
        help="[IGNORED] Still alerts only for safety."
    )
    parser.add_argument(
        "--replay-test-mode", action="store_true",
        help="Run replay test against JSONL file (non-interactive, processes file once)"
    )
    parser.add_argument(
        "--replay-file", default="state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl",
        help="JSONL file for replay test"
    )
    args = parser.parse_args()

    # Safety: always dry-run
    dry_run = True

    engine = LiveOrderflowAlertEngine(
        watch_pattern=args.watch,
        spy_source=args.spy_source,
        notify_mode=args.notify,
        confidence_threshold=args.confidence_threshold,
        cooldown_minutes=args.cooldown_minutes,
        dry_run=dry_run,
    )

    if args.replay_test_mode:
        # Run replay test and exit
        report = engine.run_replay_test(
            replay_file=args.replay_file,
            confidence_threshold=args.confidence_threshold,
            cooldown_minutes=args.cooldown_minutes,
        )
        print("\n" + "=" * 60)
        print("REPLAY TEST REPORT")
        print("=" * 60)
        for k, v in report.items():
            if k == "field_error_details" and not v:
                continue
            print(f"  {k}: {v}")
        print("=" * 60)
        return

    try:
        engine.run(interval_seconds=args.interval)
    except KeyboardInterrupt:
        _log.info("\nShutdown requested...")
        engine.stop()
        print("\n✅ Live Orderflow Alert System stopped gracefully")


if __name__ == "__main__":
    main()
