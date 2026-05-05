"""
Scan logger for live trading.
Records every scan cycle to JSONL for analysis.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

_log = logging.getLogger(__name__)


class ScanLogger:
    """Logs every scan cycle to daily JSONL file."""
    
    def __init__(self, state_dir: Path = Path("state/live")):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.scans_dir = self.state_dir / "scans"
        self.scans_dir.mkdir(exist_ok=True)
    
    def log_scan(self, scan_data: dict):
        """Log a single scan record."""
        today = date.today().isoformat()
        scan_file = self.scans_dir / f"{today}.jsonl"
        
        record = {
            "timestamp": datetime.now().isoformat(),
            **scan_data,
        }
        
        with open(scan_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    
    def get_daily_stats(self, scan_date: str | None = None) -> dict:
        """Get statistics for a day of scans."""
        if scan_date is None:
            scan_date = date.today().isoformat()
        
        scan_file = self.scans_dir / f"{scan_date}.jsonl"
        if not scan_file.exists():
            return {"total_scans": 0, "signals": 0, "holds": 0}
        
        total = 0
        signals = 0
        holds = 0
        hold_reasons: dict[str, int] = {}
        
        with open(scan_file) as f:
            for line in f:
                total += 1
                record = json.loads(line)
                if record.get("action") == "HOLD":
                    holds += 1
                    reason = record.get("hold_reason", "unknown")
                    hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
                elif record.get("action") != "HOLD":
                    signals += 1
        
        return {
            "total_scans": total,
            "signals": signals,
            "holds": holds,
            "top_hold_reasons": sorted(hold_reasons.items(), key=lambda x: -x[1])[:5],
        }
