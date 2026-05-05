#!/bin/bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
source .venv/bin/activate
python3 scripts/validate_replay_api.py state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl
