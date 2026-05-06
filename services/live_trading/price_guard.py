#!/usr/bin/env python3
"""
Price Guard — Validate Trading Prices
Prevents invalid prices from reaching live trading
"""

class PriceGuard:
    """Validates prices for trading safety"""
    
    TICK_SIZE = 0.25  # Both ES and NQ
    
    # Symbol-specific ranges
    RANGES = {
        'ESM6.CME@RITHMIC': {'min': 4000, 'max': 9000, 'name': 'ES'},
        'NQM6.CME@RITHMIC': {'min': 2000, 'max': 5000, 'name': 'NQ'},
    }
    
    # Reject these known problematic prices (NQ range in ES feed)
    REJECTED_PATTERNS = [
        (2700, 2900),  # NQ range appearing as ES
        (2750, 2850),  # Typical NQ price in ES feed
    ]
    
    def __init__(self):
        self.rejected_prices = []
        self.accepted_prices = []
    
    def is_tick_aligned(self, price):
        """Check if price is aligned to 0.25 tick"""
        remainder = price % self.TICK_SIZE
        # Allow floating point tolerance
        return abs(remainder) < 0.001 or abs(remainder - self.TICK_SIZE) < 0.001
    
    def is_in_range(self, price, symbol):
        """Check if price is in valid range for symbol"""
        if symbol not in self.RANGES:
            return False
        
        range_info = self.RANGES[symbol]
        return range_info['min'] <= price <= range_info['max']
    
    def is_pattern_rejected(self, price):
        """Check if price matches rejected patterns (contamination signs)"""
        for min_p, max_p in self.REJECTED_PATTERNS:
            if min_p <= price <= max_p:
                return True
        return False
    
    def validate_price(self, price, symbol):
        """Validate single price"""
        
        if not isinstance(price, (int, float)):
            return False, "Not a number"
        
        if price <= 0:
            return False, "Non-positive price"
        
        if not self.is_in_range(price, symbol):
            range_info = self.RANGES.get(symbol, {})
            return False, f"Out of range [{range_info.get('min')}, {range_info.get('max')}]"
        
        if not self.is_tick_aligned(price):
            return False, f"Not tick-aligned to {self.TICK_SIZE}"
        
        if self.is_pattern_rejected(price):
            return False, "Matches rejected contamination pattern"
        
        return True, "Valid"
    
    def validate_alert_prices(self, entry, stop, target1, target2, symbol):
        """Validate all alert prices together"""
        
        prices = {
            'entry': entry,
            'stop': stop,
            'target1': target1,
            'target2': target2,
        }
        
        results = {}
        all_valid = True
        
        for label, price in prices.items():
            is_valid, reason = self.validate_price(price, symbol)
            results[label] = {'valid': is_valid, 'reason': reason}
            
            if not is_valid:
                all_valid = False
                self.rejected_prices.append({
                    'symbol': symbol,
                    'price_type': label,
                    'price': price,
                    'reason': reason
                })
        
        if all_valid:
            self.accepted_prices.append({
                'symbol': symbol,
                'entry': entry,
                'stop': stop,
                'target1': target1,
                'target2': target2,
            })
        
        return all_valid, results

def run_price_guard_tests():
    """Run price validation tests"""
    
    print("="*80)
    print("PRICE GUARD VALIDATION")
    print("="*80)
    
    guard = PriceGuard()
    
    print(f"\n[1] VALID ES PRICES")
    print("-" * 80)
    
    valid_es_prices = [7400.00, 7400.25, 7400.50, 7400.75, 5000.00, 9000.00]
    
    for price in valid_es_prices:
        is_valid, reason = guard.validate_price(price, 'ESM6.CME@RITHMIC')
        print(f"{'✓' if is_valid else '✗'} {price}: {reason}")
    
    print(f"\n[2] INVALID/CONTAMINATED PRICES")
    print("-" * 80)
    
    invalid_prices = [
        (7400.54, 'ESM6.CME@RITHMIC', 'not tick-aligned'),
        (2784.69, 'ESM6.CME@RITHMIC', 'NQ price in ES feed'),
        (6799.27, 'ESM6.CME@RITHMIC', 'not tick-aligned'),
        (7400.44, 'ESM6.CME@RITHMIC', 'not tick-aligned'),
        (2757.40, 'ESM6.CME@RITHMIC', 'NQ range'),
        (9001.00, 'ESM6.CME@RITHMIC', 'out of range'),
    ]
    
    for price, symbol, expected_reason in invalid_prices:
        is_valid, reason = guard.validate_price(price, symbol)
        status = "✓" if not is_valid else "✗"
        print(f"{status} {price} ({expected_reason}): {reason}")
    
    print(f"\n[3] ALERT PRICE SET VALIDATION")
    print("-" * 80)
    
    # Valid alert
    valid, results = guard.validate_alert_prices(
        entry=6799.50,
        stop=6732.00,
        target1=6874.50,
        target2=6949.50,
        symbol='ESM6.CME@RITHMIC'
    )
    print(f"{'✓' if valid else '✗'} Valid alert set: {valid}")
    
    # Contaminated alert (from backtest)
    invalid, results = guard.validate_alert_prices(
        entry=6799.27,  # Not tick-aligned
        stop=6732.00,
        target1=6874.07,  # Not tick-aligned
        target2=6949.15,  # Not tick-aligned
        symbol='ESM6.CME@RITHMIC'
    )
    print(f"{'✓' if not invalid else '✗'} Contaminated alert set: {not invalid}")
    for label, result in results.items():
        if not result['valid']:
            print(f"  ✗ {label}: {result['reason']}")
    
    print(f"\n[4] SUMMARY")
    print("-" * 80)
    print(f"Accepted prices: {len(guard.accepted_prices)}")
    print(f"Rejected prices: {len(guard.rejected_prices)}")
    
    return len(guard.rejected_prices) == 0

if __name__ == '__main__':
    run_price_guard_tests()
