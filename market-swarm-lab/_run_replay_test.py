#!/usr/bin/env python3
import subprocess
import sys

result = subprocess.run([
    sys.executable, "scripts/run_live_orderflow_alerts.py",
    "--watch", "state/orderflow/bookmap_api/*.jsonl",
    "--spy-source", "cached",
    "--notify", "none",
    "--confidence-threshold", "60",
    "--cooldown-minutes", "3",
    "--dry-run",
    "--replay-test-mode",
    "--replay-file", "state/orderflow/bookmap_api/es_orderflow_2026-05-03.jsonl"
], cwd="/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab", capture_output=True, text=True, timeout=300)

print("STDOUT:")
print(result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout)
print("\nSTDERR:")
print(result.stderr[-3000:] if len(result.stderr) > 3000 else result.stderr)
print(f"\nReturn code: {result.returncode}")
