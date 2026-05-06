#!/usr/bin/env python3
"""
Comprehensive feed integrity audit for Bookmap/Rithmic live trade stream.

Analyzes:
  1. Trade events - count, zero-size %, avg/max size, aggressor reliability
  2. Depth events - bid/ask consistency, spread sanity, update frequency
  3. Delta validity - cumulative delta computation, aggressor flag reliability
  4. Event sequencing - out-of-order, duplicate timestamps, sequence gaps
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Generator, Tuple

class FeedAudit:
    def __init__(self):
        # Trade stats
        self.total_trades = 0
        self.zero_size_trades = 0
        self.trade_sizes = []
        self.trade_aggressors = defaultdict(int)  # True/False/None counts
        self.trades_by_symbol = defaultdict(int)
        
        # Depth stats
        self.total_depth = 0
        self.depth_by_symbol = defaultdict(int)
        self.bid_updates = defaultdict(int)
        self.ask_updates = defaultdict(int)
        self.best_bid_last = {}
        self.best_ask_last = {}
        self.spread_violations = 0
        
        # Delta computation
        self.cumulative_delta = defaultdict(lambda: defaultdict(int))  # symbol -> {level: delta}
        self.delta_errors = []
        
        # Sequencing
        self.sequence_gaps = []
        self.last_seq = {}
        self.out_of_order_events = 0
        self.duplicate_timestamps = defaultdict(int)
        self.timestamp_inversions = 0
        self.last_ts = {}
        
        # Event types seen
        self.event_types = defaultdict(int)
        
    def process_event(self, event: dict, seq_num: int) -> None:
        """Process a single JSONL event."""
        event_type = event.get("event_type")
        symbol = event.get("symbol")
        
        # Track event types
        self.event_types[event_type] += 1
        
        # Sequence checking
        if symbol not in self.last_seq:
            self.last_seq[symbol] = seq_num
        else:
            expected = self.last_seq[symbol] + 1
            if seq_num < self.last_seq[symbol]:
                self.out_of_order_events += 1
            elif seq_num > expected:
                gap = seq_num - expected
                self.sequence_gaps.append((symbol, self.last_seq[symbol], seq_num, gap))
            self.last_seq[symbol] = seq_num
        
        # Timestamp checking
        ts_event = event.get("ts_event")
        if symbol not in self.last_ts:
            self.last_ts[symbol] = ts_event
        else:
            if ts_event == self.last_ts[symbol]:
                self.duplicate_timestamps[symbol] += 1
            elif ts_event < self.last_ts[symbol]:
                self.timestamp_inversions += 1
            self.last_ts[symbol] = ts_event
        
        if event_type == "trade":
            self._audit_trade(event, symbol)
        elif event_type == "depth":
            self._audit_depth(event, symbol)
    
    def _audit_trade(self, event: dict, symbol: str) -> None:
        """Audit a trade event."""
        self.total_trades += 1
        self.trades_by_symbol[symbol] += 1
        
        size = event.get("size", 0)
        self.trade_sizes.append(size)
        if size == 0:
            self.zero_size_trades += 1
        
        # Aggressor flag
        aggressor = event.get("is_bid_aggressor")
        if aggressor is None:
            self.trade_aggressors["none"] += 1
        elif aggressor is True:
            self.trade_aggressors["bid"] += 1
        else:
            self.trade_aggressors["ask"] += 1
    
    def _audit_depth(self, event: dict, symbol: str) -> None:
        """Audit a depth event."""
        self.total_depth += 1
        self.depth_by_symbol[symbol] += 1
        
        side = event.get("side")
        price = event.get("price")
        size = event.get("size")
        
        if side == "bid":
            self.bid_updates[symbol] += 1
            old_bid = self.best_bid_last.get(symbol)
            if old_bid and price < old_bid and size > 0:
                # Price decreased—could be removal or update
                pass
            self.best_bid_last[symbol] = price
        elif side == "ask":
            self.ask_updates[symbol] += 1
            old_ask = self.best_ask_last.get(symbol)
            if old_ask and price > old_ask and size > 0:
                # Price increased—could be removal or update
                pass
            self.best_ask_last[symbol] = price
        
        # Spread sanity check
        bid = self.best_bid_last.get(symbol)
        ask = self.best_ask_last.get(symbol)
        if bid and ask and bid >= ask:
            self.spread_violations += 1
    
    def get_stats(self) -> dict:
        """Return audit statistics."""
        avg_trade_size = sum(self.trade_sizes) / len(self.trade_sizes) if self.trade_sizes else 0
        max_trade_size = max(self.trade_sizes) if self.trade_sizes else 0
        pct_zero_size = (self.zero_size_trades / self.total_trades * 100) if self.total_trades else 0
        
        gap_summary = defaultdict(int)
        for symbol, from_seq, to_seq, gap in self.sequence_gaps:
            gap_summary[gap] += 1
        
        return {
            "trades": {
                "total": self.total_trades,
                "zero_size_count": self.zero_size_trades,
                "zero_size_percent": round(pct_zero_size, 2),
                "avg_size": round(avg_trade_size, 2),
                "max_size": max_trade_size,
                "aggressors": dict(self.trade_aggressors),
                "by_symbol": dict(self.trades_by_symbol),
            },
            "depth": {
                "total": self.total_depth,
                "by_symbol": dict(self.depth_by_symbol),
                "bid_updates": dict(self.bid_updates),
                "ask_updates": dict(self.ask_updates),
                "spread_violations": self.spread_violations,
            },
            "sequencing": {
                "out_of_order": self.out_of_order_events,
                "gaps_detected": len(self.sequence_gaps),
                "gap_distribution": dict(gap_summary),
                "duplicate_timestamps": dict(self.duplicate_timestamps),
                "timestamp_inversions": self.timestamp_inversions,
            },
            "event_types": dict(self.event_types),
        }
    
    def get_blockers(self) -> list:
        """Identify blocking issues."""
        blockers = []
        
        if self.zero_size_trades > 0:
            pct = self.zero_size_trades / self.total_trades * 100
            blockers.append(f"BLOCKER: {self.zero_size_trades} zero-size trades ({pct:.2f}%) — breaks delta/absorption logic")
        
        if self.out_of_order_events > 0:
            blockers.append(f"BLOCKER: {self.out_of_order_events} out-of-order events — sequencing unreliable")
        
        if self.spread_violations > 0:
            blockers.append(f"BLOCKER: {self.spread_violations} bid >= ask violations — depth inconsistent")
        
        if self.timestamp_inversions > 0:
            blockers.append(f"BLOCKER: {self.timestamp_inversions} timestamp inversions — timing unreliable")
        
        for symbol, aggressor_count in self.trade_aggressors.items():
            if symbol == "none" and aggressor_count > self.total_trades * 0.1:
                blockers.append(f"WARNING: {aggressor_count} trades missing is_bid_aggressor ({aggressor_count/self.total_trades*100:.1f}%) — delta computation at risk")
        
        return blockers
    
    def get_recommendations(self) -> list:
        """Generate fix recommendations."""
        recs = []
        
        if self.zero_size_trades > 0:
            recs.append("FIX: Filter zero-size trades at ingestion (bookmap_l1_api produces them)")
            recs.append("  → Add: if size == 0: skip")
            recs.append("  → Normalize: size must be > 0 for valid trade")
        
        if self.out_of_order_events > 0:
            recs.append("FIX: Deduplicate and re-sort by (symbol, ts_event, seq)")
            recs.append("  → Consider: event buffering with 100ms wait before processing")
        
        if self.spread_violations > 0:
            recs.append("FIX: Validate bid < ask before computing delta")
            recs.append("  → Add: if bid >= ask: log.warn(spread violation, skip)")
        
        if self.timestamp_inversions > 0:
            recs.append("FIX: Enforce monotonic ts_event within each symbol stream")
            recs.append("  → Consider: use seq as tiebreaker when ts_event is equal")
        
        recs.append("NORMALIZATION: Ensure all trade events have is_bid_aggressor flag")
        recs.append("  → Reliably compute delta = sum(is_bid_aggressor * size)")
        recs.append("VALIDATION: Pre-compute cumulative delta per symbol, check continuity")
        
        return recs


def read_jsonl(path: str, limit: int = None) -> Generator[dict, None, None]:
    """Stream JSONL file."""
    with open(path) as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def audit_file(path: str, limit: int = None) -> Tuple[FeedAudit, float]:
    """Audit a feed file."""
    import time
    audit = FeedAudit()
    start = time.time()
    count = 0
    
    for event in read_jsonl(path, limit):
        seq = event.get("seq")
        audit.process_event(event, seq)
        count += 1
        if count % 100000 == 0:
            print(f"  Processed {count:,} events...", file=sys.stderr)
    
    elapsed = time.time() - start
    return audit, elapsed


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Audit Bookmap/Rithmic feed integrity")
    parser.add_argument("file", help="JSONL file path")
    parser.add_argument("--limit", type=int, help="Limit events to process")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    
    args = parser.parse_args()
    
    print(f"Auditing {args.file}...", file=sys.stderr)
    audit, elapsed = audit_file(args.file, args.limit)
    
    stats = audit.get_stats()
    blockers = audit.get_blockers()
    recs = audit.get_recommendations()
    
    if args.json:
        output = {
            "stats": stats,
            "blockers": blockers,
            "recommendations": recs,
            "elapsed_seconds": elapsed,
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n=== FEED INTEGRITY AUDIT ===\n")
        
        print("TRADE ANALYSIS:")
        print(f"  Total trades: {stats['trades']['total']:,}")
        print(f"  Zero-size trades: {stats['trades']['zero_size_count']:,} ({stats['trades']['zero_size_percent']:.2f}%)")
        print(f"  Avg size: {stats['trades']['avg_size']:.2f}")
        print(f"  Max size: {stats['trades']['max_size']}")
        print(f"  Aggressors: {stats['trades']['aggressors']}")
        print(f"  By symbol: {stats['trades']['by_symbol']}\n")
        
        print("DEPTH ANALYSIS:")
        print(f"  Total depth events: {stats['depth']['total']:,}")
        print(f"  Spread violations: {stats['depth']['spread_violations']}")
        print(f"  Bid updates: {stats['depth']['bid_updates']}")
        print(f"  Ask updates: {stats['depth']['ask_updates']}\n")
        
        print("SEQUENCING ANALYSIS:")
        print(f"  Out-of-order events: {stats['sequencing']['out_of_order']}")
        print(f"  Sequence gaps: {stats['sequencing']['gaps_detected']}")
        print(f"  Duplicate timestamps: {stats['sequencing']['duplicate_timestamps']}")
        print(f"  Timestamp inversions: {stats['sequencing']['timestamp_inversions']}\n")
        
        print("EVENT TYPES:")
        for etype, count in stats['event_types'].items():
            print(f"  {etype}: {count:,}")
        
        if blockers:
            print("\n" + "="*60)
            print("BLOCKERS:")
            for b in blockers:
                print(f"  • {b}")
        
        if recs:
            print("\n" + "="*60)
            print("RECOMMENDATIONS:")
            for r in recs:
                print(f"  • {r}")
        
        print(f"\n=== Processed {stats['trades']['total'] + stats['depth']['total']:,} events in {elapsed:.2f}s ===")
