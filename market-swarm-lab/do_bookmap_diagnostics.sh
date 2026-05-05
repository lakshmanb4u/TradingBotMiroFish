#!/bin/bash
cd /Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab
source .venv/bin/activate
python3 -c "
import json, glob, os, time
from pathlib import Path
out = Path('state/orderflow/live/bookmap_diagnostics.md')
out.parent.mkdir(parents=True, exist_ok=True)
files = sorted(glob.glob('state/orderflow/bookmap_api/*.jsonl'), key=lambda p: -os.path.getmtime(p))
total = 0; trade=0; depth=0; last_ts=''; last_seq=0
if files:
    latest = files[0]
    mtime = latest.stat().st_mtime if isinstance(latest, Path) else os.path.getmtime(latest)
    age_sec = time.time() - mtime
    with open(latest) as f:
        for n, line in enumerate(f):
            if n>=5000: break
            try:
                ev=json.loads(line.strip())
                total+=1
                if ev.get('event_type')=='trade': trade+=1
                if ev.get('event_type')=='depth': depth+=1
                last_ts=str(ev.get('ts_event',''))
                last_seq=int(ev.get('seq',0))
            except:
                pass
    print(f'Latest: {latest}  age_sec={age_sec:.0f}  total={total}  trades={trade}  depth={depth}  last_seq={last_seq}  last_ts={last_ts}')
else:
    print('No JSONL files found')
"
