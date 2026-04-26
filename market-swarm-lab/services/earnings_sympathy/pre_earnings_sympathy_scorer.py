"""PreEarningsSympathyScorer — deterministic weighted scorer for sympathy candidates.

Score formula:
  final_score = 0.25 * positioning_score
              + 0.25 * convexity_score
              + 0.20 * historical_sympathy_score
              + 0.20 * technical_score
              + 0.10 * flow_score

If Unusual Whales unavailable, redistributes flow weight across positioning (+5%) and technical (+5%).

Hard skip conditions (any one → SKIP regardless of score):
  - spread_pct > max_spread_pct
  - no liquidity (volume=0 or oi=0)
  - premium > max_risk_per_trade / 100
  - iv_rank too high (iv > iv_baseline * 1.4)
  - sympathy ticker has own earnings within 5 days
  - premarket move > max_premarket_move_pct_before_skip
  - no identifiable trigger level (technical_score < 10)
  - final_score < min_final_score

Strategy type is assigned based on delta:
  - PRE_EARNINGS_LOTTO:           delta 0.10-0.20
  - PRE_EARNINGS_SAFER_MOMENTUM:  delta 0.30-0.50
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_CONFIG: dict[str, Any] = {
    "max_risk_per_trade": 500,
    "min_final_score": 75,
    "max_spread_pct": 15.0,
    "min_volume": 100,
    "min_open_interest": 500,
    "min_dte": 3,
    "max_dte": 14,
    "avoid_own_earnings_within_days": 5,
    "max_premarket_move_pct_before_skip": 3.0,
    "iv_expansion_hard_limit": 1.4,
    "max_candidates_to_llm": 3,
    "min_final_score_for_llm": 70,
    "llm_timeout_seconds": 15,
}


def _load_config() -> dict[str, Any]:
    cfg_path = _ROOT / "config" / "sympathy_strategy_config.json"
    if cfg_path.exists():
        try:
            return {**_DEFAULT_CONFIG, **json.loads(cfg_path.read_text())}
        except Exception as exc:
            _log.warning("[scorer] config load error: %s — using defaults", exc)
    return _DEFAULT_CONFIG.copy()


def _strategy_type(delta: float) -> str:
    if 0.10 <= delta <= 0.20:
        return "PRE_EARNINGS_LOTTO"
    if 0.30 <= delta <= 0.50:
        return "PRE_EARNINGS_SAFER_MOMENTUM"
    if delta < 0.10:
        return "PRE_EARNINGS_LOTTO"   # very cheap lotto
    return "PRE_EARNINGS_SAFER_MOMENTUM"


class PreEarningsSympathyScorer:
    """Score and rank sympathy option candidates deterministically."""

    def __init__(self, config: dict | None = None) -> None:
        self._cfg = config or _load_config()

    def score_candidate(
        self,
        reporter: str,
        sympathy_ticker: str,
        contract: dict,
        positioning: dict,
        iv_analysis: dict,
        hist_sympathy: dict,
        technical: dict,
        uw_flow: dict | None,
        premarket_move_pct: float = 0.0,
        has_own_earnings_soon: bool = False,
    ) -> dict[str, Any]:
        """Score a single option contract as a sympathy candidate.

        Returns a full candidate dict with action WATCHLIST | BUY_TRIGGERED | SKIP.
        Every skip has a skip_reason.
        """
        cfg = self._cfg
        reporter = reporter.upper()
        sympathy_ticker = sympathy_ticker.upper()

        # ── Hard filters (SKIP immediately) ───────────────────────────────────
        skip_reason = self._hard_filter(
            contract=contract,
            iv_analysis=iv_analysis,
            premarket_move_pct=premarket_move_pct,
            has_own_earnings_soon=has_own_earnings_soon,
        )

        positioning_score  = int(positioning.get("positioning_score", 0))
        convexity_score    = int(iv_analysis.get("convexity_score", 0))
        hist_score         = int(hist_sympathy.get("historical_score", 50))
        technical_score    = int(technical.get("technical_score", 0))
        flow_score         = int((uw_flow or {}).get("flow_score", 50))
        uw_available       = uw_flow is not None and uw_flow.get("available", False)

        # Weighted final score
        if uw_available:
            final_score = (
                0.25 * positioning_score
                + 0.25 * convexity_score
                + 0.20 * hist_score
                + 0.20 * technical_score
                + 0.10 * flow_score
            )
        else:
            # Redistribute UW weight: +5% positioning, +5% technical
            final_score = (
                0.30 * positioning_score
                + 0.25 * convexity_score
                + 0.20 * hist_score
                + 0.25 * technical_score
            )
        final_score = min(100, max(0, round(final_score)))

        # Score threshold hard filter
        if not skip_reason and final_score < cfg["min_final_score"]:
            skip_reason = f"final_score_{final_score}_below_threshold_{cfg['min_final_score']}"

        # No trigger level = skip
        if not skip_reason and technical.get("setup_status") == "skip":
            skip_reason = "no_technical_trigger_level"

        # ── Determine action ──────────────────────────────────────────────────
        if skip_reason:
            action = "SKIP"
        elif technical.get("setup_status") == "ready":
            action = "BUY_TRIGGERED"
        else:
            action = "WATCHLIST"

        strike   = contract.get("strike", 0.0)
        expiry   = contract.get("expiry", "")
        premium  = contract.get("mid", 0.0)
        delta    = contract.get("delta", 0.0)
        dte      = contract.get("dte", 0)
        otype    = contract.get("option_type", "CALL")
        spread   = contract.get("spread_pct", 0.0)
        prem_pct = contract.get("premium_pct_of_underlying", 0.0)
        max_loss = round(premium * 100, 2)

        strategy = _strategy_type(delta) if action != "SKIP" else ""

        # Build reason string
        if action != "SKIP":
            parts = []
            if convexity_score >= 80:
                parts.append("high convexity")
            if positioning_score >= 75:
                parts.append("strong pre-positioning")
            if technical.get("setup_status") == "ready":
                parts.append("technical breakout triggered")
            elif technical.get("setup_status") == "watchlist":
                parts.append(f"watching trigger at ${technical.get('trigger_level', 0):.2f}")
            if hist_sympathy.get("avg_1d_move_pct", 0) > 0:
                parts.append(
                    f"hist avg move {hist_sympathy['avg_1d_move_pct']:.1f}% "
                    f"({hist_sympathy.get('direction_consistency', 0)*100:.0f}% directional)"
                )
            reason = "; ".join(parts) if parts else "passes all filters"
        else:
            reason = skip_reason or "failed hard filter"

        candidate = {
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "option_type": otype,
            "strike": strike,
            "expiry": expiry,
            "premium": premium,
            "max_loss": max_loss,
            "spread_pct": spread,
            "delta": delta,
            "dte": dte,
            "premium_pct_of_underlying": prem_pct,
            "implied_volatility": contract.get("implied_volatility", 0.0),
            "positioning_score": positioning_score,
            "convexity_score": convexity_score,
            "historical_sympathy_score": hist_score,
            "technical_score": technical_score,
            "flow_score": flow_score if uw_available else None,
            "uw_available": uw_available,
            "final_score": final_score,
            "action": action,
            "strategy_type": strategy,
            "trigger_level": technical.get("trigger_level"),
            "invalidation_level": technical.get("invalidation_level"),
            "setup_status": technical.get("setup_status"),
            "reason": reason,
            "skip_reason": skip_reason,
            "premarket_move_pct": premarket_move_pct,
            "has_own_earnings_soon": has_own_earnings_soon,
            "hist_avg_1d_move_pct": hist_sympathy.get("avg_1d_move_pct"),
            "hist_max_1d_move_pct": hist_sympathy.get("max_1d_move_pct"),
            "hist_direction_consistency": hist_sympathy.get("direction_consistency"),
            "hist_correlation": hist_sympathy.get("correlation"),
            "iv_rank": iv_analysis.get("iv_rank"),
            "iv_dislocation_score": iv_analysis.get("iv_dislocation_score"),
        }
        return candidate

    def rank_candidates(self, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split into passing and skipped, sorted by final_score descending."""
        passing = [c for c in candidates if c["action"] != "SKIP"]
        skipped = [c for c in candidates if c["action"] == "SKIP"]
        passing.sort(key=lambda c: c["final_score"], reverse=True)
        skipped.sort(key=lambda c: c["final_score"], reverse=True)
        return passing, skipped

    def should_call_llm(self, passing: list[dict]) -> bool:
        """Only call LLM if there are enough high-quality candidates."""
        cfg = self._cfg
        eligible = [c for c in passing if c["final_score"] >= cfg["min_final_score_for_llm"]]
        return len(eligible) >= 2

    # ── Private ───────────────────────────────────────────────────────────────

    def _hard_filter(
        self,
        contract: dict,
        iv_analysis: dict,
        premarket_move_pct: float,
        has_own_earnings_soon: bool,
    ) -> str | None:
        cfg = self._cfg
        spread   = contract.get("spread_pct", 999.0)
        volume   = contract.get("volume", 0)
        oi       = contract.get("open_interest", 0)
        premium  = contract.get("mid", 0.0)
        dte      = contract.get("dte", 0)
        max_prem = cfg["max_risk_per_trade"] / 100

        if spread > cfg["max_spread_pct"]:
            return f"spread_{spread:.1f}pct_exceeds_{cfg['max_spread_pct']}pct"
        if volume < cfg["min_volume"]:
            return f"volume_{volume}_below_{cfg['min_volume']}"
        if oi < cfg["min_open_interest"]:
            return f"oi_{oi}_below_{cfg['min_open_interest']}"
        if premium > max_prem:
            return f"premium_{premium:.2f}_exceeds_max_{max_prem:.2f}"
        if dte < cfg["min_dte"] or dte > cfg["max_dte"]:
            return f"dte_{dte}_out_of_range_{cfg['min_dte']}-{cfg['max_dte']}"
        if has_own_earnings_soon:
            return f"sympathy_has_own_earnings_within_{cfg['avoid_own_earnings_within_days']}d"
        if premarket_move_pct > cfg["max_premarket_move_pct_before_skip"]:
            return f"premarket_move_{premarket_move_pct:.1f}pct_edge_gone"
        if iv_analysis.get("iv_expanded", False):
            return "iv_already_expanded_above_1.4x_baseline"
        return None
