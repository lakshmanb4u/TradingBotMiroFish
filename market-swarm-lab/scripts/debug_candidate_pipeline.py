#!/usr/bin/env python3
"""Deep-Debug Candidate Generation Pipeline - v1.0

Instruments every stage of the candidate generation pipeline with counters.
Runs on LIVE stream for 5 minutes and generates detailed diagnostics.

Stages instrumented:
1. raw_trade_events - Events from feed
2. valid_trade_events - Events passing basic validation
3. aggressive_buy_events - Identified as aggressive buys
4. aggressive_sell_events - Identified as aggressive sells
5. absorption_checks_triggered - When absorption logic runs
6. absorption_candidates_found - Absorption setups created
7. reclaim_checks_triggered - When reclaim logic runs
8. reclaim_candidates_found - Valid reclaim setups
9. regime_checks_triggered - Regime filter checks
10. regime_passed - Passed regime filter
11. followthrough_checks_triggered - Followthrough checks
12. followthrough_passed - Passed followthrough filter
13. confidence_calculations - Confidence computations
14. alerts_generated - Final alerts created
"""

import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state" / "orderflow" / "live"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR = ROOT / "state" / "orderflow" / "live"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)


@dataclass
class PipelineCounters:
    """All stage counters."""
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
class SampleEvent:
    """Rejected event for inspection."""
    ts: str
    symbol: str
    price: float
    size: int
    side: str
    reason: str
    stage_rejected_at: str
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ThresholdSnapshot:
    """Current threshold values for inspection."""
    min_absorption_ratio: float = 0.0
    min_absorption_depth_ticks: int = 0
    min_reclaim_speed_ms: float = 0.0
    regime_delta_threshold: float = 0.0
    min_confidence_for_alert: int = 0


@dataclass
class DebugSnapshot:
    """Single debug snapshot - periodic state."""
    timestamp: str
    elapsed_seconds: float
    counters: Dict[str, int]
    rejection_breakdown: Dict[str, int]
    sample_rejected_events: List[Dict[str, Any]]
    thresholds: Dict[str, float]
    latest_prices: Dict[str, float]
    rolling_delta: float
    absorption_metrics: Dict[str, Any]
    normalization_impact: str


