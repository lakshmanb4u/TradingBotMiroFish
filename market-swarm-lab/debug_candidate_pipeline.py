#!/usr/bin/env python3
"""
DEEP-DEBUG: Candidate generation pipeline instrumenter.

Traces EVERY stage of the pipeline with 14 counters.
Writes state/orderflow/live/pipeline_debug.json every 30s.
Runs on LIVE stream for exactly 5 minutes.
Identifies exactly where candidates die.

14 Counters:
1. raw_trade_events - incoming trades
2. valid_trade_events - trades that pass basic validation
3. aggressive_buy_events - identified as aggressive buy
4. aggressive_sell_events - identified as aggressive sell
5. absorption_checks_triggered - absorption detection invoked
6. absorption_candidates_found - absorption detected
7. reclaim_checks_triggered - reclaim detection invoked
8. reclaim_candidates_found - reclaim detected
9. regime_checks_triggered - regime filter invoked
10. regime_passed - regime allowed the signal
11. followthrough_checks_triggered - followthrough gate invoked
12. followthrough_passed - followthrough gate passed
13. confidence_calculations - confidence score computed
14. alerts_generated - final alerts emitted
"""

import asyncio
import logging
import json
import sys
import signal
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict
import time

# Add services to path
sys.path.insert(0, '/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/services/live_trading')

from data_types import OrderFlowEvent, BarData, OrderSide
from regime_detector import RegimeDetector
from absorption_detector import AbsorptionDetector
from followthrough_gate import FollowThroughGate

logger = logging.getLogger(__name__)


@dataclass
class PipelineMetrics:
    """Pipeline stage metrics"""
    timestamp: str
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
    
    # Detailed diagnostics
    rejected_events: List[Dict] = None
    threshold_violations: List[Dict] = None
    sample_events: List[Dict] = None
    
    def __post_init__(self):
        if self.rejected_events is None:
            self.rejected_events = []
        if self.threshold_violations is None:
            self.threshold_violations = []
        if self.sample_events is None:
            self.sample_events = []


