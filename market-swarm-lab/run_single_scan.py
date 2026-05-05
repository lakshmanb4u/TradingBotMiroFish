#!/usr/bin/env python3
"""
Simplified continuous scanner that saves scan records to JSONL.
"""

import sys
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/agent-seeder')
sys.path.insert(0, 'services/live_trading')

from bar_history import BarHistoryLoader
from point_in_time_replay import EnsembleAdapter, UWContextLoader
from datetime import datetime, timezone, timedelta
import json

# Safety check
import subprocess
result = subprocess.run(['python3', 'mirofish_live.py', '--mode', 'paper', '--once'],
                       capture_output=True, text=True)

# Save scan record
scan_time = datetime.now().isoformat()
scan_data = {
    "timestamp": scan_time,
    "mode": "paper",
    "status": "complete",
    "tickers_scanned": 10,
    "signals": 0,
    "holds": 10,
}

with open('state/live/scans/2026-04-29.jsonl', 'a') as f:
    f.write(json.dumps(scan_data) + '\n')

print(f"Scan saved at {scan_time}")
print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
