#!/bin/bash
# Live Orderflow Alert System Starter
# SAFETY: Always runs in dry-run (alerts only, no trading)

set -e

cd "$(dirname "$0")"
source .venv/bin/activate

echo "🚀 Starting Live Orderflow Alert System..."
echo "   Mode: DRY RUN (alerts only, no execution)"
echo "   Time: $(date)"

# Verify Bookmap is running
if ! pgrep -f "Bookmap" > /dev/null; then
    echo "❌ Bookmap is not running. Please start Bookmap first."
    exit 1
fi

# Verify JSONL directory exists
mkdir -p state/orderflow/bookmap_api

# Run the alert engine
exec python scripts/run_live_orderflow_alerts.py \
  --watch "state/orderflow/bookmap_api/*.jsonl" \
  --spy-source schwab \
  --notify whatsapp \
  --confidence-threshold 75 \
  --cooldown-minutes 10 \
  --dry-run \
  --interval 5
