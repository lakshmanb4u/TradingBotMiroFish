#!/usr/bin/env python3
import json, time, glob, os

print("=== Bookmap Callback Diagnostics ===")
print("Time:", time.strftime("%Y-%m-%d %H:%M:%S %Z"))
print()

files = sorted(glob.glob("state/orderflow/bookmap_api/*.jsonl"), key=lambda p: -os.path.getmtime(p))
if not files:
    print("No JSONL files found!")
    exit(1)

latest = files[0]
mtime = os.path.getmtime(latest)
age_sec = time.time() - mtime
size_mb = os.path.getsize(latest) / (1024*1024)

print("1. File Growth Check:")
print("   File:", latest)
print("   Age: {} sec ({:.1f} min)".format(int(age_sec), age_sec/60))
print("   Size: {:.1f} MB".format(size_mb))
print("   Growing:", "NO ❌" if age_sec > 60 else "YES ✅")
print()

print("2. Event Type Distribution (first 10K events):")
event_types = {}
depths = trades = instruments = 0
with open(latest) as f:
    for i, line in enumerate(f):
        if i >= 10000: break
        try:
            ev = json.loads(line.strip())
            et = ev.get("event_type", "unknown")
            event_types[et] = event_types.get(et, 0) + 1
            if et == "depth": depths += 1
            if et == "trade": trades += 1
            if et == "instrument_added": instruments += 1
        except:
            pass

for et, count in sorted(event_types.items(), key=lambda x: -x[1]):
    pct = count / sum(event_types.values()) * 100
    print("   {}: {} ({:.1f}%)".format(et, count, pct))

print("   DEPTH events:", depths)
print("   TRADE events:", trades)
print("   INSTRUMENT events:", instruments)
print()

print("3. Callback Diagnosis:")
with open(latest) as f:
    first = json.loads(f.readline())
    symbol = first.get("symbol", "unknown")
    source = first.get("source", "unknown")
    print("   Symbol:", symbol)
    print("   Source:", source)
    
    if "BMD" in source.upper():
        print("   Feed type: BMD ⚠️")
        print("   BMD API permissions: LIMITED")
        print("   Layer1 callbacks: OFTEN BLOCKED in live mode")
    elif "RITHMIC" in source.upper():
        print("   Feed type: Rithmic ✅")
        print("   API support: FULL")
    else:
        print("   Feed type:", source)
print()

print("4. Root Cause Analysis:")
if age_sec > 300:
    print("   ❌ File NOT growing for >5 minutes")
    if depths > 0 and trades == 0:
        print("   → Depth callbacks fire, trade callbacks DON'T")
        print("   → PARTIAL API block - typical for BMD feed")
    elif depths == 0 and trades == 0:
        print("   → NO callbacks firing at all")
        print("   → Full API block OR feed disconnected")
    print()
    print("   LIKELY CAUSE: BMD feed blocking Layer1 API callbacks")
    print("   SOLUTION: Switch to Rithmic or contact Bookmap support")
else:
    print("   ✅ File is actively growing")
    if trades > 0:
        print("   ✅ Trade callbacks firing")
    else:
        print("   ⚠️  No trades yet (may be pre-market)")
