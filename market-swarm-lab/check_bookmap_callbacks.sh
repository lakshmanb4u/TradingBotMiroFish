/bin/bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
source .venv/bin/activate

python3 -c "
import json, time, glob, os
from pathlib import Path

print('=== Bookmap Callback Diagnostics ===')
print(f'Time: {time.strftime(\"%Y-%m-%d %H:%M:%S %Z\")}')
print()

# Check 1: File growth
files = sorted(glob.glob('state/orderflow/bookmap_api/*.jsonl'), key=lambda p: -os.path.getmtime(p))
if files:
    latest = files[0]
    mtime = os.path.getmtime(latest)
    age_sec = time.time() - mtime
    size_mb = os.path.getsize(latest) / (1024*1024)
    print(f'1. File: {latest}')
    print(f'   Age: {age_sec:.0f}s ({age_sec/60:.1f} min)')
    print(f'   Size: {size_mb:.1f} MB')
    print(f'   Growing: {\"NO\" if age_sec > 60 else \"YES\"}')
else:
    print('1. No JSONL files found!')
print()

# Check 2: Event type distribution
if files:
    event_types = {}
    depths = 0
    trades = 0
    instruments = 0
    with open(latest) as f:
        for i, line in enumerate(f):
            if i >= 10000: break
            try:
                ev = json.loads(line.strip())
                et = ev.get('event_type', 'unknown')
                event_types[et] = event_types.get(et, 0) + 1
                if et == 'depth': depths += 1
                if et == 'trade': trades += 1
                if et == 'instrument_added': instruments += 1
            except:
                pass
    print('2. Event types (first 10K events):')
    for et, count in sorted(event_types.items(), key=lambda x: -x[1]):
        pct = count / sum(event_types.values()) * 100
        print(f'   {et}: {count} ({pct:.1f}%)')
    print(f'   DEPTH events: {depths}')
    print(f'   TRADE events: {trades}')
    print(f'   INSTRUMENT events: {instruments}')
print()

# Check 3: Callback activity check
# If file not growing but Bookmap is live, callbacks are suppressed
print('3. Callback Activity Analysis:')
if age_sec > 300:  # 5 min stale
    print('   ⚠️  File NOT growing for >5 minutes')
    print('   → Bookmap Layer1 API callbacks likely SUPPRESSED')
    print('   → Check feed permissions and API license')
    if depths > 0 and trades == 0:
        print('   → Depth events exist but no trades → partial API block')
    elif depths == 0 and trades == 0:
        print('   → No events at all → full API block or feed disconnected')
else:
    print('   ✅ File is actively growing')
    if trades > 0:
        print('   ✅ Trade callbacks firing')
    else:
        print('   ⚠️  No trade events yet (may be pre-market)')
print()

# Check 4: Feed-specific diagnosis
print('4. Feed Diagnosis:')
with open(latest) as f:
    first = json.loads(f.readline())
    symbol = first.get('symbol', 'unknown')
    source = first.get('source', 'unknown')
    print(f'   Symbol: {symbol}')
    print(f'   Source: {source}')
    if 'BMD' in source.upper() or 'BMD' in symbol.upper():
        print('   ⚠️  BMD feed detected — API callbacks often restricted')
        print('   → Consider switching to Rithmic/CQG/dxFeed')
    elif 'RITHMIC' in source.upper():
        print('   ✅ Rithmic feed — API should work')
    elif 'CQG' in source.upper():
        print('   ✅ CQG feed — API should work')
    else:
        print(f'   ? Unknown feed source: {source}')
print()

print('=== Summary ===')
if age_sec > 300 and trades == 0:
    print('❌ LIKELY CAUSE: BMD feed blocking Layer1 API callbacks')
    print('   Fix: Switch to Rithmic/CQG or contact Bookmap for API access')
elif age_sec > 300:
    print('❌ LIKELY CAUSE: Feed disconnected or Bookmap not in live mode')
    print('   Fix: Verify Bookmap is live and connected')
else:
    print('✅ Feed appears healthy')
"

# Check Bookmap process
print()
echo "=== Bookmap Process ==="
ps aux | grep -i bookmap | grep -v grep | head -5

echo
echo "=== Bookmap API Port ==="
netstat -an | grep 41811 2>/dev/null || lsof -i :41811 2>/dev/null | head -5

echo
echo "=== Done ==="
echo "See state/orderflow/live/bookmap_diagnostics.md for full report"
