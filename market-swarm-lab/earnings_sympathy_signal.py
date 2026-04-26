#!/usr/bin/env python3
"""Pre-Earnings Sympathy Detector CLI.

Usage:
    python earnings_sympathy_signal.py --week current
    python earnings_sympathy_signal.py --reporter INTC
    python earnings_sympathy_signal.py --ticker AMD
    python earnings_sympathy_signal.py --reporter MSFT --reporter GOOGL
    python earnings_sympathy_signal.py backtest --as-of 2026-04-19 --reporter INTC --sympathy AMD

Options:
    --week current      Scan all upcoming reporters this week (default)
    --reporter TICKER   Scan sympathy plays for a specific reporter
    --ticker TICKER     Scan which reporters might move this sympathy ticker
    --days N            Look-ahead window in days (default: 14)
    --top N             Show top N candidates (default: 5)
    --debug             Enable debug logging

Backtest subcommand:
    backtest            Point-in-time replay with zero lookahead bias
    --as-of DATE        Replay date (YYYY-MM-DD)
    --reporter TICKER   The reporting company
    --sympathy TICKER   The sympathy ticker to evaluate
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

# Lazy import for backtest (heavy deps)
def _load_backtest():
    from backtest_replay import BacktestReplayEngine
    return BacktestReplayEngine()


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

def _run_backtest(args: argparse.Namespace) -> None:
    """Run the point-in-time backtest replay."""
    if not args.as_of:
        print("ERROR: --as-of DATE required for backtest")
        sys.exit(1)
    if not args.reporter:
        print("ERROR: --reporter TICKER required for backtest")
        sys.exit(1)
    if not args.sympathy:
        print("ERROR: --sympathy TICKER required for backtest")
        sys.exit(1)

    print(f"\n{_bar()}")
    print(f"  Pre-Earnings Sympathy BACKTEST")
    print(f"  Replay date : {args.as_of}")
    print(f"  Reporter    : {args.reporter[0].upper()}")
    print(f"  Sympathy    : {args.sympathy.upper()}")
    print(f"  ⚠️  No lookahead bias — all data clamped to as-of date")
    print(_bar())

    engine = _load_backtest()
    result = engine.run(
        as_of=args.as_of,
        reporter=args.reporter[0].upper(),
        sympathy_ticker=args.sympathy.upper(),
    )

    c = result.get("backtest_conclusions", {})
    passing = result.get("passing_candidates", [])
    skipped = result.get("skipped_candidates", [])
    outcomes = result.get("outcomes", [])

    print(f"\n  Underlying price as-of {args.as_of}: ${result.get('underlying_price_as_of','?')}")
    print(f"  Reporter earnings: {result.get('reporter_earnings_date','?')} {result.get('reporter_earnings_time','') or ''}")

    print(f"\n{_bar()}")
    print(f"  WOULD DETECTOR HAVE FIRED?")
    print(_bar())
    print(f"  Calls selected : {'YES ✅' if c.get('would_have_selected_calls') else 'NO ❌'}  ({c.get('n_passing_calls',0)} candidates)")
    print(f"  Puts selected  : {'YES ✅' if c.get('would_have_selected_puts') else 'NO ❌'}  ({c.get('n_passing_puts',0)} candidates)")

    if passing:
        print(f"\n  Passing candidates:")
        for i, can in enumerate(passing[:args.top], 1):
            _print_candidate(can, i)

    _print_skipped(skipped, limit=8)

    # Outcomes
    if outcomes:
        print(f"\n{_bar()}")
        print(f"  ACTUAL OUTCOMES (post-event)")
        print(_bar())
        for o in outcomes:
            if not o.get("outcome_available", True):
                print(f"  {o.get('option_type','?')} ${o.get('strike','?')}: {o.get('reason','no data')}")
                continue
            hits = []
            if o.get("did_it_hit_2x"): hits.append("2x ✅")
            if o.get("did_it_hit_5x"): hits.append("5x ✅")
            if o.get("did_it_hit_10x"): hits.append("10x ✅")
            if o.get("expired_worthless"): hits.append("expired worthless ❌")
            hits_str = "  ".join(hits) if hits else "below 2x"
            tp = o.get("take_profit_simulation", {})
            print(f"\n  {o.get('option_type','?')} ${o.get('strike','?')} exp {o.get('expiry','?')}")
            print(f"     Entry: ${o.get('option_entry_price','?')}  |  MFE: {o.get('max_favorable_excursion_multiple','?')}x  |  {hits_str}")
            if tp:
                print(f"     TP simulation: net {tp.get('net_return_multiple','?')}x  (${tp.get('total_realized_pnl','?')} on ${tp.get('cost_basis','?')} cost)")
            for label, wd in (o.get("outcomes_by_window") or {}).items():
                print(f"     {label:20s}: underlying ${wd.get('underlying_exit','?')} (+{wd.get('underlying_move_pct','?')}%)  option est ${wd.get('option_exit_price','?')} ({wd.get('return_multiple','?')}x)")

    # Conclusions
    print(f"\n{_bar()}")
    print(f"  CONCLUSIONS")
    print(_bar())
    print(f"  Lookahead bias: {c.get('lookahead_bias_check','?')}")
    print(f"  Best outcome multiple: {c.get('best_outcome_multiple','?')}x  ({c.get('best_outcome_type','?')} ${c.get('best_outcome_strike','?')})")
    print(f"  Any hit 2x:  {'YES ✅' if c.get('any_hit_2x') else 'NO ❌'}")
    print(f"  Any hit 5x:  {'YES ✅' if c.get('any_hit_5x') else 'NO ❌'}")
    print(f"  Any hit 10x: {'YES ✅' if c.get('any_hit_10x') else 'NO ❌'}")
    if c.get("synthetic_data_warning"):
        print(f"  ⚠️  SYNTHETIC DATA: {c.get('caveat','')}")
    if not c.get("uw_data_available"):
        print(f"  ⚠️  UW flow data unavailable for historical date — weight redistributed")

    # Source audit
    _print_source_audit({
        k: v.get("status", "?") for k, v in result.get("source_audits", {}).items()
    })

    run_key = result.get("run_key", "")
    if run_key:
        out_dir = ROOT / "state" / "backtests" / "earnings_sympathy" / run_key
        print(f"\n  Full report: {out_dir}/backtest_report.md")
    print(f"\n{_bar()}\n")


def main() -> None:
    # Check for backtest subcommand first
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        bt_parser = argparse.ArgumentParser(description="Backtest replay")
        bt_parser.add_argument("subcommand")
        bt_parser.add_argument("--as-of", required=True, help="Replay date YYYY-MM-DD")
        bt_parser.add_argument("--reporter", action="append", required=True)
        bt_parser.add_argument("--sympathy", required=True, help="Sympathy ticker")
        bt_parser.add_argument("--top", type=int, default=5)
        bt_parser.add_argument("--debug", action="store_true")
        bt_args = bt_parser.parse_args()
        logging.basicConfig(
            level=logging.DEBUG if bt_args.debug else logging.WARNING,
            format="%(levelname)s  %(name)s  %(message)s",
        )
        _run_backtest(bt_args)
        return

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
