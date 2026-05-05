#!/bin/bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
python3 mirofish_live.py --mode paper --once 2>&1 | tee state/live/paper_today.log
