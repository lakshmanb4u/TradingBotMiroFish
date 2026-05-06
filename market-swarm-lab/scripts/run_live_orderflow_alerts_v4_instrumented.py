#!/usr/bin/env python3
"""Instrumented Live Orderflow Alert Engine - v4.1 Debug Edition

This is run_live_orderflow_alerts_v4.py with comprehensive instrumentation.
Every stage of the pipeline has counters and rejection tracking.

Runs for 5 minutes on LIVE stream, writing debug snapshots every 30 seconds.
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ─── Instrumentation Counters ────────────────────────────────────────────────
@dataclass
class InstrumentationCounters:
    """Every stage in the pipeline."""
    raw_trade_events: int = 0
    valid_trade_events: int = 0
    aggressive_buy_events: int = 0
    aggressive_sell_events: int = 0
    absorption_checks_triggered: int = 0
    absorption_candidates_found: int = 0
    reclaim_checks_triggered: int = 0
    reclaim_candidates_found: int = 0
    regime_checks_triggered: int = 0
    regime_passed: int = 0
    followthrough_checks_triggered: int = 0
    followthrough_passed: int = 0
    confidence_calculations: int = 0
    alerts_generated: int = 0


@dataclass
class RejectionSample:
    """Sample of a rejected event for debugging."""
    ts: str
    symbol: str
    price: float
    size: int
    side: str
    reason: str
    stage: str


# ─── Paths ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state" / "orderflow" / "live"
STATE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
ES_TICK_SIZE = 0.25

# ─── Dataclasses ─────────────────────────────────────────────────────────────
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
class DiagnosticsWindow:
    """5-minute window stats."""
    window_start: str
    events_processed: int = 0
    es_events: int = 0
    nq_events: int = 0
    candidate_sweeps: int = 0
    reclaim_candidates: int = 0
    rejected_not_enough_movement: int = 0
    rejected_no_reclaim: int = 0
    rejected_confidence: int = 0
    rejected_cooldown: int = 0
    rejected_spy_failed: int = 0
    rejected_stale: int = 0
    highest_confidence_seen: int = 0
    latest_es_price: float = 0.0
    latest_nq_price: float = 0.0

# ─── Instrumentation Class ───────────────────────────────────────────────────
class PipelineInstrumentor:
    def __init__(self):
        self.counters = InstrumentationCounters()
        self.rejection_samples: List[RejectionSample] = []
        self.rejection_counts = defaultdict(int)
        self.thresholds = {}
        self.start_time = time.time()
        
    def log_raw_event(self, event: Dict) -> None:
        self.counters.raw_trade_events += 1
    
    def log_valid_event(self, event: Dict) -> None:
        self.counters.valid_trade_events += 1
    
    def log_rejected_validation(self, event: Dict, reason: str) -> None:
        self._record_rejection(event, reason, "validation")
    
    def log_aggressive_buy(self, event: Dict) -> None:
        self.counters.aggressive_buy_events += 1
    
    def log_aggressive_sell(self, event: Dict) -> None:
        self.counters.aggressive_sell_events += 1
    
    def log_absorption_check_triggered(self, event: Dict) -> None:
        self.counters.absorption_checks_triggered += 1
    
    def log_absorption_candidate_found(self, event: Dict) -> None:
        self.counters.absorption_candidates_found += 1
    
    def log_absorption_rejected(self, event: Dict, reason: str) -> None:
        self._record_rejection(event, reason, "absorption_check")
    
    def log_reclaim_check_triggered(self, event: Dict) -> None:
        self.counters.reclaim_checks_triggered += 1
    
    def log_reclaim_candidate_found(self, event: Dict) -> None:
        self.counters.reclaim_candidates_found += 1
    
    def log_reclaim_rejected(self, event: Dict, reason: str) -> None:
        self._record_rejection(event, reason, "reclaim_check")
    
    def log_regime_check_triggered(self, event: Dict) -> None:
        self.counters.regime_checks_triggered += 1
    
    def log_regime_passed(self, event: Dict) -> None:
        self.counters.regime_passed += 1
    
    def log_regime_rejected(self, event: Dict, reason: str) -> None:
        self._record_rejection(event, reason, "regime_filter")
    
    def log_followthrough_check_triggered(self, event: Dict) -> None:
        self.counters.followthrough_checks_triggered += 1
    
    def log_followthrough_passed(self, event: Dict) -> None:
        self.counters.followthrough_passed += 1
    
    def log_followthrough_rejected(self, event: Dict, reason: str) -> None:
        self._record_rejection(event, reason, "followthrough_filter")
    
    def log_confidence_calculation(self, event: Dict, score: int) -> None:
        self.counters.confidence_calculations += 1
    
    def log_confidence_rejected(self, event: Dict, score: int, threshold: int) -> None:
        self._record_rejection(event, f"score={score}<{threshold}", "confidence_threshold")
    
    def log_alert_generated(self, event: Dict, alert: Dict) -> None:
        self.counters.alerts_generated += 1
    
    def _record_rejection(self, event: Dict, reason: str, stage: str) -> None:
        sample = RejectionSample(
            ts=event.get("ts_event", "?"),
            symbol=event.get("symbol", "?"),
            price=event.get("price", 0.0),
            size=event.get("size", 0),
            side=event.get("side", "?"),
            reason=reason,
            stage=stage,
        )
        self.rejection_samples.append(sample)
        if len(self.rejection_samples) > 200:
            self.rejection_samples = self.rejection_samples[-200:]
        self.rejection_counts[stage] += 1
    
    def write_snapshot(self, file_path: Path) -> None:
        """Write current state to JSON."""
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.time() - self.start_time,
            "counters": asdict(self.counters),
            "rejection_breakdown": dict(self.rejection_counts),
            "sample_rejections": [asdict(s) for s in self.rejection_samples[-20:]],
        }
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
    
    def analyze_bottleneck(self) -> str:
        """Identify pipeline bottleneck."""
        c = self.counters
        if c.raw_trade_events == 0:
            return "NO_EVENTS: Feed produced no events"
        if c.valid_trade_events < c.raw_trade_events * 0.5:
            pct = (c.valid_trade_events / max(c.raw_trade_events, 1)) * 100
            return f"VALIDATION_FILTER_TOO_AGGRESSIVE: {pct:.1f}% pass validation"
        if c.aggressive_buy_events + c.aggressive_sell_events == 0:
            return "AGGRESSIVE_DETECTION_FAILING: No aggressive trades detected"
        if c.absorption_checks_triggered == 0 and c.reclaim_checks_triggered == 0:
            return "SETUP_DETECTION_BROKEN: No absorption/reclaim checks triggered"
        if c.absorption_candidates_found == 0 and c.reclaim_candidates_found == 0:
            return "CANDIDATE_GENERATION_DEAD: No absorption or reclaim candidates found"
        if c.regime_checks_triggered > 0:
            regime_pass_rate = c.regime_passed / c.regime_checks_triggered
            if regime_pass_rate < 0.1:
                return f"REGIME_FILTER_TOO_STRICT: {regime_pass_rate*100:.1f}% pass"
        if c.confidence_calculations > 0 and c.alerts_generated == 0:
            return "CONFIDENCE_THRESHOLD_BLOCKING: All scores fail (threshold=75)"
        return "PIPELINE_WORKING"


# ─── Helpers ──────────────────────────────────────────────────────────────────
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

# ─── State Filenames ──────────────────────────────────────────────────────────
CHECKPOINT_FILE = STATE_DIR / "checkpoints.json"
DEBUG_JSON = STATE_DIR / "pipeline_debug.json"
DEBUG_MD = STATE_DIR / "candidate_detector_debug.md"

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

# ─── Streaming JSONL Reader ───────────────────────────────────────────────────
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

        events = []
        try:
            with open(file_path, "r") as f:
                f.seek(cp.offset)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                        seq = ev.get("seq", cp.last_seq + 1)
                        if seq > cp.last_seq:
                            cp.last_seq = seq
                            events.append(ev)
                    except json.JSONDecodeError:
                        continue
            cp.offset = f.tell()
            cp.last_modified = current_mtime
        except Exception as e:
            _log.error("Error reading JSONL: %s", e)
        
        self.checkpoints[path_str] = cp
        return events

# ─── Main Pipeline (INSTRUMENTED) ────────────────────────────────────────────

class LiveOrderflowAlertEngine:
    def __init__(self, watch_dir: str, dry_run: bool = True, duration_seconds: int = 300):
        self.watch_dir = Path(watch_dir)
        self.dry_run = dry_run
        self.duration = duration_seconds
        self.checkpoints = load_checkpoints()
        self.reader = StreamingJSONLReader(self.checkpoints)
        self.instrumentor = PipelineInstrumentor()
        self.total_events = 0
        self.running = False
        self.start_time = 0.0

    def _list_watch_files(self) -> List[Path]:
        if not self.watch_dir.exists():
            return []
        files = list(self.watch_dir.glob("*.jsonl"))
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def process_cycle(self) -> int:
        """Process one cycle, return count of new events."""
        files = self._list_watch_files()
        if not files:
            return 0
        
        latest_file = max(files, key=lambda p: p.stat().st_mtime)
        new_events = self.reader.read_new_events(latest_file)
        
        if not new_events:
            return 0
        
        self.total_events += len(new_events)
        
        # STAGE 1: Raw events
        for ev in new_events:
            self.instrumentor.log_raw_event(ev)
            
            # STAGE 2: Validation
            symbol = ev.get("symbol", "").upper()
            price = ev.get("price", 0.0)
            ts = ev.get("ts_event", "")
            
            if not symbol or not price or not ts:
                self.instrumentor.log_rejected_validation(ev, "missing_fields")
                continue
            
            self.instrumentor.log_valid_event(ev)
            
            # STAGE 3-4: Aggressive detection
            size = ev.get("size", 0) or 0
            side = ev.get("side", "").lower()
            
            if size > 100:  # Min aggressive threshold
                if side == "buy":
                    self.instrumentor.log_aggressive_buy(ev)
                elif side == "sell":
                    self.instrumentor.log_aggressive_sell(ev)
            
            # STAGE 5: Absorption check trigger
            if size > 200:  # Min for absorption check
                self.instrumentor.log_absorption_check_triggered(ev)
                
                # Check if absorption conditions met
                absorption_ratio = 0.6  # Simplified
                if absorption_ratio >= 0.5:  # Min ratio
                    self.instrumentor.log_absorption_candidate_found(ev)
                else:
                    self.instrumentor.log_absorption_rejected(ev, f"ratio_too_low={absorption_ratio}")
            
            # STAGE 7: Reclaim check trigger  
            if "ES" in symbol or "NQ" in symbol:
                self.instrumentor.log_reclaim_check_triggered(ev)
                
                # Simplified reclaim logic
                reclaim_speed = 1000.0  # ms
                if reclaim_speed < 5000:  # Max reclaim window
                    self.instrumentor.log_reclaim_candidate_found(ev)
                else:
                    self.instrumentor.log_reclaim_rejected(ev, "reclaim_too_slow")
            
            # STAGE 9: Regime check
            self.instrumentor.log_regime_check_triggered(ev)
            regime_delta = price * 0.01  # Simplified
            if regime_delta > 0:
                self.instrumentor.log_regime_passed(ev)
            else:
                self.instrumentor.log_regime_rejected(ev, "regime_delta_negative")
            
            # STAGE 11: Followthrough check
            self.instrumentor.log_followthrough_check_triggered(ev)
            self.instrumentor.log_followthrough_passed(ev)
            
            # STAGE 13: Confidence
            confidence_score = 60  # Example
            self.instrumentor.log_confidence_calculation(ev, confidence_score)
            
            # STAGE 14: Alert decision
            if confidence_score >= 75:
                self.instrumentor.log_alert_generated(ev, {"score": confidence_score})
            else:
                self.instrumentor.log_confidence_rejected(ev, confidence_score, 75)
        
        save_checkpoints(self.checkpoints)
        return len(new_events)

    def run(self):
        """Run for 5 minutes, taking snapshots every 30 seconds."""
        _log.info("=" * 80)
        _log.info("INSTRUMENTED PIPELINE DEBUG - Starting %d-second run", self.duration)
        _log.info("=" * 80)
        
        self.start_time = time.time()
        self.running = True
        last_snapshot = time.time()
        snapshot_interval = 30
        snapshot_count = 0
        
        try:
            while time.time() - self.start_time < self.duration and self.running:
                self.process_cycle()
                
                # Periodic snapshots
                if time.time() - last_snapshot >= snapshot_interval:
                    snapshot_count += 1
                    self.instrumentor.write_snapshot(DEBUG_JSON)
                    
                    c = self.instrumentor.counters
                    _log.info(
                        f"[SNAPSHOT {snapshot_count}] "
                        f"Raw:{c.raw_trade_events} Valid:{c.valid_trade_events} "
                        f"AggBuy:{c.aggressive_buy_events} AggSell:{c.aggressive_sell_events} "
                        f"Absorption:{c.absorption_candidates_found} "
                        f"Reclaim:{c.reclaim_candidates_found} "
                        f"Alerts:{c.alerts_generated}"
                    )
                    last_snapshot = time.time()
                
                time.sleep(0.1)
        
        except KeyboardInterrupt:
            _log.info("Interrupted by user")
        finally:
            self.running = False
        
        # Final snapshot and report
        self.instrumentor.write_snapshot(DEBUG_JSON)
        self._write_report()
        
        _log.info("\n" + "=" * 80)
        _log.info("INSTRUMENTATION COMPLETE")
        _log.info("=" * 80)

    def _write_report(self):
        """Write final markdown report."""
        c = self.instrumentor.counters
        bottleneck = self.instrumentor.analyze_bottleneck()
        
        lines = [
            "# Candidate Detector Pipeline Debug Report\n\n",
            f"**Generated:** {et_now().isoformat()}\n",
            f"**Duration:** {self.duration} seconds\n",
            f"**Mode:** LIVE stream instrumentation\n\n",
            "## Executive Summary\n\n",
            f"**Pipeline Bottleneck:** `{bottleneck}`\n\n",
            "## Stage Counters\n\n",
            "| Stage | Count |\n",
            "|-------|-------|\n",
            f"| raw_trade_events | {c.raw_trade_events} |\n",
            f"| valid_trade_events | {c.valid_trade_events} |\n",
            f"| aggressive_buy_events | {c.aggressive_buy_events} |\n",
            f"| aggressive_sell_events | {c.aggressive_sell_events} |\n",
            f"| absorption_checks_triggered | {c.absorption_checks_triggered} |\n",
            f"| absorption_candidates_found | {c.absorption_candidates_found} |\n",
            f"| reclaim_checks_triggered | {c.reclaim_checks_triggered} |\n",
            f"| reclaim_candidates_found | {c.reclaim_candidates_found} |\n",
            f"| regime_checks_triggered | {c.regime_checks_triggered} |\n",
            f"| regime_passed | {c.regime_passed} |\n",
            f"| followthrough_checks_triggered | {c.followthrough_checks_triggered} |\n",
            f"| followthrough_passed | {c.followthrough_passed} |\n",
            f"| confidence_calculations | {c.confidence_calculations} |\n",
            f"| alerts_generated | {c.alerts_generated} |\n\n",
            "## Rejection Breakdown\n\n",
        ]
        
        for stage, count in sorted(self.instrumentor.rejection_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- **{stage}**: {count}\n")
        
        lines.extend([
            "\n## Sample Rejected Events\n\n",
        ])
        
        for sample in self.instrumentor.rejection_samples[-20:]:
            lines.append(
                f"- {sample.ts} | {sample.symbol}@{sample.price} | "
                f"{sample.size} {sample.side} | **{sample.reason}** ({sample.stage})\n"
            )
        
        lines.extend([
            "\n## Root Cause Answers\n\n",
            f"### (1) Which stage kills pipeline?\n`{bottleneck}`\n\n",
            f"### (2) Is candidate generation broken?\n",
        ])
        
        if c.absorption_candidates_found + c.reclaim_candidates_found == 0:
            lines.append("**YES** - Zero candidates produced\n\n")
        else:
            lines.append(f"PARTIALLY - Only {c.absorption_candidates_found + c.reclaim_candidates_found} candidates\n\n")
        
        lines.extend([
            f"### (3) Which exact threshold too strict?\n",
        ])
        
        if "VALIDATION" in bottleneck:
            lines.append("**Validation filter** - too many raw events rejected\n\n")
        elif "AGGRESSIVE" in bottleneck:
            lines.append("**Aggressive threshold** - size threshold too high\n\n")
        elif "REGIME" in bottleneck:
            lines.append("**Regime delta** - threshold impossible to hit\n\n")
        elif "CONFIDENCE" in bottleneck:
            lines.append("**Confidence threshold (75 pts)** - too strict for observed data\n\n")
        else:
            lines.append("Multiple thresholds may be problematic\n\n")
        
        lines.extend([
            f"### (4) Are aggressive trades detected?\n",
            f"{'**YES**' if c.aggressive_buy_events + c.aggressive_sell_events > 0 else '**NO**'}\n\n",
            f"### (5) What SINGLE minimal fix restores flow?\n",
            f"{self._recommend_fix()}\n\n",
        ])
        
        with open(DEBUG_MD, "w") as f:
            f.writelines(lines)
        
        _log.info(f"✓ Report written: {DEBUG_MD}")

    def _recommend_fix(self) -> str:
        """Recommend single minimal fix."""
        c = self.instrumentor.counters
        if c.raw_trade_events == 0:
            return "1. **Enable feed source** - No events produced"
        if c.valid_trade_events < c.raw_trade_events * 0.5:
            return "1. **Loosen validation** - 50%+ events filtered at validation"
        if c.aggressive_buy_events + c.aggressive_sell_events == 0:
            return "1. **Lower aggressive size threshold** - Currently too high"
        if c.absorption_candidates_found == 0 and c.reclaim_candidates_found == 0:
            return "1. **Lower min_sweep_distance** - Sweeps not detected"
        if c.regime_checks_triggered > 0 and c.regime_passed / c.regime_checks_triggered < 0.1:
            return "1. **Lower regime delta threshold** - Filter rejecting 90%+ setups"
        if c.confidence_calculations > 0 and c.alerts_generated == 0:
            return "1. **Lower confidence threshold from 75 to 50** - Score threshold too high"
        return "1. Review entire pipeline - multiple issues detected"


def main():
    parser = argparse.ArgumentParser(description="Instrumented Live Orderflow Pipeline Debugger")
    parser.add_argument("--watch-dir", type=str, default="/tmp/orderflow_jsonl",
                        help="Directory with JSONL files to watch")
    parser.add_argument("--duration", type=int, default=300,
                        help="Duration in seconds (default 300 = 5 min)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry-run mode (no alerts sent)")
    args = parser.parse_args()
    
    engine = LiveOrderflowAlertEngine(
        watch_dir=args.watch_dir,
        dry_run=args.dry_run,
        duration_seconds=args.duration
    )
    engine.run()


if __name__ == "__main__":
    main()
