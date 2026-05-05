#!/bin/bash
cd "$HOME/.openclaw/workspace/market-swarm-lab"
source "$HOME/.openclaw/workspace/market-swarm-lab/.venv/bin/activate"
exec python scripts/run_live_orderflow_alerts.py --watch "state/orderflow/bookmap_api/*.jsonl" --spy-source cached --notify none --confidence-threshold 75 --cooldown-minutes 10 --dry-run --interval 30
