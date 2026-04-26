#!/usr/bin/env python3
"""Pre-Earnings Sympathy Detector CLI.

Usage:
    python earnings_sympathy_signal.py --week current
    python earnings_sympathy_signal.py --reporter INTC
    python earnings_sympathy_signal.py --ticker AMD
    python earnings_sympathy_signal.py --reporter MSFT --reporter GOOGL

Options:
    --week current      Scan all upcoming reporters this week (default)
    --reporter TICKER   Scan sympathy plays for a specific reporter
    --ticker TICKER     Scan which reporters might move this sympathy ticker
    --days N            Look-ahead window in days (default: 14)
    --top N             Show top N candidates (default: 5)
    --debug             Enable debug logging
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _sd in [
    ROOT / "services" / "earnings_sympathy",
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "price-collector",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from sympathy_service import SympathyService


# ── Formatting ─────────────────────────────────────────────────────────────────

def _bar(width: int = 60) -> str:
    return "=" * width


def _print_candidate(c: dict, rank: int) -> None:
    action = c["action"]
    arrow = "▲" if c["option_type"] == "CALL" else "▼"
    vetoed = c.get("llm_vetoed", False)
    veto_tag = "  ⚠️  LLM VETOED" if vetoed else ""

    print(f"\n  {rank}. {c['sympathy_ticker']:6s}  {arrow} {c['option_type']}  "
          f"${c['strike']:.0f}  exp {c['expiry']}  [{action}]{veto_tag}")
    print(f"     Reporter: {c['reporter']}  |  Score: {c['final_score']}/100  |  "
          f"Strategy: {c.get('strategy_type','?')}")
    print(f"     Premium:  ${c['premium']:.2f}  (max loss ${c['max_loss']:.0f})  |  "
          f"Delta: {c['delta']:.2f}  |  DTE: {c['dte']}  |  Spread: {c['spread_pct']:.1f}%")

    if c.get("trigger_level"):
        print(f"     Entry trigger: ${c['trigger_level']:.2f}  |  "
              f"Invalidation: ${c.get('invalidation_level',0):.2f}")

    print(f"     Why: {c['reason']}")

    if c.get("hist_avg_1d_move_pct"):
        dc = c.get("hist_direction_consistency", 0) or 0
        print(f"     Hist: avg {c['hist_avg_1d_move_pct']:.1f}%  "
              f"max {c.get('hist_max_1d_move_pct',0):.1f}%  "
              f"directional {dc*100:.0f}%")

    scores = (
        f"positioning={c['positioning_score']}  "
        f"convexity={c['convexity_score']}  "
        f"hist={c['historical_sympathy_score']}  "
        f"tech={c['technical_score']}"
    )
    if c.get("flow_score") is not None:
        scores += f"  flow={c['flow_score']}"
    print(f"     Scores: {scores}")

    if c.get("llm_narrative") and not vetoed:
        print(f"     LLM: {c['llm_narrative'][:120]}")
    if vetoed:
        print(f"     Veto reason: {c.get('llm_veto_reason','')}")


def _print_skipped(skipped: list[dict], limit: int = 15) -> None:
    print(f"\n  Skipped ({len(skipped)} total, showing up to {limit}):")
    for c in skipped[:limit]:
        print(f"    - {c.get('sympathy_ticker','?'):6s}  "
              f"({c.get('reporter','?')})  "
              f"score={c.get('final_score',0):3d}  "
              f"reason: {c.get('skip_reason','?')}")


def _print_source_audit(audit: dict) -> None:
    print(f"\n  Source audit:")
    for k, v in audit.items():
        icon = "✅" if v in ("live", "config_file") else ("⚠️ " if v == "fallback" else "❌")
        print(f"    {icon} {k}: {v}")


def _print_reporter_header(reporter: str, date: str | None, time: str | None) -> None:
    time_str = (time or "").replace("_", " ")
    print(f"\n{'─'*60}")
    print(f"  Reporter: {reporter}  |  Earnings: {date or 'unknown'}  {time_str}")
    print(f"{'─'*60}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-Earnings Sympathy Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--week", choices=["current"], help="Scan all upcoming reporters")
    parser.add_argument("--reporter", action="append", dest="reporters",
                        help="Scan sympathy plays for reporter (repeatable)")
    parser.add_argument("--ticker", action="append", dest="tickers",
                        help="Find reporters that might move this sympathy ticker (repeatable)")
    parser.add_argument("--days", type=int, default=14, help="Look-ahead window (default 14)")
    parser.add_argument("--top", type=int, default=5, help="Top N candidates to show")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    svc = SympathyService()

    print(f"\n{_bar()}")
    print(f"  Pre-Earnings Sympathy Detector")
    print(_bar())

    # ── Week scan ─────────────────────────────────────────────────────────────
    if args.week == "current" or (not args.reporters and not args.tickers):
        print(f"\n  Mode: WEEK SCAN  (next {args.days} days)")
        result = svc.scan_week(days_ahead=args.days)
        print(f"\n  Reporters scanned: {result['reporters_scanned']}")

        passing = result["passing_candidates"]
        skipped = result["skipped_candidates"]

        print(f"\n{_bar()}")
        print(f"  TOP {args.top} CANDIDATES  ({len(passing)} total passed)")
        print(_bar())

        if not passing:
            print("\n  No candidates passed all filters this run.")
            print("  Likely causes: market closed, no Schwab options data, or no setups qualify.")
        for i, c in enumerate(passing[:args.top], 1):
            _print_candidate(c, i)

        _print_skipped(skipped)
        _print_source_audit(result["source_audit"])
        print(f"\n  Run ID: {result['run_id']}")

    # ── Reporter scan ─────────────────────────────────────────────────────────
    if args.reporters:
        for reporter in args.reporters:
            result = svc.scan_reporter(reporter.upper())
            _print_reporter_header(reporter, result.get("earnings_date"), result.get("earnings_time"))
            print(f"  Sympathy tickers scanned: {', '.join(result['sympathy_tickers_scanned'])}")

            passing = result["passing_candidates"]
            skipped = result["skipped_candidates"]

            print(f"\n  Passing candidates: {len(passing)}")
            if not passing:
                print("  No candidates passed filters.")
            for i, c in enumerate(passing[:args.top], 1):
                _print_candidate(c, i)

            _print_skipped(skipped, limit=10)
            _print_source_audit(result["source_audit"])

            llm = result.get("llm_result", {}).get("llm_status", {})
            if llm.get("degraded_mode"):
                print(f"\n  LLM: degraded ({llm.get('reason','')})")
            print(f"  Run ID: {result['run_id']}")

    # ── Ticker scan ───────────────────────────────────────────────────────────
    if args.tickers:
        for ticker in args.tickers:
            result = svc.scan_ticker(ticker.upper())
            print(f"\n{_bar()}")
            print(f"  TICKER SCAN: {ticker.upper()}")
            print(f"  Reporters that might move it: {', '.join(result['reporters_checked']) or 'none found in calendar'}")
            print(_bar())

            passing = result["passing_candidates"]
            skipped = result["skipped_candidates"]

            if not passing:
                print("\n  No candidates passed filters for this ticker.")
            for i, c in enumerate(passing[:args.top], 1):
                _print_candidate(c, i)

            _print_skipped(skipped, limit=5)
            _print_source_audit(result["source_audit"])

    print(f"\n{_bar()}\n")


if __name__ == "__main__":
    main()
