#!/usr/bin/env python3
"""Run backtest 2: partial_profit strategy"""

import sys
import os

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'backtest'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'strategy-engine'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'agent-seeder'))

# Mock dotenv
class MockDotenv:
    @staticmethod
    def load_dotenv(*args, **kwargs):
        pass

sys.modules['dotenv'] = MockDotenv()

from point_in_time_replay import run_backtest

print("=" * 70)
print("BACKTEST 2: partial_profit strategy")
print("=" * 70)

result = run_backtest(
    ticker="SPY",
    start="2026-04-01",
    end="2026-04-25",
    timeframe="5min",
    threshold_profile="loose",
    strategy="partial_profit",
)

print("\n" + "=" * 70)
print("RESULTS:")
print("=" * 70)
print(f"Strategy: {result['config']['strategy']}")
print(f"Total trades: {result['summary'].get('total_signals', 0)}")
print(f"Win rate: {result['summary'].get('win_rate_pct', 0)}%")
print(f"Total R: {result['summary'].get('total_r', 0):.3f}")
print(f"Profit factor: {result['summary'].get('profit_factor', 0):.2f}")
print(f"Max drawdown: {result['summary'].get('max_drawdown_r', 0):.2f}R")
