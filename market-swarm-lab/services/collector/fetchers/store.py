"""Persist raw payloads to state/raw/<source>/<ticker>_<timestamp>.json."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RAW_ROOT = Path(os.getenv("RAW_DATA_DIR", "state/raw"))


def save(source: str, ticker: str, payload: Any) -> str:
    dest = _RAW_ROOT / source
    dest.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = dest / f"{ticker.upper()}_{ts}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def latest(source: str, ticker: str) -> dict[str, Any] | None:
    dest = _RAW_ROOT / source
    pattern = f"{ticker.upper()}_*.json"
    files = sorted(dest.glob(pattern), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))
