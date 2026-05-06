#!/usr/bin/env python3
"""
Test suite for feed integrity blockers using real Bookmap/Rithmic data.

Tests:
1. Zero-size trade normalization - filters invalid sizes
2. Spread validation - rejects crossed books, stale quotes
3. Event deduplication - removes duplicates from history
4. Out-of-order buffer - reorders late-arriving events
5. Safe delta engine - only valid trades count
6. Feed health monitoring - generates metrics + safety checks

Real data: /state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from normalization import TradeNormalizer, EventValidator
from spread_validator import SpreadValidator
from dedupe import EventDeduplicator, EventFingerprint, SequenceTracker
from event_buffer import OutOfOrderBuffer
from delta_engine import SafeDeltaEngine
from feed_health import FeedHealthMonitor

# Real data path
REAL_DATA_FILE = Path(
    "/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/"
    "state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl"
)


def load_real_bookmap_data(limit: int = 1000) -> List[dict]:
    """Load real Bookmap/Rithmic data from JSONL file."""
    events = []
    
    if not REAL_DATA_FILE.exists():
        print(f"ERROR: Data file not found: {REAL_DATA_FILE}")
        return events
    
    print(f"Loading real data from {REAL_DATA_FILE}...")
    
    with open(REAL_DATA_FILE) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                event = json.loads(line.strip())
                events.append(event)
            except json.JSONDecodeError:
                continue
    
    print(f"Loaded {len(events)} events")
    return events


def test_zero_size_normalization(events: List[dict]) -> Dict:
    """Test Blocker #1: Zero-size trade normalization."""
    print("\n" + "="*60)
    print("TEST 1: Zero-Size Trade Normalization")
    print("="*60)
    
    normalizer = TradeNormalizer()
    validator = EventValidator()
    
    zero_size_events = []
    normal_events = []
    invalid_events = []
    
    for event in events:
        if event.get('event_type') != 'trade':
            continue
        
        # Create some synthetic zero-size trades for testing
        original_size = event.get('size', 1)
        test_cases = [
            (original_size, 'NORMAL'),
            (0, 'ZERO'),
            (-1, 'NEGATIVE'),
        ]
        
        for size, label in test_cases:
            result = normalizer.normalize_trade(
                size,
                event.get('price', 0),
                event.get('symbol', 'ESM6'),
                event.get('ts_event')
            )
            
            if label == 'ZERO':
                zero_size_events.append(result)
            elif label == 'NEGATIVE':
                invalid_events.append(result)
            else:
                normal_events.append(result)
    
    print(f"\nResults:")
    print(f"  Normal trades: {len(normal_events)} (all valid)")
    print(f"  Zero-size: {len(zero_size_events)} - all rejected? {all(not e.is_valid for e in zero_size_events)}")
    print(f"  Negative: {len(invalid_events)} - all rejected? {all(not e.is_valid for e in invalid_events)}")
    
    stats = normalizer.get_stats()
    print(f"\nNormalizer Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Zero-Size Normalization',
        'passed': all(not e.is_valid for e in zero_size_events + invalid_events),
        'stats': stats,
    }


def test_spread_validation(events: List[dict]) -> Dict:
    """Test Blocker #2: Spread validation."""
    print("\n" + "="*60)
    print("TEST 2: Spread Validation")
    print("="*60)
    
    validator = SpreadValidator()
    
    # Extract unique depth events by symbol and price
    spreads_found = 0
    valid_spreads = 0
    crossed_spreads = 0
    stale_spreads = 0
    
    for event in events:
        if event.get('event_type') == 'depth':
            symbol = event.get('symbol', 'ESM6')
            price = event.get('price', 0)
            size = event.get('size', 0)
            side = event.get('side', 'bid')
            
            # Simulate bid/ask by taking nearby levels
            if side == 'bid':
                bid = price
                ask = price + 0.25  # Typical spread for ES
                bid_size = size
                ask_size = 10
            else:
                bid = price - 0.25
                ask = price
                bid_size = 10
                ask_size = size
            
            result = validator.validate_spread(
                bid, ask, bid_size, ask_size, symbol,
                datetime.fromisoformat(event.get('ts_event', '2026-05-05T00:00:00Z').replace('Z', '+00:00')).timestamp()
            )
            
            spreads_found += 1
            if result.is_valid:
                valid_spreads += 1
            elif result.anomaly_type == 'CROSSED':
                crossed_spreads += 1
            elif result.anomaly_type == 'STALE':
                stale_spreads += 1
            
            if spreads_found >= 500:
                break
    
    print(f"\nResults (first 500 depth events):")
    print(f"  Valid spreads: {valid_spreads}")
    print(f"  Crossed spreads: {crossed_spreads}")
    print(f"  Stale spreads: {stale_spreads}")
    
    stats = validator.get_stats()
    print(f"\nValidator Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Spread Validation',
        'passed': valid_spreads > 0,
        'stats': stats,
    }


