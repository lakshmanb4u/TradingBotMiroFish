#!/usr/bin/env python3
"""
Live Orderflow Alert Engine — v5.0 (Skeleton)

Blueprint showing how footprint engine will be PRIMARY, sweep detector SECONDARY.
This is a skeleton ONLY — do NOT run in production yet.

Architecture:
  1. Footprint entry signals (primary) — marked levels + divergence + absorption + reclaim
  2. Sweep detector (secondary/confirm) — liquidity sweeps add bonus confidence
  3. Both feed into unified ConfidenceScorer + SignalBuilder
  4. Dry-run flag prevents any real alerts
  5. WhatsApp placeholder only
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ─── Paths ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state" / "orderflow" / "live"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Ensure services/ on path
sys.path.insert(0, str(ROOT / "services" / "orderflow"))

# Import both engines
from sweep_detector import SweepDetector, SweepEvent
from tick_footprint_builder import TickFootprintBuilder, TickFootprintCandle, FootprintLadder
from marked_levels import MarkedLevelsDetector, MarkedLevel
from absorption_detector import AbsorptionDetector, AbsorptionEvent
from footprint_entry_signal import FootprintEntrySignalGenerator, FootprintEntrySignal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
ES_TICK_SIZE = 0.25

# v5: Footprint-first thresholds
FOOTPRINT_MIN_CONFIDENCE = 60.0       # Primary signal threshold
SWEEP_BONUS_CONFIDENCE = 10           # Extra points if sweep confirms footprint
COOLDOWN_MINUTES = 10
CONFIDENCE_THRESHOLD = 75             # Final alert threshold

# ─── Placeholder WhatsApp sender ─────────────────────────────────────────────
def send_whatsapp_placeholder(message: str) -> None:
    """Placeholder for WhatsApp integration. Logs only, never sends."""
    _log.info("[WHATSAPP_PLACEHOLDER] %s", message)

# ─── Dataclasses (reused from v4) ────────────────────────────────────────────
@dataclass
class FileCheckpoint:
    path: str
    offset: int = 0
    last_seq: int = 0
    last_modified: float = 0.0

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
    # v5: track which engine(s) contributed
    footprint_signal: Optional[Dict] = None
    sweep_signal: Optional[Dict] = None

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

# ─── Streaming JSONL Reader (unchanged from v4) ─────────────────────────────
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

# ─── State Persistence (unchanged from v4) ───────────────────────────────────
CHECKPOINT_FILE = STATE_DIR / "checkpoints.json"

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

# ─── Alert Manager (unchanged from v4) ───────────────────────────────────────
class AlertManager:
    def __init__(self, cooldown_minutes: int = COOLDOWN_MINUTES, confidence_threshold: int = CONFIDENCE_THRESHOLD):
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

# ─── v5 Signal Builder ───────────────────────────────────────────────────────
class UnifiedSignalBuilder:
    """
    Build alert signals from footprint (primary) and optional sweep (secondary).
    """
    def build_signal(
        self,
        footprint_sig: Optional[FootprintEntrySignal],
        sweep_sig: Optional[SweepEvent],
        spy_data: Optional[Dict],
        latest_es_price: float,
    ) -> Optional[AlertSignal]:
        # v5: Footprint must be present (primary)
        if footprint_sig is None:
            return None

        direction_str = "BUY_CALL" if footprint_sig.direction == "LONG" else "BUY_PUT"
        spy_price = spy_data.get("price", 590.0) if spy_data else 590.0
        entry = latest_es_price if latest_es_price else footprint_sig.entry_price
        level = footprint_sig.trigger_level

        # Compute stop / targets from level distance
        tick_size = ES_TICK_SIZE
        level_dist = abs(entry - level)
        if direction_str == "BUY_CALL":
            stop = level - tick_size * 2
            target_1 = entry + level_dist * 1.0
            target_2 = entry + level_dist * 2.0
            invalidation = level - tick_size * 4
        else:
            stop = level + tick_size * 2
            target_1 = entry - level_dist * 1.0
            target_2 = entry - level_dist * 2.0
            invalidation = level + tick_size * 4

        # v5: Confidence scoring
        base_conf = int(footprint_sig.confidence)
        # Sweep confirmation bonus
        sweep_bonus = 0
        if sweep_sig:
            # Check sweep aligns with footprint direction
            sweep_dir_ok = (
                (direction_str == "BUY_CALL" and sweep_sig.direction == "bullish_sweep") or
                (direction_str == "BUY_PUT" and sweep_sig.direction == "bearish_sweep")
            )
            if sweep_dir_ok:
                sweep_bonus = min(SWEEP_BONUS_CONFIDENCE, int(sweep_sig.confidence * 10))
                base_conf += sweep_bonus

        confidence = min(100, base_conf)
        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # Determine instrument from confidence
        if confidence >= 85:
            expiry, strike, instrument = ("0DTE", "ATM or slightly ITM", "SPY options")
        elif confidence >= 75:
            expiry, strike, instrument = ("1DTE", "ATM or slightly ITM", "SPY options")
        elif confidence >= 60:
            expiry, strike, instrument = ("weekly", "ATM or slightly OTM", "SPY options")
        else:
            expiry, strike, instrument = ("weekly", "ATM", "SPY shares")

        reasons = [f"Footprint {footprint_sig.setup_type} @ {footprint_sig.trigger_level:.2f}"]
        if sweep_sig and sweep_bonus > 0:
            reasons.append(f"Sweep confirm (+{sweep_bonus})")
        if footprint_sig.divergence_detected:
            reasons.append(f"Divergence: {footprint_sig.divergence_type}")
        if footprint_sig.absorption_detected:
            reasons.append(f"Absorption score: {footprint_sig.absorption_score:.1f}")

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

        return AlertSignal(
            direction=direction_str,
            underlying="SPY",
            es_level=round(level, 2),
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
            es_symbol="ES",
            es_trigger_price=latest_es_price,
            footprint_signal={
                "setup_type": footprint_sig.setup_type,
                "confidence": footprint_sig.confidence,
                "level_type": footprint_sig.level_type,
                "divergence": footprint_sig.divergence_type,
                "absorption_score": footprint_sig.absorption_score,
            } if footprint_sig else None,
            sweep_signal={
                "direction": sweep_sig.direction,
                "level": sweep_sig.level,
                "distance": sweep_sig.sweep_distance,
            } if sweep_sig else None,
        )

# ─── v5 Main Engine Skeleton ─────────────────────────────────────────────────
class LiveOrderflowAlertEngineV5:
    """
    v5 Engine: Footprint-primary, sweep-secondary.
    Skeleton — deterministic loop, dry-run only.
    """
    def __init__(self, watch_dir: str, spy_source: str, dry_run: bool = True, start_at_end: bool = False):
        self.watch_dir = Path(watch_dir)
        self.spy_source = spy_source
        self.dry_run = dry_run
        self.start_at_end = start_at_end

        self.checkpoints = load_checkpoints()
        self.reader = StreamingJSONLReader(self.checkpoints)
        self.alert_mgr = AlertManager()
        self.signal_builder = UnifiedSignalBuilder()

        # Engine state
        self.running = False
        self.total_events = 0
        self.signals_generated = 0
        self.start_time = 0.0
        self.latest_es_price = 0.0

        # Footprint pipeline components
        self.footprint_gen = FootprintEntrySignalGenerator(min_confidence=FOOTPRINT_MIN_CONFIDENCE)
        self.sweep_detector = SweepDetector()  # Old sweep engine (secondary)

        # Rolling window for tick-footprint candles
        self._trade_buffer: List[Dict] = []
        self._buffer_flush_size = 5000  # Rebuild candles every N trades

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

    def _process_footprint_signals(self, trade_events: List[Dict]) -> List[FootprintEntrySignal]:
        """Run footprint pipeline on latest trade buffer."""
        if len(trade_events) < 200:
            return []
        builder = TickFootprintBuilder(ticks_per_candle=20)
        candles = builder.ingest(trade_events)
        if len(candles) < 20:
            return []
        signals = self.footprint_gen.generate(candles)
        return signals

    def _process_sweep_signals(self, events: List[Dict]) -> List[SweepEvent]:
        """Run sweep detector (secondary confirmation)."""
        return self.sweep_detector.ingest(events)

    def process_cycle(self) -> List[AlertSignal]:
        signals: List[AlertSignal] = []
        files = self._list_watch_files()
        if not files:
            return signals
        latest_file = max(files, key=lambda p: p.stat().st_mtime)
        new_events = self.reader.read_new_events(latest_file)
        if not new_events:
            return signals
        self.total_events += len(new_events)

        # Filter ES trade events
        es_events = []
        for e in new_events:
            sym = e.get("symbol", "").upper()
            if "ES" in sym and "MES" not in sym:
                es_events.append(e)
                p = e.get("price", 0.0) or 0.0
                if p:
                    self.latest_es_price = p

        if not es_events:
            save_checkpoints(self.checkpoints)
            return signals

        trade_events = [e for e in es_events if e.get("event_type") == "trade"]

        # ─── v5: PRIMARY — Footprint signals ───────────────────────────────
        footprint_signals = self._process_footprint_signals(trade_events)
        _log.debug("Footprint signals: %d", len(footprint_signals))

        # ─── v5: SECONDARY — Sweep confirmations ────────────────────────────
        sweep_events = self._process_sweep_signals(es_events)
        sweep_by_level: Dict[float, SweepEvent] = {}
        for sw in sweep_events:
            sweep_by_level[round(sw.level, 2)] = sw

        spy_data = self._get_spy_data()

        # ─── v5: UNIFY — Match footprint with nearby sweep ─────────────────
        for fp in footprint_signals:
            # Find nearest sweep confirmation
            nearest_sweep = None
            fp_level = round(fp.trigger_level, 2)
            for lvl, sw in sweep_by_level.items():
                if abs(lvl - fp_level) <= ES_TICK_SIZE * 4:
                    nearest_sweep = sw
                    break

            signal = self.signal_builder.build_signal(
                footprint_sig=fp,
                sweep_sig=nearest_sweep,
                spy_data=spy_data,
                latest_es_price=self.latest_es_price,
            )
            if signal:
                signals.append(signal)
                self.signals_generated += 1

        save_checkpoints(self.checkpoints)
        return signals

    def run(self, interval_seconds: float = 5.0):
        self.running = True
        self.start_time = time.time()
        self._initialize_checkpoints()

        _log.info("=" * 60)
        _log.info("Live Orderflow Alert Engine v5.0 (SKELETON)")
        _log.info("Watch: %s", self.watch_dir)
        _log.info("SPY source: %s", self.spy_source)
        _log.info("DRY RUN: %s", "YES" if self.dry_run else "NO")
        _log.info("Footprint min confidence: %.0f", FOOTPRINT_MIN_CONFIDENCE)
        _log.info("Sweep bonus confidence: %d", SWEEP_BONUS_CONFIDENCE)
        _log.info("=" * 60)

        while self.running:
            now = et_now()
            now_str = now.strftime("%H:%M:%S ET")

            if not is_market_hours():
                _log.info("[%s] Market closed. Sleeping...", now_str)
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
                    _log.info("v5 SIGNAL: %s %s (confidence: %d)",
                              signal.direction, signal.underlying, signal.confidence)
                    if not self.dry_run:
                        # Placeholder — real WhatsApp integration later
                        send_whatsapp_placeholder(
                            f"v5 ALERT: {signal.direction} {signal.underlying} @ {signal.entry} (conf: {signal.confidence})"
                        )
                    self.alert_mgr.record_alert(signal)
                else:
                    _log.info("Signal suppressed: %s conf=%d",
                              signal.direction, signal.confidence)

            time.sleep(interval_seconds)

    def stop(self):
        self.running = False
        elapsed = time.time() - self.start_time
        events_per_sec = self.total_events / max(elapsed, 1)
        _log.info("Engine stopped. Events: %d, Signals: %d, EPS: %.3f",
                  self.total_events, self.signals_generated, events_per_sec)


# ─── CLI ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Live Orderflow Alert Engine v5.0 (Skeleton)")
    parser.add_argument("--watch", required=True, help="Directory to watch for JSONL files")
    parser.add_argument("--spy-source", default="cached", choices=["cached"])
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry-run mode (default=True). Set --no-dry-run to override.")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument("--start-at-end", action="store_true", default=False)
    args = parser.parse_args()

    engine = LiveOrderflowAlertEngineV5(
        watch_dir=args.watch,
        spy_source=args.spy_source,
        dry_run=args.dry_run,
        start_at_end=args.start_at_end,
    )
    try:
        engine.run(interval_seconds=args.interval)
    except KeyboardInterrupt:
        _log.info("\nShutdown requested...")
        engine.stop()
        print(f"\nEngine stopped. Events: {engine.total_events}, Signals: {engine.signals_generated}")


if __name__ == "__main__":
    main()