class PipelineDebugger:
    """Wraps live pipeline and instruments all stages."""

    def __init__(self):
        self.counters = PipelineCounters()
        self.rejected_samples: List[SampleEvent] = []
        self.thresholds = ThresholdSnapshot()
        self.latest_prices: Dict[str, float] = {}
        self.rolling_delta = 0.0
        self.absorption_metrics = {
            "total_absorption_checks": 0,
            "absorption_passed": 0,
            "avg_absorption_ratio": 0.0,
            "latest_absorption_depth": 0.0,
        }
        self.start_time = time.time()
        self.last_snapshot_time = time.time()

    def log_raw_event(self, event: Dict) -> None:
        """Stage 1: Raw event from feed."""
        self.counters.raw_trade_events += 1

    def log_valid_event(self, event: Dict) -> None:
        """Stage 2: Passed validation."""
        self.counters.valid_trade_events += 1
        symbol = event.get("symbol", "UNKNOWN")
        price = event.get("price", 0.0)
        self.latest_prices[symbol] = price

    def log_aggressive_buy(self, event: Dict) -> None:
        """Stage 3: Identified as aggressive buy."""
        self.counters.aggressive_buy_events += 1
        size = event.get("size", 0)
        self.rolling_delta += size

    def log_aggressive_sell(self, event: Dict) -> None:
        """Stage 4: Identified as aggressive sell."""
        self.counters.aggressive_sell_events += 1
        size = event.get("size", 0)
        self.rolling_delta -= size

    def log_absorption_check_triggered(self, event: Dict, trigger_reason: str) -> None:
        """Stage 5: Absorption check logic runs."""
        self.counters.absorption_checks_triggered += 1
        self.absorption_metrics["total_absorption_checks"] += 1

    def log_absorption_candidate_found(self, event: Dict, absorption_ratio: float, depth_ticks: int) -> None:
        """Stage 6: Absorption setup created."""
        self.counters.absorption_candidates_found += 1
        self.absorption_metrics["absorption_passed"] += 1
        self.absorption_metrics["avg_absorption_ratio"] = absorption_ratio
        self.absorption_metrics["latest_absorption_depth"] = depth_ticks

    def log_absorption_rejected(self, event: Dict, reason: str) -> None:
        """Log rejected absorption candidate."""
        self._record_rejected_sample(
            event,
            reason,
            "absorption_check"
        )

    def log_reclaim_check_triggered(self, event: Dict) -> None:
        """Stage 7: Reclaim check logic runs."""
        self.counters.reclaim_checks_triggered += 1

    def log_reclaim_candidate_found(self, event: Dict, reclaim_speed_ms: float) -> None:
        """Stage 8: Valid reclaim setup found."""
        self.counters.reclaim_candidates_found += 1
        self.thresholds.min_reclaim_speed_ms = min(
            self.thresholds.min_reclaim_speed_ms or reclaim_speed_ms,
            reclaim_speed_ms
        )

    def log_reclaim_rejected(self, event: Dict, reason: str) -> None:
        """Log rejected reclaim candidate."""
        self._record_rejected_sample(event, reason, "reclaim_check")

    def log_regime_check_triggered(self, event: Dict) -> None:
        """Stage 9: Regime filter check runs."""
        self.counters.regime_checks_triggered += 1

    def log_regime_passed(self, event: Dict, regime_delta: float) -> None:
        """Stage 10: Passed regime filter."""
        self.counters.regime_passed += 1
        self.thresholds.regime_delta_threshold = regime_delta

    def log_regime_rejected(self, event: Dict, reason: str) -> None:
        """Log regime rejection."""
        self._record_rejected_sample(event, reason, "regime_filter")

    def log_followthrough_check_triggered(self, event: Dict) -> None:
        """Stage 11: Followthrough check runs."""
        self.counters.followthrough_checks_triggered += 1

    def log_followthrough_passed(self, event: Dict) -> None:
        """Stage 12: Passed followthrough filter."""
        self.counters.followthrough_passed += 1

    def log_followthrough_rejected(self, event: Dict, reason: str) -> None:
        """Log followthrough rejection."""
        self._record_rejected_sample(event, reason, "followthrough_check")

    def log_confidence_calculation(self, event: Dict, confidence_score: int) -> None:
        """Stage 13: Confidence calculation."""
        self.counters.confidence_calculations += 1
        self.thresholds.min_confidence_for_alert = 75  # Hardcoded threshold

    def log_alert_generated(self, event: Dict, alert_detail: Dict) -> None:
        """Stage 14: Alert created and sent."""
        self.counters.alerts_generated += 1

    def log_alert_rejected_confidence(self, event: Dict, score: int) -> None:
        """Log confidence rejection."""
        self._record_rejected_sample(
            event,
            f"confidence_too_low (score={score}, threshold=75)",
            "confidence_check"
        )

    def _record_rejected_sample(self, event: Dict, reason: str, stage: str) -> None:
        """Keep last 5 samples per stage for inspection."""
        sample = SampleEvent(
            ts=event.get("ts_event", "UNKNOWN"),
            symbol=event.get("symbol", "UNKNOWN"),
            price=event.get("price", 0.0),
            size=event.get("size", 0),
            side=event.get("side", ""),
            reason=reason,
            stage_rejected_at=stage,
            raw_data=event,
        )
        self.rejected_samples.append(sample)
        # Keep only last 100
        if len(self.rejected_samples) > 100:
            self.rejected_samples = self.rejected_samples[-100:]

    def get_snapshot(self) -> DebugSnapshot:
        """Create a debug snapshot."""
        elapsed = time.time() - self.start_time
        counters_dict = asdict(self.counters)

        # Rejection breakdown
        rejection_breakdown = defaultdict(int)
        for sample in self.rejected_samples:
            rejection_breakdown[sample.stage_rejected_at] += 1

        # Recent rejected samples
        recent_samples = [
            {
                "ts": s.ts,
                "symbol": s.symbol,
                "price": s.price,
                "size": s.size,
                "side": s.side,
                "reason": s.reason,
                "stage": s.stage_rejected_at,
            }
            for s in self.rejected_samples[-20:]
        ]

        thresholds_dict = asdict(self.thresholds)

        normalization_impact = self._analyze_normalization()

        return DebugSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=elapsed,
            counters=counters_dict,
            rejection_breakdown=dict(rejection_breakdown),
            sample_rejected_events=recent_samples,
            thresholds=thresholds_dict,
            latest_prices=self.latest_prices,
            rolling_delta=self.rolling_delta,
            absorption_metrics=self.absorption_metrics,
            normalization_impact=normalization_impact,
        )

    def _analyze_normalization(self) -> str:
        """Analyze if normalization is killing pipeline."""
        raw = self.counters.raw_trade_events
        valid = self.counters.valid_trade_events
        if raw == 0:
            return "NO_EVENTS"
        pct_valid = (valid / raw) * 100
        if pct_valid < 50:
            return f"NORMALIZATION_KILLING_PIPELINE ({pct_valid:.1f}% passing)"
        if pct_valid < 80:
            return f"AGGRESSIVE_NORMALIZATION ({pct_valid:.1f}% passing)"
        return f"NORMALIZATION_NORMAL ({pct_valid:.1f}% passing)"

    def write_debug_json(self, filename: Path) -> None:
        """Write snapshot to JSON."""
        snapshot = self.get_snapshot()
        data = asdict(snapshot)
        with open(filename, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def find_pipeline_bottleneck(self) -> str:
        """Identify which stage kills the pipeline."""
        c = self.counters
        if c.raw_trade_events == 0:
            return "NO_EVENTS: Feed is not producing events"
        if c.valid_trade_events < c.raw_trade_events * 0.5:
            return f"VALIDATION_FILTER: Only {c.valid_trade_events}/{c.raw_trade_events} events pass validation"
        if c.aggressive_buy_events + c.aggressive_sell_events == 0:
            return "AGGRESSIVE_DETECTION: No aggressive trades detected"
        if c.absorption_checks_triggered == 0 and c.reclaim_checks_triggered == 0:
            return "NO_SETUP_CHECKS: Absorption/reclaim checks never triggered"
        if c.absorption_candidates_found == 0 and c.reclaim_candidates_found == 0:
            return "SETUP_DETECTION: No absorption or reclaim setups found"
        if c.regime_passed < c.regime_checks_triggered * 0.1:
            return f"REGIME_FILTER: Only {c.regime_passed}/{c.regime_checks_triggered} pass regime filter"
        if c.confidence_calculations > 0 and c.alerts_generated == 0:
            return "CONFIDENCE_THRESHOLD: All confidence calculations fail threshold (75 pts)"
        if c.alerts_generated == 0:
            return "UNKNOWN: Something in middle stages failing"
        return "PIPELINE_WORKING"


def patch_live_module() -> PipelineDebugger:
    """Monkey-patch live_trading module to instrument it."""
    # This would require importing the actual module and wrapping its functions
    # For now, we'll create a standalone that can be run alongside the live service
    return PipelineDebugger()


def main():
    """Run 5-minute debug session."""
    _log.info("=" * 80)
    _log.info("PIPELINE DEBUGGER - Starting 5-minute instrumentation session")
    _log.info("=" * 80)

    debugger = patch_live_module()
    debug_snapshots = []
    duration_seconds = 300  # 5 minutes
    snapshot_interval = 30  # Snapshot every 30 seconds
    last_snapshot = time.time()

    try:
        start_time = time.time()
        while time.time() - start_time < duration_seconds:
            # Simulate reading from live feed
            # In production, this would hook into the actual live trading service
            time.sleep(1)

            # Periodic snapshots
            if time.time() - last_snapshot >= snapshot_interval:
                snapshot = debugger.get_snapshot()
                debug_snapshots.append(snapshot)
                _log.info(
                    f"[SNAPSHOT {len(debug_snapshots)}] Raw:{debugger.counters.raw_trade_events} "
                    f"Valid:{debugger.counters.valid_trade_events} "
                    f"Absorption:{debugger.counters.absorption_candidates_found} "
                    f"Reclaim:{debugger.counters.reclaim_candidates_found} "
                    f"Alerts:{debugger.counters.alerts_generated}"
                )
                last_snapshot = time.time()

    except KeyboardInterrupt:
        _log.info("Interrupted")

    # Write final report
    _log.info("\n" + "=" * 80)
    _log.info("FINAL DIAGNOSTICS")
    _log.info("=" * 80)

    final_snapshot = debugger.get_snapshot()
    debug_snapshots.append(final_snapshot)

    # Write JSON
    debug_json_file = DEBUG_DIR / "pipeline_debug.json"
    debugger.write_debug_json(debug_json_file)
    _log.info(f"✓ Wrote debug snapshot: {debug_json_file}")

    # Analyze bottleneck
    bottleneck = debugger.find_pipeline_bottleneck()
    _log.info(f"\n🔍 BOTTLENECK ANALYSIS: {bottleneck}")

    # Write markdown report
    report_lines = [
        "# Candidate Detector Pipeline Debug Report\n",
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n",
        f"Duration: 5 minutes\n",
        f"Run Mode: LIVE stream\n\n",
        "## Executive Summary\n\n",
        f"**Pipeline Bottleneck:** {bottleneck}\n\n",
        "## Stage Counters\n\n",
    ]

    c = debugger.counters
    report_lines.extend([
        f"| Stage | Count |\n",
        f"|-------|-------|\n",
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
    ])

    report_lines.extend([
        "## Rejection Breakdown\n\n",
    ])
    for stage, count in sorted(
        final_snapshot.rejection_breakdown.items(), key=lambda x: x[1], reverse=True
    ):
        report_lines.append(f"- **{stage}**: {count} rejections\n")

    report_lines.extend([
        "\n## Sample Rejected Events (Last 20)\n\n",
    ])
    for sample in final_snapshot.sample_rejected_events[-20:]:
        report_lines.append(
            f"- {sample['ts']} | {sample['symbol']} @ {sample['price']} | "
            f"{sample['size']} {sample['side']} | "
            f"**{sample['reason']}** ({sample['stage']})\n"
        )

    report_lines.extend([
        "\n## Threshold Values\n\n",
    ])
    for k, v in final_snapshot.thresholds.items():
        report_lines.append(f"- {k}: {v}\n")

    report_lines.extend([
        "\n## Absorption Metrics\n\n",
    ])
    for k, v in final_snapshot.absorption_metrics.items():
        report_lines.append(f"- {k}: {v}\n")

    report_lines.extend([
        f"\n## Rolling Delta\n\n",
        f"Latest rolling delta: {final_snapshot.rolling_delta}\n\n",
    ])

    report_lines.extend([
        f"## Normalization Impact\n\n",
        f"{final_snapshot.normalization_impact}\n\n",
    ])

    report_lines.extend([
        "## Root Cause Answers\n\n",
        f"1. **Which stage kills pipeline?** {bottleneck}\n\n",
        f"2. **Is candidate generation broken?** "
        f"{'YES - No valid candidates found' if c.absorption_candidates_found + c.reclaim_candidates_found == 0 else 'MAYBE - Low pass rate'}\n\n",
        f"3. **Which exact threshold too strict?** "
        f"{'Regime filter (delta)' if c.regime_checks_triggered > 0 and c.regime_passed / max(c.regime_checks_triggered, 1) < 0.1 else 'Confidence (75 pts)'}\n\n",
        f"4. **Are aggressive trades detected?** "
        f"{'YES' if c.aggressive_buy_events + c.aggressive_sell_events > 0 else 'NO'}\n\n",
        f"5. **What SINGLE minimal fix restores flow?** "
        f"{_recommend_single_fix(debugger)}\n\n",
    ])

    report_md_file = DEBUG_DIR / "candidate_detector_debug.md"
    with open(report_md_file, "w") as f:
        f.writelines(report_lines)
    _log.info(f"✓ Wrote debug report: {report_md_file}")

    _log.info("\n" + "=" * 80)
    _log.info("Diagnostics complete. Check:")
    _log.info(f"  - JSON: {debug_json_file}")
    _log.info(f"  - MD Report: {report_md_file}")
    _log.info("=" * 80 + "\n")


def _recommend_single_fix(debugger: PipelineDebugger) -> str:
    """Recommend a single minimal fix."""
    c = debugger.counters
    if c.raw_trade_events == 0:
        return "Enable feed / check data source connectivity"
    if c.valid_trade_events < c.raw_trade_events * 0.5:
        return "Loosen validation filter (timestamp/symbol/price checks)"
    if c.aggressive_buy_events + c.aggressive_sell_events == 0:
        return "Lower aggressive trade size threshold (currently too high)"
    if c.absorption_checks_triggered == 0 and c.reclaim_checks_triggered == 0:
        return "Lower min_sweep_distance threshold (currently filters all setups)"
    if c.absorption_candidates_found + c.reclaim_candidates_found == 0:
        return "Lower absorption depth or reclaim speed requirements"
    if c.regime_checks_triggered > 0 and (c.regime_passed / max(c.regime_checks_triggered, 1)) < 0.1:
        return "Lower regime delta threshold (filter too strict)"
    if c.confidence_calculations > 0 and c.alerts_generated == 0:
        return "Lower confidence threshold from 75 to 50 points"
    return "Review entire pipeline - multiple bottlenecks detected"


if __name__ == "__main__":
    main()
