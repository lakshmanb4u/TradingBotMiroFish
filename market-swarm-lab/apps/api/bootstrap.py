from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIRS = [
    ROOT / "services" / "collector",
    ROOT / "services" / "reddit-collector",
    ROOT / "services" / "normalizer",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "mirofish-bridge",
    ROOT / "services" / "reporting",
    ROOT / "services" / "seed-builder",
]

for service_dir in SERVICE_DIRS:
    service_path = str(service_dir)
    if service_path not in sys.path:
        sys.path.append(service_path)