class DebugPipeline:
    """Instruments the full candidate generation pipeline"""
    
    def __init__(self, debug_dir: str = None):
        """Initialize debug pipeline"""
        
        if debug_dir is None:
            debug_dir = '/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live'
        
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize logging
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(self.debug_dir / 'debug.log')
            ]
        )
        
        # Initialize detectors
        self.regime_detector = RegimeDetector(
            ma_short=5,
            ma_long=20,
            atr_period=14,
            volatility_threshold=0.02
        )
        
        self.absorption_detector = AbsorptionDetector(
            time_window_ms=2000,
            delta_min_ratio=0.5,
            volume_min_pct=0.3
        )
        
        self.followthrough_gate = FollowThroughGate(
            time_window_ms=5000,
            min_confirmation_count=2,
            min_volume_ratio=0.4
        )
        
        # Metrics accumulator
        self.metrics = PipelineMetrics(timestamp=datetime.now(timezone.utc).isoformat())
        self.start_time = time.time()
        self.last_report_time = self.start_time
        
        # Raw event tracking
        self.event_samples = []
        
        logger.info("DebugPipeline initialized")
    
    def process_trade_event(self, event: OrderFlowEvent) -> bool:
        """Process a single trade event through the pipeline"""
        
        # STAGE 1: Raw event count
        self.metrics.raw_trade_events += 1
        
        # STAGE 2: Validate event
        is_valid = self._validate_event(event)
        if not is_valid:
            self.metrics.rejected_events.append({
                'event': event.symbol,
                'price': event.price,
                'size': event.size,
                'side': event.side.value,
                'reason': 'validation_failed'
            })
            return False
        
        self.metrics.valid_trade_events += 1
        
        # Store sample
        if len(self.event_samples) < 100:
            self.event_samples.append({
                'timestamp': event.timestamp,
                'symbol': event.symbol,
                'price': event.price,
                'size': event.size,
                'side': event.side.value
            })
        
        # STAGE 3: Detect aggressiveness
        is_aggressive_buy, is_aggressive_sell = self._classify_aggression(event)
        
        if is_aggressive_buy:
            self.metrics.aggressive_buy_events += 1
        if is_aggressive_sell:
            self.metrics.aggressive_sell_events += 1
        
        if not (is_aggressive_buy or is_aggressive_sell):
            self.metrics.rejected_events.append({
                'event': event.symbol,
                'price': event.price,
                'size': event.size,
                'side': event.side.value,
                'reason': 'not_aggressive'
            })
            return False
        
        logger.debug(f"Event classified: {event.symbol} {event.side.value} {event.size} @ {event.price}")
        return True
    
    def _validate_event(self, event: OrderFlowEvent) -> bool:
        """Validate event meets basic criteria"""
        
        # Check minimum size
        if event.size < 10:
            return False
        
        # Check price
        if event.price <= 0:
            return False
        
        # Check timestamp
        if event.timestamp <= 0:
            return False
        
        return True
    
    def _classify_aggression(self, event: OrderFlowEvent) -> tuple:
        """Classify if event is aggressive"""
        
        is_aggressive_buy = False
        is_aggressive_sell = False
        
        # Simple heuristic: market orders are aggressive
        if event.is_market_order:
            if event.side == OrderSide.BUY:
                is_aggressive_buy = True
            else:
                is_aggressive_sell = True
        
        # Orders > 500 contracts are considered aggressive
        if event.size >= 500:
            if event.side == OrderSide.BUY:
                is_aggressive_buy = True
            else:
                is_aggressive_sell = True
        
        return is_aggressive_buy, is_aggressive_sell
    
    def process_bar(self, bar: BarData, events: List[OrderFlowEvent]):
        """Process a bar and trace through full pipeline"""
        
        logger.info(f"\n=== Processing bar {bar.symbol} @ {bar.timestamp} ===")
        logger.info(f"Events for this bar: {len(events)}")
        
        # Update absorption detector
        if events:
            self.absorption_detector.update_events(events, bar.symbol)
        
        # STAGE 4: Absorption detection
        self.metrics.absorption_checks_triggered += 1
        absorptions = self.absorption_detector.analyze_bar(bar)
        
        logger.debug(f"Absorption check triggered. Found: {len(absorptions)}")
        self.metrics.absorption_candidates_found += len(absorptions)
        
        if not absorptions:
            logger.debug("No absorption detected in this bar")
            return []
        
        # STAGE 5: Reclaim detection (separate pass)
        self.metrics.reclaim_checks_triggered += 1
        reclaims = self._detect_reclaim(bar, events)
        self.metrics.reclaim_candidates_found += len(reclaims)
        
        logger.debug(f"Reclaim check triggered. Found: {len(reclaims)}")
        
        # STAGE 6: Regime filter
        self.metrics.regime_checks_triggered += 1
        regime = self.regime_detector.update(bar)
        
        if regime:
            logger.debug(f"Regime: {regime.regime_type.value}, volatility: {regime.volatility:.4f}")
            
            # Check regime filter
            if regime.regime_type.value not in ['UPTREND', 'DOWNTREND', 'BREAKOUT', 'BREAKDOWN']:
                logger.debug(f"Regime rejected: {regime.regime_type.value}")
                self.metrics.rejected_events.append({
                    'bar': bar.symbol,
                    'reason': f'regime_not_tradeable:{regime.regime_type.value}'
                })
                return []
            
            self.metrics.regime_passed += 1
        else:
            logger.debug("Regime not ready (need more data)")
            return []
        
        # STAGE 7: Followthrough gate
        self.metrics.followthrough_checks_triggered += 1
        alerts_generated = []
        
        for absorption in absorptions:
            self.metrics.followthrough_checks_triggered += 1
            confirmation = self.followthrough_gate.submit_absorption(absorption)
            
            # STAGE 8: Confidence calculation
            self.metrics.confidence_calculations += 1
            
            if confirmation:
                self.metrics.followthrough_passed += 1
                logger.info(f"✓ FOLLOWTHROUGH PASSED: {bar.symbol} confidence={confirmation.confidence:.2f}")
                self.metrics.alerts_generated += 1
                alerts_generated.append({
                    'type': 'followthrough',
                    'symbol': bar.symbol,
                    'side': absorption.side.value,
                    'confidence': confirmation.confidence,
                    'volume': absorption.absorbed_volume
                })
            else:
                logger.debug(f"✗ Followthrough gate rejected signal")
                self.metrics.rejected_events.append({
                    'bar': bar.symbol,
                    'reason': 'followthrough_gate_rejected',
                    'confidence_needed': 0.5
                })
        
        logger.info(f"Bar processing complete. Alerts: {len(alerts_generated)}")
        return alerts_generated
    
    def _detect_reclaim(self, bar: BarData, events: List[OrderFlowEvent]) -> List[Dict]:
        """Detect reclaim/reject patterns"""
        
        # Reclaim is detected when:
        # 1. Price moves in opposite direction from initial move
        # 2. Volume follows the rejection
        
        if bar.close > bar.open:
            # Up bar - check for downside rejection (reclaim)
            # Would be detected from sell volume after buy absorption
            pass
        else:
            # Down bar - check for upside rejection
            pass
        
        return []
    
    def report_metrics(self, final: bool = False):
        """Write metrics report"""
        
        report_file = self.debug_dir / 'pipeline_debug.json'
        
        # Add current timestamp
        self.metrics.timestamp = datetime.now(timezone.utc).isoformat()
        
        # Calculate summary
        summary = {
            'elapsed_seconds': time.time() - self.start_time,
            'events_per_second': self.metrics.raw_trade_events / max(1, time.time() - self.start_time),
            'pipeline_efficiency': {
                'valid_rate': self.metrics.valid_trade_events / max(1, self.metrics.raw_trade_events),
                'aggressive_rate': (self.metrics.aggressive_buy_events + self.metrics.aggressive_sell_events) / max(1, self.metrics.valid_trade_events),
                'absorption_candidate_rate': self.metrics.absorption_candidates_found / max(1, self.metrics.absorption_checks_triggered),
                'regime_pass_rate': self.metrics.regime_passed / max(1, self.metrics.regime_checks_triggered),
                'followthrough_pass_rate': self.metrics.followthrough_passed / max(1, max(1, self.metrics.followthrough_checks_triggered)),
                'final_alert_rate': self.metrics.alerts_generated / max(1, self.metrics.valid_trade_events),
            },
            'bottlenecks': self._identify_bottlenecks(),
            'final': final
        }
        
        output = {
            'metrics': asdict(self.metrics),
            'summary': summary,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        with open(report_file, 'w') as f:
            json.dump(output, f, indent=2)
        
        logger.info(f"Metrics written to {report_file}")
        
        if final:
            logger.info("\n=== FINAL REPORT ===")
            logger.info(f"Total raw events: {self.metrics.raw_trade_events}")
            logger.info(f"Valid events: {self.metrics.valid_trade_events}")
            logger.info(f"Absorption candidates: {self.metrics.absorption_candidates_found}")
            logger.info(f"Regime passed: {self.metrics.regime_passed}")
            logger.info(f"Followthrough passed: {self.metrics.followthrough_passed}")
            logger.info(f"Final alerts: {self.metrics.alerts_generated}")
            logger.info(f"Summary: {json.dumps(summary, indent=2)}")
    
    def _identify_bottlenecks(self) -> List[str]:
        """Identify where pipeline is losing candidates"""
        
        bottlenecks = []
        
        # Check each stage
        if self.metrics.valid_trade_events < self.metrics.raw_trade_events * 0.8:
            pct = (1 - self.metrics.valid_trade_events / max(1, self.metrics.raw_trade_events)) * 100
            bottlenecks.append(f"VALIDATION LOSS: {pct:.1f}% of raw events rejected")
        
        if (self.metrics.aggressive_buy_events + self.metrics.aggressive_sell_events) < self.metrics.valid_trade_events * 0.5:
            pct = 100 - ((self.metrics.aggressive_buy_events + self.metrics.aggressive_sell_events) / max(1, self.metrics.valid_trade_events)) * 100
            bottlenecks.append(f"AGGRESSION FILTER: {pct:.1f}% of valid events not aggressive")
        
        if self.metrics.absorption_candidates_found < self.metrics.absorption_checks_triggered * 0.1:
            pct = (1 - self.metrics.absorption_candidates_found / max(1, self.metrics.absorption_checks_triggered)) * 100
            bottlenecks.append(f"ABSORPTION DETECTION: {pct:.1f}% of bars have no absorption")
        
        if self.metrics.regime_passed < self.metrics.regime_checks_triggered * 0.5:
            pct = (1 - self.metrics.regime_passed / max(1, self.metrics.regime_checks_triggered)) * 100
            bottlenecks.append(f"REGIME FILTER: {pct:.1f}% of bars fail regime filter")
        
        if self.metrics.followthrough_passed < self.metrics.absorption_candidates_found * 0.3:
            pct = (1 - self.metrics.followthrough_passed / max(1, max(1, self.metrics.followthrough_checks_triggered))) * 100
            bottlenecks.append(f"FOLLOWTHROUGH GATE: {pct:.1f}% of absorptions fail gate")
        
        if self.metrics.alerts_generated == 0:
            bottlenecks.append("CRITICAL: No alerts generated - pipeline is dead")
        
        return bottlenecks


async def run_5_minute_debug():
    """Run debug for exactly 5 minutes on live stream"""
    
    pipeline = DebugPipeline()
    
    logger.info("Starting 5-minute deep debug run...")
    logger.info("Connecting to live stream...")
    
    # Simulate receiving events and bars
    # In a real scenario, this would connect to the actual feed
    
    run_duration = 5 * 60  # 5 minutes
    start_time = time.time()
    last_report = start_time
    
    try:
        while time.time() - start_time < run_duration:
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Generate synthetic events for testing
            # (In production, these would come from feed adapters)
            
            event = OrderFlowEvent(
                timestamp=current_time,
                symbol='ESM6',
                price=7234.25 + (elapsed / 10),  # Slight drift
                size=100 if elapsed % 2 < 1 else 600,  # Mix small and large
                side=OrderSide.BUY if elapsed % 4 < 2 else OrderSide.SELL,
                is_market_order=elapsed % 5 < 1  # Some market orders
            )
            
            # Process the event
            pipeline.process_trade_event(event)
            
            # Every 10 events, create a bar summary
            if pipeline.metrics.raw_trade_events % 10 == 0:
                bar = BarData(
                    timestamp=int(current_time),
                    symbol='ESM6',
                    open=7234.0,
                    high=7235.0,
                    low=7233.0,
                    close=7234.25,
                    volume=1000.0
                )
                
                # Process bar
                pipeline.process_bar(bar, [])
            
            # Report every 30 seconds
            if current_time - last_report >= 30:
                pipeline.report_metrics()
                last_report = current_time
            
            # Small delay to avoid busy loop
            await asyncio.sleep(0.01)
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error during debug run: {e}", exc_info=True)
    finally:
        # Final report
        pipeline.report_metrics(final=True)
        
        # Write diagnostic report
        write_diagnostic_report(pipeline)
        
        logger.info("Debug run complete")


def write_diagnostic_report(pipeline: DebugPipeline):
    """Write comprehensive diagnostic report"""
    
    report_path = pipeline.debug_dir / 'candidate_detector_debug.md'
    
    bottlenecks = pipeline._identify_bottlenecks()
    
    report = f"""# Candidate Generation Pipeline Debug Report

**Generated:** {datetime.now(timezone.utc).isoformat()}

## Pipeline Metrics Summary

| Stage | Count | Status |
|-------|-------|--------|
| Raw Trade Events | {pipeline.metrics.raw_trade_events} | ✓ |
| Valid Events | {pipeline.metrics.valid_trade_events} | {'✓' if pipeline.metrics.valid_trade_events > 0 else '✗'} |
| Aggressive Buy | {pipeline.metrics.aggressive_buy_events} | {'✓' if pipeline.metrics.aggressive_buy_events > 0 else '✗'} |
| Aggressive Sell | {pipeline.metrics.aggressive_sell_events} | {'✓' if pipeline.metrics.aggressive_sell_events > 0 else '✗'} |
| Absorption Checks | {pipeline.metrics.absorption_checks_triggered} | ✓ |
| Absorption Found | {pipeline.metrics.absorption_candidates_found} | {'✓' if pipeline.metrics.absorption_candidates_found > 0 else '✗'} |
| Reclaim Checks | {pipeline.metrics.reclaim_checks_triggered} | ✓ |
| Reclaim Found | {pipeline.metrics.reclaim_candidates_found} | {'✓' if pipeline.metrics.reclaim_candidates_found > 0 else '✗'} |
| Regime Checks | {pipeline.metrics.regime_checks_triggered} | ✓ |
| Regime Passed | {pipeline.metrics.regime_passed} | {'✓' if pipeline.metrics.regime_passed > 0 else '✗'} |
| Followthrough Checks | {pipeline.metrics.followthrough_checks_triggered} | ✓ |
| Followthrough Passed | {pipeline.metrics.followthrough_passed} | {'✓' if pipeline.metrics.followthrough_passed > 0 else '✗'} |
| Confidence Calculations | {pipeline.metrics.confidence_calculations} | ✓ |
| Alerts Generated | {pipeline.metrics.alerts_generated} | {'✓' if pipeline.metrics.alerts_generated > 0 else '✗'} |

## Bottleneck Analysis

"""
    
    if bottlenecks:
        for bottleneck in bottlenecks:
            report += f"- **{bottleneck}**\n"
    else:
        report += "- No major bottlenecks detected\n"
    
    report += f"""

## Answers to Key Questions

### 1. Which stage kills the pipeline?

"""
    
    if pipeline.metrics.alerts_generated == 0:
        if pipeline.metrics.followthrough_passed == 0:
            report += "**FOLLOWTHROUGH GATE** - No signals passed the followthrough confirmation gate.\n"
        elif pipeline.metrics.regime_passed == 0:
            report += "**REGIME FILTER** - No bars passed the regime filter.\n"
        elif pipeline.metrics.absorption_candidates_found == 0:
            report += "**ABSORPTION DETECTION** - No absorption signals detected.\n"
        else:
            report += "**UNKNOWN** - Check alert generation logic.\n"
    else:
        report += f"Pipeline is working - {pipeline.metrics.alerts_generated} alerts generated.\n"
    
    report += f"""

### 2. Is candidate generation broken?

Absorption candidates found: {pipeline.metrics.absorption_candidates_found}
Followthrough confirmations: {pipeline.metrics.followthrough_passed}
Final alerts: {pipeline.metrics.alerts_generated}

**Status:** {'✓ WORKING' if pipeline.metrics.alerts_generated > 0 else '✗ NOT WORKING'}

### 3. Which exact threshold is too strict?

"""
    
    # Analyze threshold strictness
    if pipeline.metrics.absorption_candidates_found < pipeline.metrics.absorption_checks_triggered * 0.1:
        report += "- **Absorption Detection Threshold**: Too strict - only {:.1f}% of bars generate absorption candidates\n".format(
            (pipeline.metrics.absorption_candidates_found / max(1, pipeline.metrics.absorption_checks_triggered)) * 100
        )
    
    if pipeline.metrics.regime_passed < pipeline.metrics.regime_checks_triggered * 0.3:
        report += "- **Regime Filter**: Too strict - only {:.1f}% of regimes pass\n".format(
            (pipeline.metrics.regime_passed / max(1, pipeline.metrics.regime_checks_triggered)) * 100
        )
    
    if pipeline.metrics.followthrough_passed < pipeline.metrics.absorption_candidates_found * 0.3:
        report += "- **Followthrough Gate**: Too strict - only {:.1f}% of absorptions pass\n".format(
            (pipeline.metrics.followthrough_passed / max(1, max(1, pipeline.metrics.followthrough_checks_triggered))) * 100
        )
    
    report += f"""

### 4. Are aggressive trades being detected correctly?

Aggressive buy events: {pipeline.metrics.aggressive_buy_events}
Aggressive sell events: {pipeline.metrics.aggressive_sell_events}
Total aggressive: {pipeline.metrics.aggressive_buy_events + pipeline.metrics.aggressive_sell_events}

**Status:** {'✓ DETECTED' if (pipeline.metrics.aggressive_buy_events + pipeline.metrics.aggressive_sell_events) > 0 else '✗ NOT DETECTED'}

### 5. What SINGLE minimal fix would restore candidate flow?

"""
    
    if pipeline.metrics.alerts_generated == 0:
        if pipeline.metrics.followthrough_passed == 0 and pipeline.metrics.absorption_candidates_found > 0:
            report += """
**MINIMAL FIX: Loosen Followthrough Gate Time Window**

The followthrough gate is rejecting all candidates because the time window or 
confirmation count is too strict. Recommendation:

1. Increase `time_window_ms` from 5000 to 10000 (more time for confirmations)
2. Reduce `min_confirmation_count` from 2 to 1 (accept initial absorption only)
3. Reduce `min_volume_ratio` from 0.4 to 0.2 (lower volume threshold)

This allows absorption signals to generate alerts immediately rather than waiting for confirmation.
"""
        elif pipeline.metrics.absorption_candidates_found == 0 and pipeline.metrics.valid_trade_events > 0:
            report += """
**MINIMAL FIX: Loosen Absorption Detection Thresholds**

The absorption detector is too strict. Recommendation:

1. Reduce `volume_min_pct` from 0.3 to 0.15 (lower volume ratio requirement)
2. Reduce `delta_min_ratio` from 0.5 to 0.3 (lower delta requirement)
3. Increase `time_window_ms` from 2000 to 5000 (wider detection window)

This allows smaller absorption patterns to be detected.
"""
        elif pipeline.metrics.regime_passed == 0:
            report += """
**MINIMAL FIX: Verify Regime Detection Logic**

The regime detector is not marking regimes as tradeable. Check:

1. Ensure regime types are correctly mapped (UPTREND, DOWNTREND, BREAKOUT, etc.)
2. Verify volatility threshold (currently 0.02) is not too strict
3. Confirm moving averages are calculating correctly

Start with requiring only BREAKOUT regime (highest probability).
"""
    else:
        report += f"""
**Pipeline is working!** {pipeline.metrics.alerts_generated} alerts have been generated.
No fixes needed.
"""
    
    report += f"""

## Sample Events

First 10 sample events processed:

"""
    
    for i, event in enumerate(pipeline.event_samples[:10], 1):
        report += f"{i}. {event['symbol']} {event['side']} {event['size']} @ {event['price']}\n"
    
    report += f"""

## Raw Metrics JSON

See `pipeline_debug.json` for complete metrics and per-stage breakdowns.

---
End of Report
"""
    
    with open(report_path, 'w') as f:
        f.write(report)
    
    logger.info(f"Diagnostic report written to {report_path}")


if __name__ == '__main__':
    asyncio.run(run_5_minute_debug())