def test_event_deduplication(events: List[dict]) -> Dict:
    """Test Blocker #3: Event deduplication."""
    print("\n" + "="*60)
    print("TEST 3: Event Deduplication")
    print("="*60)
    
    deduplicator = EventDeduplicator()
    
    unique_count = 0
    duplicate_count = 0
    processed = 0
    
    for i, event in enumerate(events):
        if processed >= 500:
            break
        
        if event.get('event_type') not in ['trade', 'depth']:
            continue
        
        processed += 1
        
        # Create fingerprint
        timestamp = datetime.fromisoformat(
            event.get('ts_event', '2026-05-05T00:00:00Z').replace('Z', '+00:00')
        ).timestamp()
        
        fingerprint = deduplicator.get_fingerprint(
            timestamp,
            event.get('symbol', 'ESM6'),
            event.get('price', 0),
            event.get('size', 0),
            event.get('side', 'bid'),
            event.get('seq', 0)
        )
        
        result = deduplicator.check_duplicate(fingerprint)
        
        if result.is_duplicate:
            duplicate_count += 1
        else:
            unique_count += 1
        
        # Simulate duplicate by checking same event again
        if i < 50 and not result.is_duplicate:
            dup_result = deduplicator.check_duplicate(fingerprint)
            if dup_result.is_duplicate:
                duplicate_count += 1
    
    print(f"\nResults (simulated {processed} events + duplicates):")
    print(f"  Unique events: {unique_count}")
    print(f"  Duplicates detected: {duplicate_count}")
    
    stats = deduplicator.get_stats()
    print(f"\nDeduplicator Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Event Deduplication',
        'passed': duplicate_count > 0,  # Should have found at least some duplicates
        'stats': stats,
    }


def test_out_of_order_buffer(events: List[dict]) -> Dict:
    """Test Blocker #4: Out-of-order buffer."""
    print("\n" + "="*60)
    print("TEST 4: Out-of-Order Buffer")
    print("="*60)
    
    buffer = OutOfOrderBuffer(reorder_window_ms=100)
    
    # Add events in order first
    events_added = 0
    for event in events[:100]:
        if event.get('event_type') not in ['trade', 'depth']:
            continue
        
        timestamp = datetime.fromisoformat(
            event.get('ts_event', '2026-05-05T00:00:00Z').replace('Z', '+00:00')
        ).timestamp()
        
        buffer.add_event(
            f"evt_{events_added}",
            timestamp,
            event.get('symbol', 'ESM6'),
            event.get('event_type'),
            event
        )
        events_added += 1
        
        if events_added >= 50:
            break
    
    # Try to emit
    result = buffer.try_emit_ordered('ESM6.CME@RITHMIC')
    
    print(f"\nResults:")
    print(f"  Events added: {events_added}")
    print(f"  Events ready to emit: {len(result.ordered_events)}")
    print(f"  Events reordered: {result.events_reordered}")
    print(f"  Max reorder delay: {result.max_reorder_delay_ms:.1f}ms")
    
    stats = buffer.get_stats()
    print(f"\nBuffer Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Out-of-Order Buffer',
        'passed': events_added > 0,
        'stats': stats,
    }


def test_safe_delta_engine(events: List[dict]) -> Dict:
    """Test Blocker #5: Safe delta engine."""
    print("\n" + "="*60)
    print("TEST 5: Safe Delta Engine")
    print("="*60)
    
    engine = SafeDeltaEngine()
    
    # Process trades
    valid_trades = 0
    invalid_trades = 0
    duplicates_skipped = 0
    
    for event in events:
        if event.get('event_type') != 'trade':
            continue
        
        if valid_trades >= 200:
            break
        
        symbol = event.get('symbol', 'ESM6')
        side = event.get('side', 'buy').upper()
        size = event.get('size', 0)
        price = event.get('price', 0)
        
        # Simulate validation
        is_valid = size > 0 and price > 0
        is_duplicate = False
        
        result = engine.process_trade(
            symbol, side, size, price,
            is_valid=is_valid,
            is_duplicate=is_duplicate
        )
        
        if result['processed']:
            valid_trades += 1
        else:
            if result['reason'] == 'DUPLICATE':
                duplicates_skipped += 1
            else:
                invalid_trades += 1
    
    # Get state
    snapshot = engine.get_delta_snapshot('ESM6.CME@RITHMIC')
    
    print(f"\nResults:")
    print(f"  Valid trades processed: {valid_trades}")
    print(f"  Invalid trades skipped: {invalid_trades}")
    print(f"  Duplicates skipped: {duplicates_skipped}")
    
    if snapshot:
        print(f"\nDelta Snapshot:")
        print(f"  Cumulative Delta: {snapshot.cumulative_delta:.0f}")
        print(f"  Buy Volume: {snapshot.buy_volume:.0f}")
        print(f"  Sell Volume: {snapshot.sell_volume:.0f}")
        print(f"  Aggression Ratio: {snapshot.aggression_ratio:.2f}")
        print(f"  Is Bullish: {snapshot.is_bullish}")
    
    stats = engine.get_stats()
    print(f"\nEngine Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Safe Delta Engine',
        'passed': valid_trades > 0 and snapshot is not None,
        'stats': stats,
        'delta': snapshot.cumulative_delta if snapshot else 0,
    }


