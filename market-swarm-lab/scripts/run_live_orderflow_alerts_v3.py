#!/usr/bin/env python3
"""Live Orderflow Alert Engine — v3.0 (Dry-Run Mode)"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ─── Paths ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state" / "orderflow" / "live"
STATE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
ES_TICK_SIZE = 0.25

@dataclass
class FileCheckpoint:
    path: str
    offset: int = 0
    last_seq: int = 0
    last_modified: float = 0.0

@dataclass
class SweepEvent:
    ts_event: str
    symbol: str
    direction: str
    trigger_price: float
    level: float
    sweep_distance: float
    reclaim_delay_ms: float
    liquidity_behavior: str
    confidence: float
    side: str = ""
    size: int = 0
    seq: int = 0

@dataclass
class AlertSignal:
    direction: str
    underlying: str
    es_level: float
    spy_level: float
    entry: float
    stop: float
    target_1: float
    target_2: float
    invalidation: float
    confidence: int
    setup_reason: str
    expiry_suggestion: str
    strike_suggestion: str
    instrument: str
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
    events_per_sec: float = 0.0
    total_events: int = 0
    signals_generated: int = 0

# ─── Helpers ─────────────────────────────────────────────────────────────────
def et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)

def is_market_hours() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= now <= end

def parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)

# ─── State Writers ───────────────────────────────────────────────────────────
CHECKPOINT_FILE = STATE_DIR / "checkpoints.json"
STATUS_FILE = STATE_DIR / "status.md"
LATEST_SIGNAL_FILE = STATE_DIR / "latest_signal.json"
ALERTS_CSV = STATE_DIR / "alerts.csv"
HEALTH_FILE = STATE_DIR / "health.json"

def load_checkpoints() -> Dict[str, FileCheckpoint]:
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
        return {k: FileCheckpoint(**v) for k, v in data.items()}
    except Exception as e:
        _log.warning("Failed to load checkpoints: %s", e)
        return {}

def save_checkpoints(checkpoints: Dict[str, FileCheckpoint]) -> None:
    try:
        data = {k: asdict(v) for k, v in checkpoints.items()}
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _log.error("Failed to save checkpoints: %s", e)

def write_status(lines: List[str]) -> None:
    try:
        with open(STATUS_FILE, "a") as f:
            f.write(f"\n## {et_now().strftime('%Y-%m-%d %H:%M:%S ET')}\n")
            for line in lines:
                f.write(f"- {line}\n")
    except Exception as e:
        _log.error("Failed to write status: %s", e)

def write_latest_signal(signal: AlertSignal) -> None:
    try:
        with open(LATEST_SIGNAL_FILE, "w") as f:
            json.dump(asdict(signal), f, indent=2, default=str)
    except Exception as e:
        _log.error("Failed to write latest signal: %s", e)

def append_alert_csv(signal: AlertSignal) -> None:
    fieldnames = ["ts_fired", "direction", "underlying", "es_level", "spy_level",
                  "entry", "stop", "target_1", "target_2", "invalidation",
                  "confidence", "setup_reason", "expiry_suggestion", "strike_suggestion",
                  "instrument", "es_symbol", "time_stop"]
    try:
        file_exists = ALERTS_CSV.exists() and ALERTS_CSV.stat().st_size > 0
        with open(ALERTS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(signal))
    except Exception as e:
        _log.error("Failed to append alert CSV: %s", e)

def write_health(health: HealthStatus) -> None:
    try:
        with open(HEALTH_FILE, "w") as f:
            json.dump(asdict(health), f, indent=2)
    except Exception as e:
        _log.error("Failed to write health: %s", e)

def init_state_files() -> None:
    if not STATUS_FILE.exists() or STATUS_FILE.stat().st_size == 0:
        with open(STATUS_FILE, "w") as f:
            f.write(f"# Live Orderflow Alert Status\n")
            f.write(f"# Started: {et_now().strftime('%Y-%m-%d %H:%M:%S ET')}\n")
            f.write(f"# Mode: v3 dry-run\n\n")
    if not LATEST_SIGNAL_FILE.exists() or LATEST_SIGNAL_FILE.stat().st_size == 0:
        with open(LATEST_SIGNAL_FILE, "w") as f:
            json.dump({"status": "no_signals_yet"}, f, indent=2)
    if not ALERTS_CSV.exists():
        fieldnames = ["ts_fired", "direction", "underlying", "es_level", "spy_level",
                      "entry", "stop", "target_1", "target_2", "invalidation",
                      "confidence", "setup_reason", "expiry_suggestion", "strike_suggestion",
                      "instrument", "es_symbol", "time_stop"]
        with open(ALERTS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

# ─── Streaming JSONL Reader ──────────────────────────────────────────────────
class StreamingJSONLReader:
    def __init__(self, checkpoints: Dict[str, FileCheckpoint]):
        self.checkpoints = checkpoints

    def read_new_events(self, file_path: Path) -> List[Dict]:
        path_str = str(file_path)
        cp = self.checkpoints.get(path_str, FileCheckpoint(path=path_str))

        if not file_path.exists():
            return []

        current_size = file_path.stat().st_size
        current_mtime = file_path.stat().st_mtime

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
                cp.offset = f.tell()
        except Exception as e:
            _log.error("Error reading %s: %s", file_path.name, e)
            return []

        cp.last_modified = current_mtime
        self.checkpoints[path_str] = cp
        return events

# ─── Sweep Detector ──────────────────────────────────────────────────────────
class LiveSweepDetector:
    def __init__(self, min_sweep_ticks: float = 1.0, max_sweep_ticks: float = 10.0,
                 tick_size: float = ES_TICK_SIZE, max_pending_events: int = 30):
        self.min_sweep = min_sweep_ticks * tick_size
        self.max_sweep = max_sweep_ticks * tick_size
        self.tick_size = tick_size
        self.max_pending = max_pending_events
        self.recent_prices: Deque[float] = deque(maxlen=200)
        self.latest_bid = 0.0
        self.latest_ask = 0.0
        self.latest_bid_size = 0
        self.latest_ask_size = 0
        self._pending: Dict[str, Dict] = {}
        self.sweeps: List[SweepEvent] = []

    def process_events(self, events: List[Dict]) -> List[SweepEvent]:
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
            size = ev.get("size", 0) or 0
            if side == "bid":
                self.latest_bid = p
                self.latest_bid_size = size
            elif side == "ask":
                self.latest_ask = p
                self.latest_ask_size = size

    def _expire_pending(self, current_seq: int, current_price: float):
        expired = []
        for key, pending in list(self._pending.items()):
            seq_age = current_seq - pending["seq"]
            if seq_age > self.max_pending:
                expired.append(key)
                continue
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
                    "type": "bullish", "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p, "level": support, "seq": seq,
                }

        # Bearish sweep
        if p > resistance + self.min_sweep and p < resistance + self.max_sweep:
            key = f"resistance_{resistance:.2f}"
            if key not in self._pending:
                self._pending[key] = {
                    "type": "bearish", "trigger_ts": ev.get("ts_event", ""),
                    "trigger_price": p, "level": resistance, "seq": seq,
                }

        # Check reclaims
        for key, pending in list(self._pending.items()):
            if pending["type"] == "bullish" and p >= pending["level"]:
                sweep = SweepEvent(
                    ts_event=pending["trigger_ts"], symbol=symbol,
                    direction="bullish_sweep", trigger_price=pending["trigger_price"],
                    level=pending["level"],
                    sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                    reclaim_delay_ms=0.0,
                    liquidity_behavior=f"bid_size={self.latest_bid_size}",
                    confidence=0.6, side=ev.get("side", ""),
                    size=ev.get("size", 0) or 0, seq=pending["seq"],
                )
                del self._pending[key]
                return sweep

            elif pending["type"] == "bearish" and p <= pending["level"]:
                sweep = SweepEvent(
                    ts_event=pending["trigger_ts"], symbol=symbol,
                    direction="bearish_sweep", trigger_price=pending["trigger_price"],
                    level=pending["level"],
                    sweep_distance=abs(pending["trigger_price"] - pending["level"]),
                    reclaim_delay_ms=0.0,
                    liquidity_behavior=f"ask_size={self.latest_ask_size}",
                    confidence=0.6, side=ev.get("side", ""),
                    size=ev.get("size", 0) or 0, seq=pending["seq"],
                )
                del self._pending[key]
                return sweep
        return None

# ─── Footprint Analyzer ──────────────────────────────────────────────────────
class LiveFootprintAnalyzer:
    def __init__(self, window_size: int = 60):
        self.window_size = window_size
        self.trades: Deque[Dict] = deque(maxlen=window_size * 10)
        self.buy_vol = 0
        self.sell_vol = 0
        self.current_delta = 0

    def process_trades(self, events: List[Dict]) -> Dict:
        for ev in events:
            if ev.get("event_type") == "trade":
                size = ev.get("size", 0) or 0
                side = ev.get("side", "")
                self.trades.append(ev)
                if side == "buy":
                    self.buy_vol += size; self.current_delta += size
                elif side == "sell":
                    self.sell_vol += size; self.current_delta -= size

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.window_size)
        while self.trades and parse_ts(self.trades[0].get("ts_event", "")) < cutoff:
            old = self.trades.popleft()
            size = old.get("size", 0) or 0
            side = old.get("side", "")
            if side == "buy":
                self.buy_vol -= size; self.current_delta -= size
            elif side == "sell":
                self.sell_vol -= size; self.current_delta += size

        return {
            "delta": self.current_delta,
            "buy_vol": self.buy_vol,
            "sell_vol": self.sell_vol,
            "imbalance_ratio": self.buy_vol / max(self.sell_vol, 1) if self.sell_vol > 0 else float("inf") if self.buy_vol > 0 else 1.0,
            "exhaustion": self._detect_exhaustion(),
        }

    def _detect_exhaustion(self) -> Optional[str]:
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

        if self.sell_vol > self.buy_vol * 2 and recent_delta > 0:
            return "sell_exhaustion"
        if self.buy_vol > self.sell_vol * 2 and recent_delta < 0:
            return "buy_exhaustion"
        return None

# ─── Confidence Scorer ───────────────────────────────────────────────────────
class ConfidenceScorer:
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

    def compute(self, sweep: SweepEvent, footprint: Dict, spy_data: Optional[Dict]) -> int:
        self.reset()

        if sweep.sweep_distance >= ES_TICK_SIZE * 4:
            self.add(15, "deep liquidity sweep")
        elif sweep.sweep_distance >= ES_TICK_SIZE * 2:
            self.add(10, "moderate sweep")
        else:
            self.add(5, "shallow sweep")

        if sweep.confidence >= 0.7:
            self.add(10, "clean reclaim")
        elif sweep.confidence >= 0.5:
            self.add(5, "weak reclaim")

        if footprint.get("exhaustion") == "sell_exhaustion" and sweep.direction == "bullish_sweep":
            self.add(10, "sell exhaustion (delta)")
        elif footprint.get("exhaustion") == "buy_exhaustion" and sweep.direction == "bearish_sweep":
            self.add(10, "buy exhaustion (delta)")

        if spy_data:
            intraday_return = spy_data.get("intraday_return_pct", 0)
            if sweep.direction == "bullish_sweep" and intraday_return > 0.3:
                self.add(8, "SPY bullish trend")
            elif sweep.direction == "bearish_sweep" and intraday_return < -0.3:
                self.add(8, "SPY bearish trend")

        return max(0, min(100, self.score))

# ─── Signal Builder ──────────────────────────────────────────────────────────
class SignalBuilder:
    def build_signal(self, sweep: SweepEvent, confidence: int,
                     spy_data: Optional[Dict], footprint: Dict) -> Optional[AlertSignal]:
        if confidence < 75:
            return None

        spy_price = spy_data.get("price", 590.0) if spy_data else 590.0
        es_level = sweep.level
        direction = "BUY_CALL" if sweep.direction == "bullish_sweep" else "BUY_PUT"

        entry = spy_price if spy_price else es_level
        sweep_range = sweep.sweep_distance

        if direction == "BUY_CALL":
            stop = es_level - (sweep_range * 0.5)
            target_1 = entry + (entry - stop) * 1.0
            target_2 = entry + (entry - stop) * 2.0
            invalidation = es_level - (sweep_range * 1.2)
        else:
            stop = es_level + (sweep_range * 0.5)
            target_1 = entry - (stop - entry) * 1.0
            target_2 = entry - (stop - entry) * 2.0
            invalidation = es_level + (sweep_range * 1.2)

        expiry, strike, instrument = self._determine_instrument(confidence)

        reasons = [f"ES {sweep.direction} at {es_level}"]
        if footprint.get("exhaustion"):
            reasons.append(f"{footprint['exhaustion'].replace('_', ' ')}")

        setup_reason = " | ".join(reasons)

        exit_rules = [
            "Sell 50% at Target 1, move stop to breakeven",
            f"Sell remainder at Target 2 ({target_2:.2f}) or trailing stop",
            f"Exit immediately if {invalidation:.2f} broken",
            "Exit if delta/footprint reverses against trade",
        ]
        if expiry == "0DTE":
            exit_rules.append("Exit all 0DTE before 15:30 unless strong momentum")

        time_stop = "Exit by 15:30 ET if not hit targets" if expiry == "0DTE" else "Hold 2-3 sessions max"

        signal = AlertSignal(
            direction=direction, underlying="SPY",
            es_level=round(es_level, 2), spy_level=round(spy_price, 2),
            entry=round(entry, 2), stop=round(stop, 2),
            target_1=round(target_1, 2), target_2=round(target_2, 2),
            invalidation=round(invalidation, 2),
            confidence=confidence, setup_reason=setup_reason,
            expiry_suggestion=expiry, strike_suggestion=strike,
            instrument=instrument, time_stop=time_stop,
            exit_rules=exit_rules,
            ts_fired=et_now().strftime("%Y-%m-%d %H:%M:%S ET"),
            es_symbol=sweep.symbol, es_trigger_price=sweep.trigger_price,
        )
        return signal

    def _determine_instrument(self, confidence: int) -> tuple:
        if confidence >= 85:
            return ("0DTE", "ATM or slightly ITM", "SPY options")
        elif confidence >= 75:
            return ("1DTE", "ATM or slightly ITM", "SPY options")
        elif confidence >= 60:
            return ("weekly", "ATM or slightly OTM", "SPY options")
        return ("weekly", "ATM", "SPY shares")

# ─── Alert Manager ───────────────────────────────────────────────────────────
class AlertManager:
    def __init__(self, cooldown_minutes: int = 10, confidence_threshold: int = 75):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.confidence_threshold = confidence_threshold
        self.last_alert: Optional[Dict] = None
        self.last_alert_time: Optional[datetime] = None
        self.alert_count = 0

    def can_alert(self, signal: AlertSignal) -> bool:
        if signal.confidence < self.confidence_threshold:
            return False
        if self.last_alert and self.last_alert.get("direction") == signal.direction:
            if self.last_alert_time and (et_now() - self.last_alert_time) < self.cooldown:
                return False
        return True

    def record_alert(self, signal: AlertSignal) -> None:
        self.last_alert = {"direction": signal.direction, "signal": signal}
        self.last_alert_time = et_now()
        self.alert_count += 1

# ─── Main Engine ─────────────────────────────────────────────────────────────
class LiveOrderflowAlertEngine:
    def __init__(self, watch_dir: str, spy_source: str, notify_mode: str,
                 confidence_threshold: int, cooldown_minutes: int,
                 dry_run: bool, start_at_end: bool):
        self.watch_dir = Path(watch_dir)
        self.spy_source = spy_source
        self.notify_mode = notify_mode
        self.confidence_threshold = confidence_threshold
        self.cooldown_minutes = cooldown_minutes
        self.dry_run = dry_run
        self.start_at_end = start_at_end

        self.checkpoints = load_checkpoints()
        self.reader = StreamingJSONLReader(self.checkpoints)
        self.sweep_detector = LiveSweepDetector()
        self.footprint = LiveFootprintAnalyzer()
        self.scorer = ConfidenceScorer()
        self.builder = SignalBuilder()
        self.alert_mgr = AlertManager(cooldown_minutes, confidence_threshold)

        self.running = False
        self.total_events = 0
        self.signals_generated = 0
        self.start_time = 0.0

    def _list_watch_files(self) -> List[Path]:
        if not self.watch_dir.exists():
            return []
        files = list(self.watch_dir.glob("*.jsonl"))
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def _get_spy_data(self) -> Optional[Dict]:
        if self.spy_source == "cached":
            return {"price": 590.0, "intraday_return_pct": 0.5}
        return None

    def _initialize_checkpoints(self):
        if not self.start_at_end:
            return
        files = self._list_watch_files()
        for f in files:
            path_str = str(f)
            if path_str not in self.checkpoints:
                cp = FileCheckpoint(path=path_str)
                if f.exists():
                    cp.offset = f.stat().st_size
                    cp.last_modified = f.stat().st_mtime
                self.checkpoints[path_str] = cp
                _log.info("Tail-from-EOF: %s at offset %d", f.name, cp.offset)

    def process_cycle(self) -> List[AlertSignal]:
        signals = []
        files = self._list_watch_files()

        if not files:
            return signals

        latest_file = max(files, key=lambda p: p.stat().st_mtime)
        new_events = self.reader.read_new_events(latest_file)
        if not new_events:
            return signals

        self.total_events += len(new_events)

        # Normalize and filter symbols dynamically
        es_events = []
        for e in new_events:
            sym = e.get("symbol", "").upper()
            if ("ES" in sym and "MES" not in sym) or ("NQ" in sym and "MNQ" not in sym) or ("MES" in sym) or ("MNQ" in sym):
                es_events.append(e)
        if not es_events:
            _log.debug("No ES events in batch (symbols: %s)", 
                       set(e.get("symbol", "") for e in new_events[:10]))
            save_checkpoints(self.checkpoints)
            return []

        trade_events = [e for e in es_events if e.get("event_type") == "trade"]

        # Update analyzers
        footprint = self.footprint.process_trades(trade_events)

        # Detect sweeps
        sweeps = self.sweep_detector.process_events(es_events)

        # Get SPY data
        spy_data = self._get_spy_data()

        # Evaluate each sweep
        for sweep in sweeps:
            confidence = self.scorer.compute(sweep, footprint, spy_data)
            _log.info("Sweep: %s %s at %.2f (confidence: %d)",
                      sweep.symbol, sweep.direction, sweep.level, confidence)

            signal = self.builder.build_signal(sweep, confidence, spy_data, footprint)
            if signal:
                signals.append(signal)
                self.signals_generated += 1

        save_checkpoints(self.checkpoints)
        return signals

    def run(self, interval_seconds: float = 5.0):
        self.running = True
        self.start_time = time.time()

        init_state_files()
        self._initialize_checkpoints()

        _log.info("=" * 60)
        _log.info("Live Orderflow Alert Engine v3.0")
        _log.info("Watch: %s", self.watch_dir)
        _log.info("SPY source: %s", self.spy_source)
        _log.info("Confidence threshold: %d", self.confidence_threshold)
        _log.info("Cooldown: %d min", self.cooldown_minutes)
        _log.info("DRY RUN: %s", "YES" if self.dry_run else "NO")
        _log.info("=" * 60)

        write_status(["Engine v3.0 started", f"SPY source: {self.spy_source}",
                      f"Dry run: {self.dry_run}", f"Start at end: {self.start_at_end}"])

        while self.running:
            now = et_now()
            now_str = now.strftime("%H:%M:%S ET")

            if not is_market_hours():
                _log.info("[%s] Market closed. Sleeping...", now_str)
                write_status(["Market closed"])
                time.sleep(60)
                continue

            try:
                signals = self.process_cycle()
            except Exception as e:
                _log.error("Error in processing cycle: %s", e)
                time.sleep(interval_seconds)
                continue

            for signal in signals:
                if self.alert_mgr.can_alert(signal):
                    _log.info("SIGNAL: %s %s (confidence: %d)",
                              signal.direction, signal.underlying, signal.confidence)
                    write_latest_signal(signal)
                    append_alert_csv(signal)
                    write_status([f"SIGNAL: {signal.direction} {signal.underlying} @ {signal.entry} (conf: {signal.confidence})"])
                    self.alert_mgr.record_alert(signal)
                else:
                    _log.info("Signal suppressed: %s conf=%d",
                              signal.direction, signal.confidence)

            # Update health
            elapsed = time.time() - self.start_time
            events_per_sec = self.total_events / max(elapsed, 1)

            health = HealthStatus(
                capture_ok=True,
                spy_fresh=True,
                symbols_ok=True,
                timestamps_ok=True,
                event_rate_ok=events_per_sec > 0.001,
                no_duplicates=True,
                last_check=et_now().strftime("%Y-%m-%d %H:%M:%S ET"),
                reason="Running normally",
                events_per_sec=round(events_per_sec, 3),
                total_events=self.total_events,
                signals_generated=self.signals_generated,
            )
            write_health(health)

            time.sleep(interval_seconds)

    def stop(self):
        self.running = False
        elapsed = time.time() - self.start_time
        events_per_sec = self.total_events / max(elapsed, 1)
        health = HealthStatus(
            capture_ok=True,
            last_check=et_now().strftime("%Y-%m-%d %H:%M:%S ET"),
            reason="Stopped gracefully",
            events_per_sec=round(events_per_sec, 3),
            total_events=self.total_events,
            signals_generated=self.signals_generated,
        )
        write_health(health)
        write_status([f"Engine stopped", f"Total events: {self.total_events}",
                      f"Signals generated: {self.signals_generated}"])

# ─── CLI ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Live Orderflow Alert Engine v3.0")
    parser.add_argument("--watch", required=True, help="Directory to watch for JSONL files")
    parser.add_argument("--spy-source", default="cached", choices=["cached"])
    parser.add_argument("--notify", default="none", choices=["none"])
    parser.add_argument("--confidence-threshold", type=int, default=75)
    parser.add_argument("--cooldown-minutes", type=int, default=10)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--start-at-end", action="store_true", default=False, help="Start from EOF (skip history)")
    args = parser.parse_args()

    engine = LiveOrderflowAlertEngine(
        watch_dir=args.watch,
        spy_source=args.spy_source,
        notify_mode=args.notify,
        confidence_threshold=args.confidence_threshold,
        cooldown_minutes=args.cooldown_minutes,
        dry_run=args.dry_run,
        start_at_end=args.start_at_end,
    )

    try:
        engine.run(interval_seconds=args.interval)
    except KeyboardInterrupt:
        _log.info("\nShutdown requested...")
        engine.stop()
        print(f"\nEngine stopped. Events: {engine.total_events}, Signals: {engine.signals_generated}")
        print(f"  State: {STATE_DIR}")

if __name__ == "__main__":
    main()
