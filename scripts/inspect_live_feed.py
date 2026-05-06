#!/usr/bin/env python3
"""
Live feed inspection: tail JSONL for 60 seconds and report metrics.

Prints:
  - trades/sec, depth updates/sec
  - % zero-size trades
  - Top symbols
  - Spread stats (min/max/avg)
  - Cumulative delta (live per symbol)
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path


class LiveInspector:
    def __init__(self, duration_sec=60):
        self.duration = duration_sec
        self.start_time = None
        self.end_time = None
        
        # Counters
        self.trades = 0
        self.depth = 0
        self.zero_size = 0
        
        # Per-symbol tracking
        self.symbols = defaultdict(lambda: {
            "trades": 0,
            "depth": 0,
            "last_bid": None,
            "last_ask": None,
            "spreads": [],
            "delta": 0,
            "aggressor_up": 0,
            "aggressor_down": 0,
        })
        
    def process_event(self, event: dict):
        """Process one event."""
        event_type = event.get("event_type")
        symbol = event.get("symbol")
        
        if event_type == "trade":
            self.trades += 1
            size = event.get("size", 0)
            if size == 0:
                self.zero_size += 1
            
            self.symbols[symbol]["trades"] += 1
            
            # Track delta
            is_bid_agg = event.get("is_bid_aggressor")
            if is_bid_agg is True:
                self.symbols[symbol]["aggressor_up"] += size
                self.symbols[symbol]["delta"] += size
            elif is_bid_agg is False:
                self.symbols[symbol]["aggressor_down"] += size
                self.symbols[symbol]["delta"] -= size
        
        elif event_type == "depth":
            self.depth += 1
            side = event.get("side")
            price = event.get("price")
            
            self.symbols[symbol]["depth"] += 1
            
            if side == "bid":
                self.symbols[symbol]["last_bid"] = price
            elif side == "ask":
                self.symbols[symbol]["last_ask"] = price
            
            # Track spread
            bid = self.symbols[symbol]["last_bid"]
            ask = self.symbols[symbol]["last_ask"]
            if bid and ask and ask > bid:
                spread = ask - bid
                self.symbols[symbol]["spreads"].append(spread)
    
    def run(self, jsonl_path: str):
        """Tail JSONL file for duration_sec seconds."""
        print(f"Tailing {jsonl_path} for {self.duration}s...\n", file=sys.stderr)
        
        self.start_time = time.time()
        self.end_time = self.start_time + self.duration
        
        with open(jsonl_path) as f:
            # Seek to end (latest events)
            f.seek(0, 2)
            
            while time.time() < self.end_time:
                line = f.readline()
                if line:
                    try:
                        event = json.loads(line)
                        self.process_event(event)
                    except json.JSONDecodeError:
                        continue
                else:
                    # No new data; small sleep to avoid busy-polling
                    time.sleep(0.01)
        
        self.report()
    
    def report(self):
        """Print inspection report."""
        elapsed = self.end_time - self.start_time if self.end_time else self.duration
        
        trades_per_sec = self.trades / elapsed if elapsed else 0
        depth_per_sec = self.depth / elapsed if elapsed else 0
        pct_zero = (self.zero_size / self.trades * 100) if self.trades else 0
        
        print("\n" + "="*70)
        print("LIVE FEED INSPECTION (60 seconds)")
        print("="*70)
        
        print(f"\nTHROUGHPUT:")
        print(f"  Trades/sec:      {trades_per_sec:>10.1f}")
        print(f"  Depth updates/sec: {depth_per_sec:>7.1f}")
        print(f"  Total trades:    {self.trades:>10,}")
        print(f"  Total depth:     {self.depth:>10,}")
        
        print(f"\nQUALITY:")
        print(f"  Zero-size trades: {self.zero_size:>8,} ({pct_zero:.2f}%)")
        
        # Top symbols
        top_symbols = sorted(self.symbols.items(), key=lambda x: x[1]["trades"], reverse=True)[:5]
        
        print(f"\nTOP SYMBOLS:")
        for symbol, stats in top_symbols:
            print(f"  {symbol}:")
            print(f"    Trades: {stats['trades']}, Depth: {stats['depth']}")
            print(f"    Delta: {stats['delta']:+d} (up:{stats['aggressor_up']}, down:{stats['aggressor_down']})")
            
            bid = stats["last_bid"]
            ask = stats["last_ask"]
            if bid and ask:
                spread = ask - bid
                spreads = stats["spreads"]
                avg_spread = sum(spreads) / len(spreads) if spreads else 0
                min_spread = min(spreads) if spreads else 0
                max_spread = max(spreads) if spreads else 0
                print(f"    Bid: {bid}, Ask: {ask}, Spread: {spread:.4f}")
                print(f"    Spread stats: min={min_spread:.4f}, avg={avg_spread:.4f}, max={max_spread:.4f}")
            else:
                print(f"    Bid/Ask: incomplete")
        
        # Cumulative delta summary
        print(f"\nCUMULATIVE DELTA (all symbols):")
        for symbol, stats in top_symbols:
            print(f"  {symbol}: {stats['delta']:+d}")
        
        print("\n" + "="*70)
        print(f"Inspection complete. Elapsed: {elapsed:.1f}s")
        print("="*70 + "\n")
        
        # Safety verdict
        print("SAFETY VERDICT FOR DELTA/ABSORPTION/DISPLACEMENT/FOLLOW-THROUGH:")
        
        if pct_zero > 5:
            print(f"  ⚠️  UNSAFE: {pct_zero:.1f}% zero-size trades — delta computation unreliable")
            print("     Recommendation: Filter zero-size trades before delta logic")
        else:
            print(f"  ✓ Safe: {pct_zero:.2f}% zero-size trades (acceptable)")
        
        # Check for aggressors
        missing_agg = sum(1 for s in self.symbols.values() if s["trades"] > 0 and (s["aggressor_up"] + s["aggressor_down"]) == 0)
        if missing_agg > 0:
            print(f"  ⚠️  WARNING: {missing_agg} symbols with no aggressor data — delta may be partial")
        else:
            print("  ✓ Aggressor flags present and reliable")
        
        # Spread check
        spread_issues = sum(1 for s in self.symbols.values() if s["last_bid"] and s["last_ask"] and s["last_bid"] >= s["last_ask"])
        if spread_issues > 0:
            print(f"  ⚠️  WARNING: {spread_issues} symbols with bid >= ask violations")
        else:
            print("  ✓ Spread sanity maintained")
        
        # Overall
        if pct_zero <= 5 and missing_agg == 0 and spread_issues == 0:
            print("\n  ✅ OVERALL: Feed integrity acceptable for delta-based strategies")
        else:
            print("\n  ❌ OVERALL: Feed has issues; recommend fixing before production")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Inspect live JSONL feed")
    parser.add_argument("--file", default="/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl",
                       help="JSONL file to tail")
    parser.add_argument("--duration", type=int, default=60, help="Inspection duration (seconds)")
    
    args = parser.parse_args()
    
    inspector = LiveInspector(args.duration)
    inspector.run(args.file)
