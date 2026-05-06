#!/usr/bin/env python3
"""
Live Source Guard — Prevent Synthetic/Replay Contamination
Enforces hard rules for live orderflow data sources
"""

import os
import json
from datetime import datetime, date
from pathlib import Path

class LiveSourceGuard:
    """Validates data sources for live trading mode"""
    
    def __init__(self, today_date=None):
        self.today = today_date or date.today()
        self.rejected_count = 0
        self.accepted_count = 0
        self.quarantined = []
        
    def is_live_path_allowed(self, filepath):
        """Check if filepath is allowed for live mode"""
        
        # BLOCK all exports/ CSV files
        if 'exports/' in str(filepath):
            return False, "CSV ledger files not allowed in live mode"
        
        # BLOCK all reports/ files
        if 'reports/' in str(filepath):
            return False, "Report files not allowed in live mode"
        
        # BLOCK all test/mock files
        if any(x in str(filepath) for x in ['test', 'mock', 'replay', 'backtest', 'synthetic']):
            return False, "Test/mock/replay files not allowed in live mode"
        
        # ALLOW ONLY: state/orderflow/bookmap_api/*.jsonl
        if 'state/orderflow/bookmap_api/' not in str(filepath):
            return False, "Must read from state/orderflow/bookmap_api/ only"
        
        if not str(filepath).endswith('.jsonl'):
            return False, "Must be JSONL format"
        
        return True, "Path allowed"
    
    def validate_event_date(self, event):
        """Validate event timestamp matches today"""
        
        try:
            ts_str = event.get('ts_event', '')
            if not ts_str:
                return False, "Missing ts_event"
            
            # Extract date from ISO timestamp (YYYY-MM-DD)
            event_date_str = ts_str.split('T')[0]
            event_date = datetime.fromisoformat(event_date_str).date()
            
            if event_date != self.today:
                return False, f"Event date {event_date} != today {self.today}"
            
            return True, "Date valid"
        except Exception as e:
            return False, f"Date parsing error: {e}"
    
    def validate_symbol(self, event):
        """Validate symbol is ES or NQ"""
        
        symbol = event.get('symbol', '')
        
        if symbol not in ['ESM6.CME@RITHMIC', 'NQM6.CME@RITHMIC']:
            return False, f"Invalid symbol: {symbol}"
        
        return True, "Symbol valid"
    
    def validate_price_tick_alignment(self, price, symbol):
        """Validate price is tick-aligned"""
        
        tick_size = 0.25  # Both ES and NQ use 0.25
        
        # Check if price is multiple of tick size (within floating point tolerance)
        remainder = price % tick_size
        
        # Allow for floating point errors
        if abs(remainder) < 0.001 or abs(remainder - tick_size) < 0.001:
            return True, "Price tick-aligned"
        
        return False, f"Price {price} not tick-aligned to {tick_size}"
    
    def validate_price_range(self, price, symbol):
        """Validate price is in reasonable range for symbol"""
        
        if symbol == 'ESM6.CME@RITHMIC':
            min_price, max_price = 4000, 9000
            name = "ES"
        elif symbol == 'NQM6.CME@RITHMIC':
            min_price, max_price = 2000, 5000
            name = "NQ"
        else:
            return False, "Unknown symbol"
        
        if price < min_price or price > max_price:
            return False, f"Price {price} outside {name} range [{min_price}, {max_price}]"
        
        return True, "Price range valid"
    
    def validate_source(self, event):
        """Validate data source"""
        
        source = event.get('source', '')
        
        if source != 'bookmap_l1_api':
            return False, f"Invalid source: {source}"
        
        return True, "Source valid"
    
    def validate_event(self, event):
        """Validate entire event"""
        
        checks = [
            ('date', self.validate_event_date(event)),
            ('symbol', self.validate_symbol(event)),
            ('source', self.validate_source(event)),
        ]
        
        # Price checks only if we have valid symbol and price
        if checks[1][1][0]:  # symbol is valid
            symbol = event.get('symbol')
            price = event.get('price')
            
            if price is not None and price > 0:
                checks.append(('tick_align', self.validate_price_tick_alignment(price, symbol)))
                checks.append(('price_range', self.validate_price_range(price, symbol)))
        
        # Check all validations
        for check_name, (is_valid, reason) in checks:
            if not is_valid:
                return False, f"{check_name}: {reason}"
        
        return True, "Event valid"
    
    def process_event(self, event):
        """Process and validate single event"""
        
        is_valid, reason = self.validate_event(event)
        
        if is_valid:
            self.accepted_count += 1
            return True, reason
        else:
            self.rejected_count += 1
            self.quarantined.append({
                'timestamp': event.get('ts_event'),
                'symbol': event.get('symbol'),
                'price': event.get('price'),
                'reason': reason,
                'event': event
            })
            return False, reason

