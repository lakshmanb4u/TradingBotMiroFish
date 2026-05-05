#!/usr/bin/env python3
"""
Continuous scanner for paper trading.
Runs during market hours, executes one scan per run.
Called by cron every 5 minutes during market hours.
"""

import sys
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/agent-seeder')
sys.path.insert(0, 'services/live_trading')

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("state/live/continuous.log"),
    ]
)
log = logging.getLogger(__name__)

def is_market_hours() -> bool:
    now = datetime.now(timezone.utc) - timedelta(hours=4)
    if now.weekday() >= 5:  # Skip weekends
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= minutes <= (16 * 60)

def run_scan():
    """Run one scan cycle."""
    if not is_market_hours():
        log.info("Market closed, skipping scan")
        return False
        
    log.info("Running paper trading scan...")
    
    # Run mirofish_live.py --mode paper --once
    result = subprocess.run([
        sys.executable,
        "mirofish_live.py",
        "--mode", "paper",
        "--once",
    ], capture_output=True, text=True, timeout=120)
    
    # Append to daily log
    daily_log = Path(f"state/live/paper_{datetime.now().strftime('%Y-%m-%d')}.log")
    with open(daily_log, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Scan: {datetime.now().isoformat()}\n")
        f.write(result.stdout)
        f.write(result.stderr)
    
    log.info("Scan complete. See %s", daily_log)
    return True

def run_until_market_close():
    """Run continuously during market hours."""
    log.info("Starting continuous paper trading loop")
    
    while True:
        try:
            if is_market_hours():
                run_scan()
                # Sleep 5 minutes
                import time
                time.sleep(300)
            else:
                log.info("Market closed, waiting...")
                import time
                time.sleep(300)
                
        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Error: %s", e)
            import time
            time.sleep(60)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_scan()
    else:
        run_until_market_close()
