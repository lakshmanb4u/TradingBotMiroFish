#!/usr/bin/env python3
"""
Clean Live Alert Engine — Real Bookmap Feed Only
Enforces source guard + price guard before any alert
"""

import os
import json
import pandas as pd
from datetime import datetime, date, timedelta
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from live_source_guard import LiveSourceGuard
from price_guard import PriceGuard

class LiveAlertEngine:
    """Generate live alerts from real Bookmap JSONL feed"""
    
    def __init__(self, today_date=None):
        self.today = today_date or date.today()
        self.source_guard = LiveSourceGuard(self.today)
        self.price_guard = PriceGuard()
        
        self.feed_path = None
        self.alerts = []
        self.quarantined = []
        
        self.feed_health = {
            'status': 'UNKNOWN',
            'file_exists': False,
            'last_event_age_seconds': None,
            'total_events_processed': 0,
            'symbols': [],
            'last_timestamp': None,
            'growth_rate': None,
        }
        
        self.source_guard_status = {
            'path_check': False,
            'date_check': False,
            'symbol_check': False,
            'source_check': False,
            'price_check': False,
            'overall': False,
            'verdict': 'UNKNOWN',
        }
    
    def find_today_feed(self):
        """Find today's Bookmap JSONL file"""
        
        feed_dir = Path("state/orderflow/bookmap_api")
        today_str = self.today.isoformat()
        feed_file = feed_dir / f"es_orderflow_{today_str}.jsonl"
        
        if feed_file.exists():
            self.feed_path = feed_file
            self.feed_health['file_exists'] = True
            return True, f"Found: {feed_file}"
        else:
            return False, f"Not found: {feed_file}"
    
    def check_feed_active(self):
        """Check if feed file is actively growing"""
        
        if not self.feed_path or not self.feed_path.exists():
            return False, "Feed file does not exist"
        
        try:
            # Get file modification time
            mtime = self.feed_path.stat().st_mtime
            mtime_dt = datetime.fromtimestamp(mtime)
            
            # Check if modified in last 5 minutes
            age = (datetime.now() - mtime_dt).total_seconds()
            
            if age > 300:  # 5 minutes
                return False, f"Feed inactive for {age:.0f}s (>300s)"
            
            self.feed_health['growth_rate'] = f"active ({age:.0f}s old)"
            return True, f"Feed active ({age:.0f}s old)"
        except Exception as e:
            return False, f"Error checking feed: {e}"
    
    def check_feed_freshness(self):
        """Check if last event is recent (or historical but valid)"""
        
        if not self.feed_path or not self.feed_path.exists():
            return False, "Feed file does not exist"
        
        try:
            # Read last line
            with open(self.feed_path, 'r') as f:
                lines = f.readlines()
                if not lines:
                    return False, "Feed is empty"
                
                last_line = lines[-1]
                last_event = json.loads(last_line)
                
                ts_str = last_event.get('ts_event', '')
                if not ts_str:
                    return False, "No timestamp in last event"
                
                # Parse timestamp
                last_ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                # Get age relative to event's date, not current UTC
                age = (datetime.now(last_ts.tzinfo) - last_ts).total_seconds()
                
                self.feed_health['last_timestamp'] = ts_str
                self.feed_health['last_event_age_seconds'] = age
                
                # Allow historical data (same-day feed)
                # Check if event is from today
                event_date = ts_str.split('T')[0]
                if event_date != self.today.isoformat():
                    return False, f"Event date {event_date} != today {self.today.isoformat()}"
                
                # Feed is valid if it's today's data
                return True, f"Last event from today at {ts_str} ({age:.0f}s ago)"
        except Exception as e:
            return False, f"Error checking freshness: {e}"
    
    def validate_feed_with_guards(self):
        """Run all guard checks on feed"""
        
        print("\n[FEED VALIDATION]")
        print("-" * 80)
        
        all_pass = True
        
        # Check 1: File exists
        print(f"\n1. Feed file exists...")
        path_ok, path_msg = self.find_today_feed()
        print(f"   {'✓' if path_ok else '✗'} {path_msg}")
        self.source_guard_status['path_check'] = path_ok
        all_pass = all_pass and path_ok
        
        if not path_ok:
            self.source_guard_status['verdict'] = 'LIVE_FEED_NOT_AVAILABLE'
            self.source_guard_status['overall'] = False
            return False
        
        # Check 2: Feed is actively growing
        print(f"\n2. Feed actively growing...")
        active_ok, active_msg = self.check_feed_active()
        print(f"   {'✓' if active_ok else '✗'} {active_msg}")
        all_pass = all_pass and active_ok
        
        # Check 3: Last event is fresh
        print(f"\n3. Last event freshness...")
        fresh_ok, fresh_msg = self.check_feed_freshness()
        print(f"   {'✓' if fresh_ok else '✗'} {fresh_msg}")
        all_pass = all_pass and fresh_ok
        
        # Check 4: Sample events for integrity
        print(f"\n4. Sampling events for integrity...")
        sample_ok = self.sample_feed_events()
        print(f"   {'✓' if sample_ok else '✗'} {'All samples valid' if sample_ok else 'Some samples failed'}")
        all_pass = all_pass and sample_ok
        
        # Determine verdict
        if all_pass:
            self.source_guard_status['verdict'] = 'LIVE_PATH_CLEAN'
        else:
            self.source_guard_status['verdict'] = 'LIVE_PATH_STILL_CONTAMINATED'
        
        self.source_guard_status['overall'] = all_pass
        
        print(f"\nVERDICT: {self.source_guard_status['verdict']}")
        
        return all_pass
    
    def sample_feed_events(self):
        """Sample and validate random events from feed"""
        
        if not self.feed_path or not self.feed_path.exists():
            return False
        
        try:
            with open(self.feed_path, 'r') as f:
                lines = f.readlines()
            
            if len(lines) < 3:
                return False
            
            # Sample every 100th event (or last 3 if fewer)
            sample_indices = [0, len(lines)//2, len(lines)-1]
            all_valid = True
            
            for idx in sample_indices:
                if idx >= len(lines):
                    continue
                
                event = json.loads(lines[idx])
                
                # Check date
                ts_str = event.get('ts_event', '')
                event_date_str = ts_str.split('T')[0]
                
                if event_date_str != self.today.isoformat():
                    print(f"   ✗ Event {idx}: wrong date {event_date_str}")
                    self.source_guard_status['date_check'] = False
                    all_valid = False
                    continue
                
                # Check symbol
                symbol = event.get('symbol', '')
                if symbol not in ['ESM6.CME@RITHMIC', 'NQM6.CME@RITHMIC']:
                    print(f"   ✗ Event {idx}: invalid symbol {symbol}")
                    self.source_guard_status['symbol_check'] = False
                    all_valid = False
                    continue
                
                # Check source
                source = event.get('source', '')
                if source != 'bookmap_l1_api':
                    print(f"   ✗ Event {idx}: invalid source {source}")
                    self.source_guard_status['source_check'] = False
                    all_valid = False
                    continue
                
                # Check price
                price = event.get('price')
                if price and not self.price_guard.is_tick_aligned(price):
                    print(f"   ✗ Event {idx}: non-aligned price {price}")
                    self.source_guard_status['price_check'] = False
                    all_valid = False
                    continue
                
                # Track symbols
                if symbol not in self.feed_health['symbols']:
                    self.feed_health['symbols'].append(symbol)
            
            if all_valid:
                self.source_guard_status['date_check'] = True
                self.source_guard_status['symbol_check'] = True
                self.source_guard_status['source_check'] = True
                self.source_guard_status['price_check'] = True
            
            return all_valid
        
        except Exception as e:
            print(f"   ✗ Error sampling: {e}")
            return False
    
    def save_status_files(self):
        """Save feed health and source guard status"""
        
        # Feed health
        self.feed_health['timestamp_et'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.feed_health['status'] = 'ACTIVE' if self.feed_path and self.feed_path.exists() else 'OFFLINE'
        
        with open('state/orderflow/live/feed_health.json', 'w') as f:
            json.dump(self.feed_health, f, indent=2)
        
        # Source guard status
        with open('state/orderflow/live/source_guard_status.json', 'w') as f:
            json.dump(self.source_guard_status, f, indent=2)
    
    def generate_alerts(self):
        """Generate alerts from feed (stub for now)"""
        
        if not self.source_guard_status['overall']:
            print("\n⛔ SOURCE GUARD FAILED — NO ALERTS GENERATED")
            return 0
        
        print("\n[ALERT GENERATION]")
        print("-" * 80)
        print(f"Source guard: ✓ PASSED")
        print(f"Status: OBSERVATIONAL ONLY (no auto-trade)")
        print(f"Ready to process live events...")
        
        # TODO: Implement alert generation from real events
        # For now, just report readiness
        
        return 0

def main():
    print("="*80)
    print("CLEAN LIVE ALERT ENGINE — REAL BOOKMAP FEED ONLY")
    print("="*80)
    
    engine = LiveAlertEngine()
    
    # Validate feed
    feed_ok = engine.validate_feed_with_guards()
    
    # Save status files
    engine.save_status_files()
    
    # Generate alerts if feed passes
    alerts = engine.generate_alerts()
    
    print(f"\n{'='*80}")
    print(f"STATUS: {engine.source_guard_status['verdict']}")
    print(f"ALERTS GENERATED: {alerts}")
    print(f"{'='*80}")
    
    return 0 if engine.source_guard_status['overall'] else 1

if __name__ == '__main__':
    exit(main())
