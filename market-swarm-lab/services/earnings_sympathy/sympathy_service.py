"""SympathyService — orchestrator for the full pre-earnings sympathy detection pipeline.

Pipeline:
  EarningsCalendarService
    → SympathyMapper
    → OptionsPositioningScanner  (per sympathy ticker)
    → OIVolumeAnalyzer
    → IVDislocationAnalyzer + HistoricalSympathyEngine
    → TechnicalConfirmationEngine
    → PreEarningsSympathyScorer  (deterministic scoring + hard filters)
    → SympathyLLMAnalyst         (explain + veto only, never decides)
    → Ranked candidates + Markdown report

Works without MiroFish, without LLM, without Unusual Whales — all optional.
Degrades gracefully through the fallback chain.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]

# Service path setup
for _sd in [
    "services/schwab-collector",
    "services/uw-collector",
    "services/forecasting",
    "services/strategy-engine",
    "services/price-collector",
    "services/earnings_sympathy",
]:
    _sp = str(_ROOT / _sd)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

# Local service imports
from earnings_calendar_service import EarningsCalendarService, EarningsEvent
from sympathy_map import SympathyMapper
from options_positioning_scanner import OptionsPositioningScanner
from oi_volume_analyzer import OIVolumeAnalyzer
from iv_dislocation_analyzer import IVDislocationAnalyzer, HistoricalSympathyEngine
from technical_confirmation import TechnicalConfirmationEngine
from pre_earnings_sympathy_scorer import PreEarningsSympathyScorer
from sympathy_llm_analyst import SympathyLLMAnalyst

# Optional UW collector
try:
    from uw_collector_service import UWCollectorService
    _UW_AVAILABLE = True
except Exception:
    _UW_AVAILABLE = False
    _log.info("[sympathy] Unusual Whales not available — flow score will be neutral")

# Optional Schwab price
try:
    from schwab_price_service import SchwabPriceService
    _SCHWAB_PRICE = True
except Exception:
    _SCHWAB_PRICE = False

# Optional TimesFM
try:
    from forecasting_service import TimesFMForecastingService
    _TIMESFM = True
except Exception:
    _TIMESFM = False


def _load_config() -> dict[str, Any]:
    cfg_path = _ROOT / "config" / "sympathy_strategy_config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except Exception:
            pass
    return {}


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


class SympathyService:
    """Run the full earnings sympathy detection pipeline."""

    def __init__(self) -> None:
        self._cfg = _load_config()
        self._calendar = EarningsCalendarService()
        self._mapper   = SympathyMapper()
        self._scanner  = OptionsPositioningScanner(self._cfg)
        self._oi       = OIVolumeAnalyzer()
        self._iv       = IVDislocationAnalyzer()
        self._hist     = HistoricalSympathyEngine()
        self._tech     = TechnicalConfirmationEngine()
        self._scorer   = PreEarningsSympathyScorer(self._cfg)
        self._llm      = SympathyLLMAnalyst(timeout=self._cfg.get("llm_timeout_seconds", 15))
        self._uw       = UWCollectorService() if _UW_AVAILABLE else None
        self._price_svc = SchwabPriceService() if _SCHWAB_PRICE else None
        self._timesfm  = TimesFMForecastingService() if _TIMESFM else None

    # ── Public API ─────────────────────────────────────────────────────────────

    def scan_week(self, days_ahead: int = 14) -> dict[str, Any]:
        """Scan all upcoming reporters for the next N days."""
        events = self._calendar.fetch_upcoming(days_ahead=days_ahead)
        _log.info("[sympathy] scan_week: %d upcoming earnings events", len(events))
        all_candidates: list[dict] = []
        all_skipped: list[dict]    = []
        reporter_results: list[dict] = []

        for event in events:
            result = self.scan_reporter(event.ticker, event=event)
            all_candidates.extend(result["passing_candidates"])
            all_skipped.extend(result["skipped_candidates"])
            reporter_results.append({
                "reporter": event.ticker,
                "date": event.date,
                "time": event.time,
                "sector": event.sector,
                "sympathy_tickers_scanned": result["sympathy_tickers_scanned"],
                "passing_count": len(result["passing_candidates"]),
                "skipped_count": len(result["skipped_candidates"]),
            })

        passing, skipped = self._scorer.rank_candidates(all_candidates)

        run_id = self._save_run(
            tag="week_scan",
            events=[e.to_dict() for e in events],
            passing=passing,
            skipped=skipped,
        )

        return {
            "run_id": run_id,
            "scan_date": date.today().isoformat(),
            "days_ahead": days_ahead,
            "reporters_scanned": len(events),
            "reporter_results": reporter_results,
            "passing_candidates": passing,
            "skipped_candidates": skipped,
            "source_audit": self._source_audit(),
        }

    def scan_reporter(
        self,
        reporter: str,
        event: EarningsEvent | None = None,
    ) -> dict[str, Any]:
        """Scan sympathy tickers for a single reporting company."""
        reporter = reporter.upper()
        sympathy_tickers = self._mapper.get_sympathy_tickers(reporter)
        _log.info("[sympathy] reporter=%s → sympathy tickers: %s", reporter, sympathy_tickers)

        if event is None:
            # Try to find from calendar
            upcoming = self._calendar.fetch_upcoming(days_ahead=30)
            event = next((e for e in upcoming if e.ticker == reporter), None)

        past_events = self._calendar.get_past_earnings(reporter)

        all_candidates: list[dict] = []
        all_skipped:    list[dict] = []

        for sym_ticker in sympathy_tickers:
            try:
                candidates, skipped = self._scan_sympathy_ticker(
                    reporter=reporter,
                    sympathy_ticker=sym_ticker,
                    reporter_event=event,
                    past_reporter_events=past_events,
                )
                all_candidates.extend(candidates)
                all_skipped.extend(skipped)
            except Exception as exc:
                _log.warning("[sympathy] error scanning %s → %s: %s", reporter, sym_ticker, exc)
                all_skipped.append({
                    "reporter": reporter,
                    "sympathy_ticker": sym_ticker,
                    "action": "SKIP",
                    "skip_reason": f"scan_error: {exc}",
                    "final_score": 0,
                })

        passing, skipped_sorted = self._scorer.rank_candidates(all_candidates)
        skipped_all = skipped_sorted + all_skipped

        # LLM veto layer
        llm_result: dict[str, Any] = {"llm_analyses": [], "llm_status": {"degraded_mode": True, "reason": "not_called"}}
        if self._scorer.should_call_llm(passing):
            reporter_context = f"{reporter} has earnings on {event.date if event else 'upcoming'}."
            if event:
                reporter_context += f" Reports {event.time.replace('_', ' ')}. Sector: {event.sector}."
            llm_result = self._llm.analyze(passing[:3], reporter, reporter_context)
        elif len(passing) < 2:
            llm_result["llm_status"]["reason"] = "insufficient_candidates"

        # Apply LLM vetoes (mark vetoed candidates but keep them in list)
        vetoed_tickers = set()
        for la in llm_result.get("llm_analyses", []):
            if la.get("vetoed"):
                vetoed_tickers.add(la.get("sympathy_ticker", ""))
        for c in passing:
            la = next(
                (a for a in llm_result.get("llm_analyses", [])
                 if a.get("sympathy_ticker") == c["sympathy_ticker"]),
                None,
            )
            if la:
                c["llm_narrative"] = la.get("narrative_summary", "")
                c["llm_risks"] = la.get("risks", [])
                c["llm_vetoed"] = la.get("vetoed", False)
                c["llm_veto_reason"] = la.get("veto_reason", "")
            else:
                c["llm_vetoed"] = False
                c["llm_narrative"] = ""
                c["llm_risks"] = []

        run_id = self._save_run(
            tag=f"reporter_{reporter}",
            events=[event.to_dict()] if event else [],
            passing=passing,
            skipped=skipped_all,
            llm_result=llm_result,
        )

        return {
            "run_id": run_id,
            "reporter": reporter,
            "earnings_date": event.date if event else None,
            "earnings_time": event.time if event else None,
            "sympathy_tickers_scanned": sympathy_tickers,
            "passing_candidates": passing,
            "skipped_candidates": skipped_all,
            "llm_result": llm_result,
            "source_audit": self._source_audit(),
        }

    def scan_ticker(self, sympathy_ticker: str) -> dict[str, Any]:
        """Scan a specific sympathy ticker across all relevant upcoming reporters."""
        sympathy_ticker = sympathy_ticker.upper()
        upcoming = self._calendar.fetch_upcoming(days_ahead=14)
        relevant_reporters = [
            e for e in upcoming
            if sympathy_ticker in self._mapper.get_sympathy_tickers(e.ticker)
        ]
        _log.info("[sympathy] scan_ticker=%s → reporters: %s",
                  sympathy_ticker, [e.ticker for e in relevant_reporters])

        all_candidates: list[dict] = []
        all_skipped:    list[dict] = []

        for event in relevant_reporters:
            try:
                candidates, skipped = self._scan_sympathy_ticker(
                    reporter=event.ticker,
                    sympathy_ticker=sympathy_ticker,
                    reporter_event=event,
                    past_reporter_events=self._calendar.get_past_earnings(event.ticker),
                )
                all_candidates.extend(candidates)
                all_skipped.extend(skipped)
            except Exception as exc:
                _log.warning("[sympathy] scan_ticker error %s: %s", sympathy_ticker, exc)

        passing, skipped_sorted = self._scorer.rank_candidates(all_candidates)

        return {
            "sympathy_ticker": sympathy_ticker,
            "reporters_checked": [e.ticker for e in relevant_reporters],
            "passing_candidates": passing,
            "skipped_candidates": skipped_sorted + all_skipped,
            "source_audit": self._source_audit(),
        }

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _scan_sympathy_ticker(
        self,
        reporter: str,
        sympathy_ticker: str,
        reporter_event: EarningsEvent | None,
        past_reporter_events: list[EarningsEvent],
    ) -> tuple[list[dict], list[dict]]:
        """Full pipeline for one sympathy ticker. Returns (passing, skipped) candidate lists."""
        # Check if sympathy ticker has its own earnings soon
        has_own_earnings = self._calendar.has_earnings_soon(
            sympathy_ticker,
            within_days=self._cfg.get("avoid_own_earnings_within_days", 5),
        )

        # 1. Options chain scan
        chain_result = self._scanner.scan(sympathy_ticker)
        contracts = chain_result.get("contracts", [])

        if not contracts:
            skip_reason = chain_result.get("skip_reason", "no_options_contracts")
            return [], [{
                "reporter": reporter,
                "sympathy_ticker": sympathy_ticker,
                "action": "SKIP",
                "skip_reason": skip_reason,
                "final_score": 0,
                "source": chain_result.get("source", "unknown"),
            }]

        # 2. OI + Volume analysis
        positioning = self._oi.analyze(sympathy_ticker, contracts)

        # 3. IV dislocation
        iv_analysis = self._iv.analyze(sympathy_ticker, contracts)

        # 4. Historical sympathy moves
        hist = self._hist.compute(
            reporter=reporter,
            sympathy_ticker=sympathy_ticker,
            past_reporter_events=past_reporter_events,
        )

        # 5. Technical confirmation (uses Schwab intraday)
        technical = self._tech.confirm(sympathy_ticker)

        # 6. UW flow (optional)
        uw_flow: dict | None = None
        if self._uw:
            try:
                uw_raw = self._uw.collect(sympathy_ticker, current_price=chain_result.get("underlying_price", 0))
                uw_flow = {
                    "available": True,
                    "flow_score": self._uw_to_score(uw_raw.get("flow_bias", "neutral")),
                    "flow_bias": uw_raw.get("flow_bias", "neutral"),
                }
            except Exception as exc:
                _log.debug("[sympathy] UW error for %s: %s", sympathy_ticker, exc)

        # 7. Premarket move estimation (use intraday data if available)
        premarket_move_pct = technical.get("premarket_move_pct", 0.0)

        # 8. Score each passing contract — pick best call and best put separately
        passing: list[dict] = []
        skipped: list[dict] = []

        # Score top 3 OTM calls and top 3 OTM puts (avoid redundant same-ticker candidates)
        best_call = self._best_otm_contract(contracts, "CALL")
        best_put  = self._best_otm_contract(contracts, "PUT")

        for contract in filter(None, [best_call, best_put]):
            candidate = self._scorer.score_candidate(
                reporter=reporter,
                sympathy_ticker=sympathy_ticker,
                contract=contract,
                positioning=positioning,
                iv_analysis=iv_analysis,
                hist_sympathy=hist,
                technical=technical,
                uw_flow=uw_flow,
                premarket_move_pct=premarket_move_pct,
                has_own_earnings_soon=has_own_earnings,
            )
            if candidate["action"] == "SKIP":
                skipped.append(candidate)
            else:
                passing.append(candidate)

        return passing, skipped

    def _best_otm_contract(self, contracts: list[dict], option_type: str) -> dict | None:
        """Pick the best OTM contract of a given type by delta proximity to 0.15."""
        otm = [c for c in contracts if c["option_type"] == option_type and c.get("is_otm", True)]
        if not otm:
            return None
        # Target delta: 0.15 for lotto, pick closest
        otm.sort(key=lambda c: abs(c.get("delta", 0) - 0.15))
        return otm[0] if otm else None

    @staticmethod
    def _uw_to_score(bias: str) -> int:
        return {"bullish": 80, "neutral": 50, "bearish": 20}.get(bias, 50)

    def _source_audit(self) -> dict[str, Any]:
        return {
            "schwab_options":   "live" if _SCHWAB_PRICE else "fallback",
            "unusual_whales":   "live" if _UW_AVAILABLE else "unavailable",
            "timesfm":          "live" if _TIMESFM else "unavailable",
            "earnings_calendar": "config_file",
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save_run(
        self,
        tag: str,
        events: list[dict],
        passing: list[dict],
        skipped: list[dict],
        llm_result: dict | None = None,
    ) -> str:
        run_id = f"{date.today().isoformat()}_{tag}_{uuid.uuid4().hex[:8]}"
        run_dir = _ROOT / "state" / "runs" / run_id / "earnings_sympathy"
        _ensure_dir(run_dir)

        def _write(name: str, data: Any) -> None:
            try:
                (run_dir / name).write_text(json.dumps(data, indent=2, default=str))
            except Exception as exc:
                _log.warning("[sympathy] persist %s failed: %s", name, exc)

        _write("earnings_calendar.json", events)
        _write("ranked_candidates.json", passing)
        _write("skipped_candidates.json", skipped)
        if llm_result:
            _write("llm_veto.json", llm_result)

        # Generate markdown report
        report_md = self._build_report(tag, events, passing, skipped, llm_result)
        try:
            (run_dir / "final_report.md").write_text(report_md)
        except Exception as exc:
            _log.warning("[sympathy] report write failed: %s", exc)

        _log.info("[sympathy] run saved → %s", run_dir)
        return run_id

    def _build_report(
        self,
        tag: str,
        events: list[dict],
        passing: list[dict],
        skipped: list[dict],
        llm_result: dict | None,
    ) -> str:
        lines = [
            f"# Pre-Earnings Sympathy Report",
            f"**Run:** {tag}  |  **Date:** {date.today().isoformat()}",
            "",
            "## Upcoming Reporters",
        ]
        for e in events:
            lines.append(f"- **{e.get('ticker')}** — {e.get('date')} {e.get('time','')} | sector: {e.get('sector','')}")

        lines += ["", "## Top Candidates"]
        if not passing:
            lines.append("_No candidates passed all filters this run._")
        for i, c in enumerate(passing[:5], 1):
            vetoed = c.get("llm_vetoed", False)
            veto_tag = " ⚠️ LLM VETOED" if vetoed else ""
            lines += [
                f"",
                f"### {i}. {c['sympathy_ticker']} {c['option_type']} ${c['strike']} exp {c['expiry']}{veto_tag}",
                f"**Reporter:** {c['reporter']}  |  **Action:** {c['action']}  |  **Score:** {c['final_score']}/100  |  **Strategy:** {c.get('strategy_type','')}",
                f"**Premium:** ${c['premium']} (max loss ${c['max_loss']})  |  **Delta:** {c['delta']}  |  **DTE:** {c['dte']}  |  **Spread:** {c['spread_pct']}%",
                f"**Trigger:** ${c.get('trigger_level','?')}  |  **Invalidation:** ${c.get('invalidation_level','?')}",
                f"**Why:** {c['reason']}",
            ]
            if c.get("hist_avg_1d_move_pct"):
                lines.append(f"**Hist move:** avg {c['hist_avg_1d_move_pct']:.1f}% / max {c.get('hist_max_1d_move_pct',0):.1f}% | direction consistency {(c.get('hist_direction_consistency',0)*100):.0f}%")
            if c.get("llm_narrative"):
                lines.append(f"**LLM:** {c['llm_narrative']}")
            if c.get("llm_risks"):
                lines.append(f"**Risks:** {', '.join(c['llm_risks'])}")
            if vetoed:
                lines.append(f"**Veto reason:** {c.get('llm_veto_reason','')}")

        lines += ["", "---", "## Skipped Candidates"]
        if not skipped:
            lines.append("_None skipped._")
        for c in skipped[:20]:
            lines.append(
                f"- **{c.get('sympathy_ticker','')}** ({c.get('reporter','')}) — "
                f"score {c.get('final_score',0)} — `{c.get('skip_reason','?')}`"
            )

        sa = self._source_audit()
        lines += [
            "", "---", "## Source Audit",
            f"- Schwab options: **{sa['schwab_options']}**",
            f"- Unusual Whales: **{sa['unusual_whales']}**",
            f"- TimesFM: **{sa['timesfm']}**",
            f"- Earnings calendar: **{sa['earnings_calendar']}**",
        ]
        if llm_result:
            status = llm_result.get("llm_status", {})
            lines.append(
                f"- LLM analyst: **{'degraded' if status.get('degraded_mode') else 'live'}** "
                f"({status.get('reason','') or status.get('model','')})"
            )

        return "\n".join(lines)