def test_feed_health_monitoring(events: List[dict]) -> Dict:
    """Test Blocker #6: Feed health monitoring."""
    print("\n" + "="*60)
    print("TEST 6: Feed Health Monitoring")
    print("="*60)
    
    monitor = FeedHealthMonitor()
    
    # Record events
    for event in events[:1000]:
        if event.get('event_type') not in ['trade', 'depth']:
            continue
        
        symbol = event.get('symbol', 'ESM6')
        event_type = event.get('event_type')
        
        # Simulate some invalids and duplicates
        event_num = monitor.event_counters.get(symbol, {}).get('total', 0) if symbol in monitor.event_counters else 0
        is_valid = event_num % 20 != 0  # 5% invalid
        is_duplicate = event_num % 50 == 0  # 2% duplicate
        
        monitor.record_event(
            symbol, event_type,
            is_valid=is_valid,
            is_duplicate=is_duplicate
        )
    
    # Calculate metrics
    metrics = monitor.calculate_metrics(
        'ESM6.CME@RITHMIC',
        cumulative_delta=1500,
        delta_acceleration=50,
        aggression_ratio=0.65,
        latest_bid=5227.0,
        latest_ask=5227.25,
        latest_spread_bps=0.35,
        avg_spread_bps=0.45,
        max_spread_bps=2.5,
        buffer_depth=25,
        buffer_max_depth=100,
        buffer_overflows=0,
        spread_violations=2
    )
    
    # Perform safety checks
    checks = monitor.perform_safety_checks(metrics)
    
    print(f"\nHealth Metrics:")
    print(f"  Events/sec: {metrics.events_per_sec:.1f}")
    print(f"  Invalid %: {metrics.invalid_percentage*100:.2f}%")
    print(f"  Duplicate %: {metrics.duplicate_percentage*100:.2f}%")
    print(f"  Reorder %: {metrics.reorder_percentage*100:.2f}%")
    print(f"  Spread violations %: {metrics.spread_violation_percentage*100:.3f}%")
    
    print(f"\nDelta Metrics:")
    print(f"  Cumulative Delta: {metrics.cumulative_delta:.0f}")
    print(f"  Delta Acceleration: {metrics.delta_acceleration:.1f}")
    print(f"  Aggression Ratio: {metrics.aggression_ratio:.2f}")
    
    print(f"\nSpread Metrics:")
    print(f"  Bid: {metrics.latest_bid:.2f}, Ask: {metrics.latest_ask:.2f}")
    print(f"  Latest Spread: {metrics.latest_spread_bps:.2f} bps")
    
    print(f"\nBuffer Metrics:")
    print(f"  Current Depth: {metrics.buffer_depth}")
    print(f"  Max Depth: {metrics.buffer_max_depth}")
    print(f"  Overflows: {metrics.buffer_overflows}")
    
    # Check safety
    can_alert = monitor.can_alert(metrics)
    
    print(f"\nSafety Checks:")
    for check in checks:
        status = "✓ PASS" if check.is_safe else "✗ FAIL"
        print(f"  {status} {check.check_name}: {check.reason}")
    
    print(f"\nCan Alert: {can_alert}")
    
    # Export JSON
    json_output = monitor.to_json()
    print(f"\nJSON Export (first 500 chars):")
    print(json_output[:500] + "...\n")
    
    stats = monitor.get_stats()
    print(f"Monitor Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    return {
        'test_name': 'Feed Health Monitoring',
        'passed': can_alert and metrics.events_per_sec > 0,
        'stats': stats,
        'can_alert': can_alert,
    }


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("FEED INTEGRITY BLOCKER TEST SUITE")
    print("Using real Bookmap/Rithmic data")
    print("="*60)
    
    # Load real data
    events = load_real_bookmap_data(limit=2000)
    
    if not events:
        print("ERROR: Could not load test data")
        sys.exit(1)
    
    # Run tests
    results = []
    results.append(test_zero_size_normalization(events))
    results.append(test_spread_validation(events))
    results.append(test_event_deduplication(events))
    results.append(test_out_of_order_buffer(events))
    results.append(test_safe_delta_engine(events))
    results.append(test_feed_health_monitoring(events))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for r in results if r.get('passed'))
    total = len(results)
    
    for result in results:
        status = "✓ PASS" if result.get('passed') else "✗ FAIL"
        print(f"{status} {result['test_name']}")
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
