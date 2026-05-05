#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$DIR/bookmap-recorder/build/BookmapOrderflowRecorder.jar"

echo "=== Install BookmapOrderflowRecorder ==="

if [[ ! -f "$JAR" ]]; then
    echo "ERROR: JAR not found. Run scripts/build_bookmap_recorder.sh first."
    exit 1
fi

# macOS Bookmap strategies directory
BM_STRATEGIES="$HOME/Library/Application Support/Bookmap/Strategies"
mkdir -p "$BM_STRATEGIES"

cp "$JAR" "$BM_STRATEGIES/"
echo "Installed to: $BM_STRATEGIES/BookmapOrderflowRecorder.jar"

# Verify
if [[ -f "$BM_STRATEGIES/BookmapOrderflowRecorder.jar" ]]; then
    echo "=== Install OK ==="
    echo ""
    echo "Next steps:"
    echo "1. Start Bookmap"
    echo "2. Open ES chart"
    echo "3. Add indicator → 'OrderflowRecorder'"
    echo "4. Output will be written to:"
    echo "   $HOME/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/"
else
    echo "INSTALL FAILED"
    exit 1
fi