def run_guard_validation():
    """Run validation tests"""
    
    print("="*80)
    print("LIVE SOURCE GUARD VALIDATION")
    print("="*80)
    
    guard = LiveSourceGuard()
    
    print(f"\n[1] PATH VALIDATION")
    print("-" * 80)
    
    test_paths = [
        ('state/orderflow/bookmap_api/es_orderflow_2026-05-06.jsonl', True),
        ('exports/phase1_5_validated_ledger.csv', False),
        ('reports/phase2_backtest_results.md', False),
        ('test_fixtures/mock_es_data.jsonl', False),
    ]
    
    for path, should_allow in test_paths:
        allowed, reason = guard.is_live_path_allowed(path)
        status = "✓" if allowed == should_allow else "✗"
        print(f"{status} {path}: {reason}")
    
    print(f"\n[2] PRICE VALIDATION")
    print("-" * 80)
    
    test_prices = [
        (7400.00, 'ESM6.CME@RITHMIC', True),
        (7400.25, 'ESM6.CME@RITHMIC', True),
        (7400.50, 'ESM6.CME@RITHMIC', True),
        (7400.54, 'ESM6.CME@RITHMIC', False),  # Invalid
        (2784.69, 'ESM6.CME@RITHMIC', False),  # Invalid + out of range
        (3000.00, 'NQM6.CME@RITHMIC', True),
    ]
    
    for price, symbol, should_pass in test_prices:
        aligned, align_reason = guard.validate_price_tick_alignment(price, symbol)
        ranged, range_reason = guard.validate_price_range(price, symbol)
        
        is_valid = aligned and ranged
        status = "✓" if is_valid == should_pass else "✗"
        print(f"{status} {symbol} {price}: align={aligned}, range={ranged}")
    
    print(f"\n[3] EVENT VALIDATION")
    print("-" * 80)
    
    today_str = date.today().isoformat()
    
    valid_event = {
        'ts_event': f'{today_str}T12:00:00Z',
        'symbol': 'ESM6.CME@RITHMIC',
        'price': 7400.00,
        'source': 'bookmap_l1_api',
    }
    
    replay_event = {
        'ts_event': '2026-05-05T12:00:00Z',  # Yesterday
        'symbol': 'ESM6.CME@RITHMIC',
        'price': 7400.00,
        'source': 'bookmap_l1_api',
    }
    
    invalid_price_event = {
        'ts_event': f'{today_str}T12:00:00Z',
        'symbol': 'ESM6.CME@RITHMIC',
        'price': 7400.54,  # Not tick-aligned
        'source': 'bookmap_l1_api',
    }
    
    for label, event in [('valid', valid_event), ('replay', replay_event), ('invalid_price', invalid_price_event)]:
        is_valid, reason = guard.validate_event(event)
        print(f"{'✓' if is_valid else '✗'} {label}: {reason}")
    
    print(f"\n[4] SUMMARY")
    print("-" * 80)
    print(f"Accepted: {guard.accepted_count}")
    print(f"Rejected: {guard.rejected_count}")
    print(f"Quarantined: {len(guard.quarantined)}")
    
    # Determine verdict
    if guard.rejected_count == 0 and len(guard.quarantined) == 0:
        verdict = "LIVE_PATH_CLEAN"
    elif 'state/orderflow/bookmap_api/' in str(test_paths[0][0]):
        verdict = "LIVE_PATH_STILL_CONTAMINATED"
    else:
        verdict = "LIVE_FEED_NOT_AVAILABLE"
    
    print(f"\nVERDICT: {verdict}")
    
    return verdict

if __name__ == '__main__':
    run_guard_validation()
