"""OI + Volume Analyzer — detect pre-positioning and unusual options activity.

Computes:
  - option_volume_to_oi_ratio
  - call/put volume and OI statistics
  - unusual_volume_score (volume vs historical average)
  - strike_cluster_score (multiple strikes showing activity)
  - repeated_strike_activity_score (same strike shows up across multiple snapshots)
  - oi_change (requires prior snapshot — gracefully skips if unavailable)

Persists daily snapshots to: data/options_positioning/{date}/{ticker}.json
Snapshots accumulate so OI change can be computed on subsequent runs.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT_DIR = _ROOT / "data" / "options_positioning_snapshots"
_POSITIONING_DIR = _ROOT / "data" / "options_positioning"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


class OIVolumeAnalyzer:
    """Analyze options positioning and detect unusual pre-earnings activity."""

    def __init__(self) -> None:
        _ensure_dir(_SNAPSHOT_DIR)
        _ensure_dir(_POSITIONING_DIR)

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(
        self,
        ticker: str,
        contracts: list[dict],
        date_str: str | None = None,
    ) -> dict[str, Any]:
        """Analyze positioning for a list of filtered contracts.

        Returns a dict with:
            positioning_score: 0-100
            call_volume, put_volume, call_oi, put_oi
            call_put_volume_ratio, option_volume_to_oi_ratio
            unusual_volume_score, strike_cluster_score
            oi_change_available, oi_changes (per strike, if available)
            raw_summary: {call/put breakdown by strike}
        """
        ticker = ticker.upper()
        today_str = date_str or date.today().isoformat()

        if not contracts:
            return self._empty_result(ticker, today_str, "no_contracts")

        # Aggregate call and put stats
        call_vol = sum(c["volume"] for c in contracts if c["option_type"] == "CALL")
        put_vol = sum(c["volume"] for c in contracts if c["option_type"] == "PUT")
        call_oi = sum(c["open_interest"] for c in contracts if c["option_type"] == "CALL")
        put_oi = sum(c["open_interest"] for c in contracts if c["option_type"] == "PUT")
        total_vol = call_vol + put_vol
        total_oi = call_oi + put_oi

        vol_to_oi = round(total_vol / total_oi, 4) if total_oi > 0 else 0.0
        cp_ratio = round(call_vol / put_vol, 3) if put_vol > 0 else (10.0 if call_vol > 0 else 1.0)

        # Load prior snapshot for OI change computation
        prior = self._load_prior_snapshot(ticker, today_str)
        oi_changes = self._compute_oi_changes(contracts, prior) if prior else {}
        oi_change_available = len(oi_changes) > 0

        # Compute component scores
        unusual_vol_score = self._unusual_volume_score(contracts, vol_to_oi)
        cluster_score = self._strike_cluster_score(contracts)
        repeated_score = self._repeated_strike_score(ticker, contracts) if prior else 50

        # Persist today's snapshot
        self._save_snapshot(ticker, today_str, contracts)
        self._save_positioning(ticker, today_str, contracts)

        # Final positioning score (0-100)
        positioning_score = self._compute_positioning_score(
            vol_to_oi=vol_to_oi,
            cp_ratio=cp_ratio,
            unusual_vol_score=unusual_vol_score,
            cluster_score=cluster_score,
            repeated_score=repeated_score,
            oi_change_available=oi_change_available,
            oi_changes=oi_changes,
            contracts=contracts,
        )

        return {
            "ticker": ticker,
            "date": today_str,
            "positioning_score": positioning_score,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_volume": total_vol,
            "total_oi": total_oi,
            "option_volume_to_oi_ratio": vol_to_oi,
            "call_put_volume_ratio": cp_ratio,
            "unusual_volume_score": unusual_vol_score,
            "strike_cluster_score": cluster_score,
            "repeated_strike_activity_score": repeated_score,
            "oi_change_available": oi_change_available,
            "oi_changes": oi_changes,
            "top_call_strikes": self._top_strikes(contracts, "CALL"),
            "top_put_strikes": self._top_strikes(contracts, "PUT"),
        }

    # ── Score Components ───────────────────────────────────────────────────────

    def _unusual_volume_score(self, contracts: list[dict], vol_to_oi: float) -> int:
        """Score 0-100 for how unusual the current volume is relative to OI."""
        if vol_to_oi >= 0.5:
            return 95
        if vol_to_oi >= 0.3:
            return 80
        if vol_to_oi >= 0.2:
            return 65
        if vol_to_oi >= 0.1:
            return 45
        if vol_to_oi >= 0.05:
            return 25
        return 10

    def _strike_cluster_score(self, contracts: list[dict]) -> int:
        """Score based on how many distinct strikes show significant activity."""
        active_calls = {c["strike"] for c in contracts
                        if c["option_type"] == "CALL" and c["volume"] >= 100}
        active_puts = {c["strike"] for c in contracts
                       if c["option_type"] == "PUT" and c["volume"] >= 100}
        n_call_clusters = len(active_calls)
        n_put_clusters = len(active_puts)
        # Bullish: many call strikes active
        max_clusters = max(n_call_clusters, n_put_clusters)
        if max_clusters >= 5:
            return 90
        if max_clusters >= 3:
            return 70
        if max_clusters >= 2:
            return 50
        if max_clusters >= 1:
            return 30
        return 10

    def _repeated_strike_score(self, ticker: str, contracts: list[dict]) -> int:
        """Score based on whether same strikes appeared in prior snapshot."""
        prior = self._load_prior_snapshot(ticker, date.today().isoformat())
        if not prior:
            return 50  # neutral when no history
        prior_strikes = {(c.get("strike"), c.get("option_type")) for c in prior}
        current_strikes = {(c["strike"], c["option_type"]) for c in contracts if c["volume"] >= 50}
        overlap = len(current_strikes & prior_strikes)
        if overlap >= 4:
            return 85
        if overlap >= 2:
            return 65
        if overlap >= 1:
            return 50
        return 35

    def _compute_oi_changes(
        self, contracts: list[dict], prior: list[dict]
    ) -> dict[str, float]:
        """Compute OI change per strike/option_type key from prior snapshot."""
        prior_map = {
            f"{c.get('strike')}_{c.get('option_type')}": c.get("open_interest", 0)
            for c in prior
        }
        changes: dict[str, float] = {}
        for c in contracts:
            key = f"{c['strike']}_{c['option_type']}"
            prior_oi = prior_map.get(key, 0)
            current_oi = c["open_interest"]
            if prior_oi > 0:
                changes[key] = round((current_oi - prior_oi) / prior_oi * 100, 2)
        return changes

    def _compute_positioning_score(
        self,
        vol_to_oi: float,
        cp_ratio: float,
        unusual_vol_score: int,
        cluster_score: int,
        repeated_score: int,
        oi_change_available: bool,
        oi_changes: dict,
        contracts: list[dict],
    ) -> int:
        # Base weighted score
        score = (
            0.35 * unusual_vol_score
            + 0.30 * cluster_score
            + 0.20 * repeated_score
        )

        # OI change boost (if available)
        if oi_change_available and oi_changes:
            avg_oi_change = sum(v for v in oi_changes.values() if v > 0) / max(len(oi_changes), 1)
            oi_boost = min(15, avg_oi_change / 5)  # cap at 15 pts
            score += oi_boost * 0.15
        else:
            # Redistribute if unavailable
            score = score / 0.85  # scale up to fill the 15% gap

        # Skew adjustment: heavy call bias before potential bullish catalyst = good
        if cp_ratio >= 2.5:
            score = min(100, score * 1.1)
        elif cp_ratio <= 0.4:
            score = min(100, score * 1.05)  # heavy put bias before potential bad news

        return min(100, max(0, round(score)))

    # ── Persistence ────────────────────────────────────────────────────────────

    def _snapshot_path(self, ticker: str, today_str: str) -> Path:
        return _SNAPSHOT_DIR / f"{today_str}_{ticker}.json"

    def _save_snapshot(self, ticker: str, today_str: str, contracts: list[dict]) -> None:
        path = self._snapshot_path(ticker, today_str)
        try:
            path.write_text(json.dumps(contracts, indent=2))
        except Exception as exc:
            _log.warning("[oi_analyzer] snapshot save failed for %s: %s", ticker, exc)

    def _save_positioning(self, ticker: str, today_str: str, contracts: list[dict]) -> None:
        day_dir = _POSITIONING_DIR / today_str
        _ensure_dir(day_dir)
        path = day_dir / f"{ticker}.json"
        try:
            path.write_text(json.dumps(contracts, indent=2))
        except Exception as exc:
            _log.warning("[oi_analyzer] positioning save failed: %s", exc)

    def _load_prior_snapshot(self, ticker: str, today_str: str) -> list[dict] | None:
        """Load most recent snapshot before today."""
        try:
            candidates = sorted(_SNAPSHOT_DIR.glob(f"*_{ticker}.json"), reverse=True)
            for path in candidates:
                date_part = path.stem.split("_")[0]
                if date_part < today_str:
                    data = json.loads(path.read_text())
                    if isinstance(data, list):
                        return data
        except Exception as exc:
            _log.debug("[oi_analyzer] prior snapshot load failed for %s: %s", ticker, exc)
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _top_strikes(self, contracts: list[dict], option_type: str, n: int = 3) -> list[dict]:
        subset = [c for c in contracts if c["option_type"] == option_type]
        subset.sort(key=lambda c: c["volume"], reverse=True)
        return [
            {"strike": c["strike"], "volume": c["volume"], "oi": c["open_interest"], "dte": c["dte"]}
            for c in subset[:n]
        ]

    def _empty_result(self, ticker: str, today_str: str, reason: str) -> dict:
        return {
            "ticker": ticker,
            "date": today_str,
            "positioning_score": 0,
            "call_volume": 0,
            "put_volume": 0,
            "call_oi": 0,
            "put_oi": 0,
            "total_volume": 0,
            "total_oi": 0,
            "option_volume_to_oi_ratio": 0.0,
            "call_put_volume_ratio": 1.0,
            "unusual_volume_score": 0,
            "strike_cluster_score": 0,
            "repeated_strike_activity_score": 0,
            "oi_change_available": False,
            "oi_changes": {},
            "top_call_strikes": [],
            "top_put_strikes": [],
            "skip_reason": reason,
        }
