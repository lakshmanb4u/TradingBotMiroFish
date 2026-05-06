#!/bin/bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
source .venv/bin/activate
python3 scripts/segmented_replay_validation.py --input state/orderflow/bookmap_api/es_orderflow_2026-05-05.jsonl --output reports/session_segmented_replay_validation.md
