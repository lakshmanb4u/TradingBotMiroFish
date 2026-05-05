#!/usr/bin/env python3
"""Test live signal generation without market hours check."""

import sys
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/agent-seeder')
sys.path.insert(0, 'services/live_trading')

from mirofish_live import LiveSignalEngine, load_config

config = load_config()
config["mode"] = "alert_only"

engine = LiveSignalEngine(config)

# Test SPY signal generation
print("Testing SPY signal generation...")
alert = engine.generate_signal("SPY")

if alert:
    print("\n✅ SIGNAL GENERATED:")
    print(f"Ticker: {alert['ticker']}")
    print(f"Action: {alert['action']}")
    print(f"Entry: ${alert['underlying_entry']}")
    print(f"Stop: ${alert['underlying_stop']}")
    print(f"Confidence: {alert['confidence']}%")
    print(f"Regime: {alert['regime']}")
    print(f"Votes: {alert['votes']}")
    print(f"Reason: {alert['reason']}")
    print(f"\nRisk Notes:")
    for note in alert['risk_notes']:
        print(f"  • {note}")
    
    if alert.get('option_contract'):
        opt = alert['option_contract']
        print(f"\nOption Contract:")
        print(f"  Symbol: {opt.get('symbol', 'N/A')}")
        print(f"  Strike: ${opt.get('strike', 'N/A')}")
        print(f"  Expiry: {opt.get('expiry', 'N/A')}")
        print(f"  Premium: ${opt.get('premium', 'N/A')}")
        print(f"  Delta: {opt.get('delta', 'N/A')}")
else:
    print("\n❌ No signal generated (HOLD)")

# Test debug endpoint
print("\n" + "="*60)
print("DEBUG STATUS:")
print("="*60)
print(engine.debug.format_status_text())
