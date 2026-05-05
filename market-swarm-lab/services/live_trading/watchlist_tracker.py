"""
Watchlist tracker for sub-threshold candidates.
Logs signals that don't meet execution criteria for later analysis.
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class WatchlistTracker:
    """Tracks sub-threshold signal candidates."""
    
    STATE_DIR = Path("state/live")
    
    def __init__(self, config: dict):
        self.config = config.get("watchlist_mode", {})
        self.enabled = self.config.get("enabled", True)
        self.min_votes = self.config.get("min_votes_for_watch", 2)
        self.candidates: list[dict] = []
    
    def log_candidate(
        self,
        ticker: str,
        action: str,
        votes_bull: int,
        votes_bear: int,
        regime: str,
        confidence: int,
        rejection_reason: str,
        price: float,
    ):
        """Log a sub-threshold candidate."""
        if not self.enabled:
            return
        
        total_votes = votes_bull + votes_bear
        if total_votes < self.min_votes:
            return
        
        candidate = {
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "action": action,
            "votes_bull": votes_bull,
            "votes_bear": votes_bear,
            "regime": regime,
            "confidence": confidence,
            "rejection_reason": rejection_reason,
            "price": price,
        }
        
        self.candidates.append(candidate)
        self._write_candidate(candidate)
        
        _log.info("[watchlist] %s: %s (votes %d/%d, %s) - %s",
                 ticker, action, votes_bull, votes_bear, regime, rejection_reason)
    
    def _write_candidate(self, candidate: dict):
        """Write candidate to watchlist log."""
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        watchlist_file = self.STATE_DIR / "watchlist_candidates.csv"
        
        fieldnames = [
            "timestamp", "ticker", "action", "votes_bull", "votes_bear",
            "regime", "confidence", "rejection_reason", "price",
        ]
        
        file_exists = watchlist_file.exists()
        with open(watchlist_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(candidate)
    
    def get_candidates_by_ticker(self, ticker: str) -> list[dict]:
        """Get all candidates for a specific ticker."""
        return [c for c in self.candidates if c["ticker"] == ticker]
    
    def get_candidates_by_reason(self, reason: str) -> list[dict]:
        """Get candidates filtered by rejection reason."""
        return [c for c in self.candidates if reason in c["rejection_reason"]]
    
    def get_summary(self) -> dict:
        """Get summary of tracked candidates."""
        if not self.candidates:
            return {"total": 0}
        
        by_ticker = {}
        by_reason = {}
        
        for c in self.candidates:
            ticker = c["ticker"]
            reason = c["rejection_reason"]
            
            by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
        
        return {
            "total": len(self.candidates),
            "by_ticker": dict(sorted(by_ticker.items(), key=lambda x: -x[1])[:10]),
            "by_reason": dict(sorted(by_reason.items(), key=lambda x: -x[1])[:5]),
            "most_recent": self.candidates[-1] if self.candidates else None,
        }
    
    def format_summary_text(self) -> str:
        """Format watchlist summary as text."""
        summary = self.get_summary()
        
        if summary["total"] == 0:
            return "No watchlist candidates today."
        
        lines = [
            f"Watchlist Candidates: {summary['total']}",
            "",
            "Top Tickers:",
        ]
        
        for ticker, count in summary["by_ticker"].items():
            lines.append(f"  {ticker}: {count} candidates")
        
        lines.extend(["", "Top Rejection Reasons:"])
        for reason, count in summary["by_reason"].items():
            lines.append(f"  {reason}: {count}")
        
        return "\n".join(lines)
