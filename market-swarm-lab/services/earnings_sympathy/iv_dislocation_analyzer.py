"""IV Dislocation Analyzer + Historical Sympathy Move Engine.

IV Dislocation:
  - Computes iv_rank, iv_percentile, iv_dislocation_score
  - Detects cheap convexity: convexity_score = historical_move / premium_pct
  - Hard filter: if iv_current > iv_baseline * 1.4 → skip (IV already expanded)

Historical Sympathy Move Engine:
  - For each reporter → sympathy pair, track sympathy ticker moves around earnings
  - Computes: avg_1d_move_pct, max_1d_move_pct, direction_consistency, correlation
  - Uses Alpha Vantage for historical OHLCV
  - Persists stats to data/historical_sympathy/{reporter}_{sympathy}.json
  - Falls back to sector ETF correlation if insufficient history
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_HIST_DIR = _ROOT / "data" / "historical_sympathy"

_PRICE_DIR = str(_ROOT / "services" / "price-collector")
if _PRICE_DIR not in sys.path:
    sys.path.insert(0, _PRICE_DIR)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


_ensure_dir(_HIST_DIR)


# ── Historical Sympathy Move Engine ───────────────────────────────────────────

class HistoricalSympathyEngine:
    """Compute sympathy move statistics for a reporter → sympathy ticker pair."""

    # Sector ETF fallback when no pair history exists
    _SECTOR_ETF = {
        "semiconductors": "SMH",
        "cloud_software": "WCLD",
        "social_media": "SOCL",
        "ecommerce": "IBUY",
        "fintech": "FINX",
        "ev": "DRIV",
        "general": "SPY",
    }

    # Assumed avg moves when no data (conservative)
    _DEFAULT_MOVES: dict[str, float] = {
        "semiconductors": 3.5,
        "cloud_software": 2.8,
        "social_media": 4.0,
        "ecommerce": 2.5,
        "general": 2.0,
    }

    def compute(
        self,
        reporter: str,
        sympathy_ticker: str,
        past_earnings_dates: list[str],
        sector: str = "general",
    ) -> dict[str, Any]:
        """Return historical sympathy stats, loading from cache if available."""
        reporter = reporter.upper()
        sympathy_ticker = sympathy_ticker.upper()
        cache_path = _HIST_DIR / f"{reporter}_{sympathy_ticker}.json"

        # Load cached stats if fresh enough (< 30 days old)
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                age_days = (date.today() - datetime.fromisoformat(cached.get("computed_date", "2000-01-01")).date()).days
                if age_days < 30 and cached.get("sample_count", 0) > 0:
                    _log.info("[hist_sympathy] using cached stats for %s→%s (%dd old)", reporter, sympathy_ticker, age_days)
                    return cached
            except Exception:
                pass

        # Try to compute from price history
        if past_earnings_dates:
            result = self._compute_from_history(reporter, sympathy_ticker, past_earnings_dates)
            if result.get("sample_count", 0) >= 2:
                result["computed_date"] = date.today().isoformat()
                try:
                    cache_path.write_text(json.dumps(result, indent=2))
                except Exception:
                    pass
                return result

        # Fall back to defaults
        return self._default_result(reporter, sympathy_ticker, sector)

    def _compute_from_history(
        self,
        reporter: str,
        sympathy_ticker: str,
        past_earnings_dates: list[str],
    ) -> dict[str, Any]:
        """Compute move stats from Alpha Vantage historical prices."""
        sym_prices = self._fetch_prices(sympathy_ticker)
        rep_prices = self._fetch_prices(reporter)

        if not sym_prices or not rep_prices:
            return {"reporter": reporter, "sympathy_ticker": sympathy_ticker, "sample_count": 0}

        # Build date → close maps
        sym_map = {r["date"]: r["adjusted_close"] for r in sym_prices}
        rep_map = {r["date"]: r["adjusted_close"] for r in rep_prices}
        all_dates = sorted(set(sym_map.keys()) & set(rep_map.keys()))

        moves_1d: list[float] = []
        moves_3d: list[float] = []
        rep_moves_1d: list[float] = []

        for earn_date_str in past_earnings_dates:
            try:
                earn_date = datetime.strptime(earn_date_str, "%Y-%m-%d").date()
                # Find trading day before earnings
                pre_date = self._find_prev_trading_day(earn_date, all_dates)
                post_date_1d = self._find_next_trading_day(earn_date, all_dates, offset=1)
                post_date_3d = self._find_next_trading_day(earn_date, all_dates, offset=3)
                if not pre_date or not post_date_1d:
                    continue
                sym_pre = sym_map.get(pre_date)
                sym_post_1d = sym_map.get(post_date_1d)
                sym_post_3d = sym_map.get(post_date_3d) if post_date_3d else None
                rep_pre = rep_map.get(pre_date)
                rep_post_1d = rep_map.get(post_date_1d)
                if not sym_pre or not sym_post_1d or sym_pre <= 0:
                    continue
                move_1d = (sym_post_1d - sym_pre) / sym_pre * 100
                moves_1d.append(move_1d)
                if sym_post_3d and sym_post_3d > 0:
                    moves_3d.append((sym_post_3d - sym_pre) / sym_pre * 100)
                if rep_pre and rep_post_1d and rep_pre > 0:
                    rep_moves_1d.append((rep_post_1d - rep_pre) / rep_pre * 100)
            except Exception as exc:
                _log.debug("[hist_sympathy] skip earnings date %s: %s", earn_date_str, exc)

        if len(moves_1d) < 1:
            return {"reporter": reporter, "sympathy_ticker": sympathy_ticker, "sample_count": 0}

        abs_moves = [abs(m) for m in moves_1d]
        avg_1d = round(sum(abs_moves) / len(abs_moves), 3)
        max_1d = round(max(abs_moves), 3)
        avg_3d = round(sum(abs(m) for m in moves_3d) / len(moves_3d), 3) if moves_3d else avg_1d * 1.2

        # Direction consistency: how often sympathy moved same direction as reporter
        if rep_moves_1d and len(rep_moves_1d) == len(moves_1d):
            same_dir = sum(1 for s, r in zip(moves_1d, rep_moves_1d) if s * r > 0)
            direction_consistency = round(same_dir / len(moves_1d), 3)
        else:
            direction_consistency = 0.6  # assume moderate

        # Rolling correlation (all available dates)
        correlation = self._compute_correlation(sym_map, rep_map, all_dates)

        return {
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "avg_1d_move_pct": avg_1d,
            "max_1d_move_pct": max_1d,
            "avg_3d_move_pct": avg_3d,
            "direction_consistency": direction_consistency,
            "correlation": correlation,
            "sample_count": len(moves_1d),
            "raw_moves_1d": [round(m, 3) for m in moves_1d],
        }

    def _default_result(self, reporter: str, sympathy_ticker: str, sector: str) -> dict:
        avg_move = self._DEFAULT_MOVES.get(sector, 2.0)
        return {
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "avg_1d_move_pct": avg_move,
            "max_1d_move_pct": avg_move * 2.5,
            "avg_3d_move_pct": avg_move * 1.3,
            "direction_consistency": 0.55,
            "correlation": 0.50,
            "sample_count": 0,
            "data_source": "sector_default",
            "sector": sector,
        }

    def _fetch_prices(self, ticker: str) -> list[dict]:
        try:
            from alpha_vantage_client import AlphaVantageClient
            client = AlphaVantageClient()
            return client.fetch_daily(ticker, outputsize="full")
        except Exception as exc:
            _log.warning("[hist_sympathy] Alpha Vantage failed for %s: %s", ticker, exc)
        # Try Schwab history as fallback
        try:
            _SCHWAB_DIR = str(_ROOT / "services" / "schwab-collector")
            if _SCHWAB_DIR not in sys.path:
                sys.path.insert(0, _SCHWAB_DIR)
            from schwab_client import SchwabClient
            candles = SchwabClient().get_price_history(ticker)
            return [
                {
                    "date": datetime.fromtimestamp(c["timestamp"] / 1000).strftime("%Y-%m-%d")
                    if isinstance(c.get("timestamp"), (int, float))
                    else c.get("timestamp", ""),
                    "adjusted_close": float(c.get("close", 0)),
                }
                for c in candles
            ]
        except Exception as exc:
            _log.warning("[hist_sympathy] Schwab history failed for %s: %s", ticker, exc)
        return []

    @staticmethod
    def _find_prev_trading_day(earn_date: date, all_dates: list[str]) -> str | None:
        earn_str = earn_date.isoformat()
        candidates = [d for d in all_dates if d < earn_str]
        return candidates[-1] if candidates else None

    @staticmethod
    def _find_next_trading_day(earn_date: date, all_dates: list[str], offset: int = 1) -> str | None:
        earn_str = earn_date.isoformat()
        candidates = [d for d in all_dates if d > earn_str]
        return candidates[offset - 1] if len(candidates) >= offset else None

    @staticmethod
    def _compute_correlation(
        sym_map: dict[str, float], rep_map: dict[str, float], dates: list[str]
    ) -> float:
        """Pearson correlation of daily returns over shared dates."""
        shared = sorted(set(sym_map.keys()) & set(rep_map.keys()))
        if len(shared) < 20:
            return 0.5
        sym_rets = []
        rep_rets = []
        for i in range(1, len(shared)):
            d0, d1 = shared[i - 1], shared[i]
            s0, s1 = sym_map.get(d0, 0), sym_map.get(d1, 0)
            r0, r1 = rep_map.get(d0, 0), rep_map.get(d1, 0)
            if s0 > 0 and r0 > 0:
                sym_rets.append((s1 - s0) / s0)
                rep_rets.append((r1 - r0) / r0)
        if len(sym_rets) < 10:
            return 0.5
        n = len(sym_rets)
        mean_s = sum(sym_rets) / n
        mean_r = sum(rep_rets) / n
        cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(sym_rets, rep_rets)) / n
        std_s = math.sqrt(sum((s - mean_s) ** 2 for s in sym_rets) / n)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rep_rets) / n)
        if std_s == 0 or std_r == 0:
            return 0.0
        return round(min(1.0, max(-1.0, cov / (std_s * std_r))), 3)


# ── IV Dislocation Analyzer ────────────────────────────────────────────────────

class IVDislocationAnalyzer:
    """Detect cheap convexity and IV dislocation for sympathy candidates."""

    IV_EXPANSION_LIMIT = 1.4  # skip if iv > baseline * this factor

    def __init__(self) -> None:
        self._hist_engine = HistoricalSympathyEngine()

    def analyze(
        self,
        reporter: str,
        sympathy_ticker: str,
        contracts: list[dict],
        underlying_price: float,
        past_earnings_dates: list[str],
        sector: str = "general",
        reporter_iv: float = 0.0,
    ) -> dict[str, Any]:
        """Compute IV dislocation and convexity scores for each contract.

        Returns per-contract enrichment + aggregate scores.
        """
        reporter = reporter.upper()
        sympathy_ticker = sympathy_ticker.upper()

        hist = self._hist_engine.compute(reporter, sympathy_ticker, past_earnings_dates, sector)

        if not contracts:
            return {
                "reporter": reporter,
                "sympathy_ticker": sympathy_ticker,
                "convexity_score": 0,
                "iv_dislocation_score": 0,
                "historical_sympathy_score": self._historical_score(hist),
                "historical_stats": hist,
                "enriched_contracts": [],
                "contracts_passed_iv_filter": 0,
            }

        # Compute IV baseline from all contracts (median IV)
        ivs = [c["implied_volatility"] for c in contracts if c["implied_volatility"] > 0]
        iv_baseline = sorted(ivs)[len(ivs) // 2] if ivs else 0.30

        enriched: list[dict] = []
        iv_skipped = 0

        for c in contracts:
            iv = c["implied_volatility"]
            mid = c["mid"]
            premium_pct = c["premium_pct_of_underlying"]

            # Hard IV filter
            if iv > 0 and iv_baseline > 0 and iv > iv_baseline * self.IV_EXPANSION_LIMIT:
                c = {**c, "skip_reason": "iv_already_expanded", "skip": True}
                iv_skipped += 1
                enriched.append(c)
                continue

            # IV rank (rough: where current IV sits vs baseline)
            iv_rank = self._iv_rank(iv, iv_baseline)
            iv_percentile = iv_rank  # simplified; same scale
            iv_disloc = self._iv_dislocation_score(iv, iv_baseline)

            # Convexity: expected move vs premium paid
            avg_move = hist.get("avg_1d_move_pct", 2.0)
            if premium_pct > 0:
                raw_convexity = avg_move / premium_pct
            else:
                raw_convexity = 0.0
            contract_convexity = self._convexity_to_score(raw_convexity)

            enriched.append({
                **c,
                "iv_rank": round(iv_rank, 2),
                "iv_percentile": round(iv_percentile, 2),
                "iv_dislocation_score": iv_disloc,
                "raw_convexity_ratio": round(raw_convexity, 3),
                "contract_convexity_score": contract_convexity,
                "skip": False,
            })

        passed = [c for c in enriched if not c.get("skip")]

        # Best convexity score from passed contracts
        best_convexity = max((c["contract_convexity_score"] for c in passed), default=0)
        best_iv_disloc = max((c["iv_dislocation_score"] for c in passed), default=0)
        avg_convexity = (
            sum(c["contract_convexity_score"] for c in passed) / len(passed)
            if passed else 0
        )

        hist_score = self._historical_score(hist)

        return {
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "convexity_score": round((best_convexity * 0.6 + avg_convexity * 0.4)),
            "iv_dislocation_score": best_iv_disloc,
            "historical_sympathy_score": hist_score,
            "historical_stats": hist,
            "enriched_contracts": enriched,
            "contracts_passed_iv_filter": len(passed),
            "iv_baseline": round(iv_baseline, 4),
            "iv_skipped": iv_skipped,
        }

    # ── Score Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _iv_rank(current_iv: float, baseline_iv: float) -> float:
        """0-100: where current IV sits. Lower = cheaper (better for buying)."""
        if baseline_iv <= 0:
            return 50.0
        ratio = current_iv / baseline_iv
        # Below baseline = cheap, above = expensive
        return min(100, max(0, ratio * 50))

    @staticmethod
    def _iv_dislocation_score(current_iv: float, baseline_iv: float) -> int:
        """0-100: how dislocated (cheap) IV is vs baseline. Higher = more underpriced."""
        if baseline_iv <= 0:
            return 50
        ratio = current_iv / baseline_iv
        if ratio < 0.7:   return 95   # very cheap
        if ratio < 0.85:  return 80
        if ratio < 1.0:   return 65
        if ratio < 1.1:   return 50
        if ratio < 1.25:  return 35
        if ratio < 1.4:   return 20
        return 0  # too expensive — IV already expanded

    @staticmethod
    def _convexity_to_score(ratio: float) -> int:
        """Convert convexity ratio (expected_move / premium_pct) to 0-100 score."""
        if ratio >= 8.0:   return 100
        if ratio >= 5.0:   return 90
        if ratio >= 3.0:   return 75
        if ratio >= 2.0:   return 60   # minimum threshold for good trade
        if ratio >= 1.5:   return 40
        if ratio >= 1.0:   return 25
        return 10

    @staticmethod
    def _historical_score(hist: dict) -> int:
        """Convert historical sympathy stats to 0-100 score."""
        avg_move = hist.get("avg_1d_move_pct", 0)
        direction_consistency = hist.get("direction_consistency", 0.5)
        correlation = hist.get("correlation", 0.5)
        sample_count = hist.get("sample_count", 0)

        # Move size component (0-40)
        if avg_move >= 5.0:   move_pts = 40
        elif avg_move >= 3.0: move_pts = 30
        elif avg_move >= 2.0: move_pts = 20
        elif avg_move >= 1.0: move_pts = 10
        else:                  move_pts = 5

        # Direction consistency (0-30)
        dir_pts = round(direction_consistency * 30)

        # Correlation (0-20)
        corr_pts = round(max(0, correlation) * 20)

        # Sample count bonus (0-10)
        sample_pts = min(10, sample_count * 2)

        score = move_pts + dir_pts + corr_pts + sample_pts

        # Penalty if no real data (default estimates)
        if hist.get("data_source") == "sector_default":
            score = round(score * 0.7)

        return min(100, max(0, score))
