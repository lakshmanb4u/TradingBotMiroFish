#!/usr/bin/env python3
"""MiroFish Signal CLI — run a decisive BUY/SELL/HOLD signal for any ticker.

Usage:
    python3 mirofish_signal.py SPY
    python3 mirofish_signal.py ARM CAR NVDA TSLA

Output: clear signal per ticker with entry, target, stop, R:R, confidence.
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SERVICE_DIRS = [
    ROOT / "services" / "schwab-collector",
    ROOT / "services" / "uw-collector",
    ROOT / "services" / "forecasting",
    ROOT / "services" / "strategy-engine",
    ROOT / "services" / "price-collector",
]
for sd in SERVICE_DIRS:
    sp = str(sd)
    if sp not in sys.path:
        sys.path.insert(0, sp)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING)

from schwab_price_service import SchwabPriceService
from schwab_intraday_service import SchwabIntradayService
from uw_collector_service import UWCollectorService
from forecasting_service import TimesFMForecastingService
from signal_scorer import score_ticker
try:
    from ensemble_scorer import ensemble_score
    _ENSEMBLE = True
except Exception:
    _ENSEMBLE = False
try:
    sys.path.insert(0, str(ROOT / "services" / "agent-seeder"))
    from masi_agent import run_masi_agent, _fetch_futures_bars, _fetch_futures_quote, _compute_vwap
    _MASI_AGENT = True
except Exception:
    _MASI_AGENT = False


def run(ticker: str) -> dict:
    ticker = ticker.upper()
    print(f"  Fetching {ticker}...", end=" ", flush=True)

    price    = SchwabPriceService().collect(ticker)
    intraday = SchwabIntradayService().collect(ticker)
    uw       = UWCollectorService().collect(ticker, current_price=price["last_price"])
    forecast = TimesFMForecastingService().forecast_from_prices(ticker, price["close_prices"])

    result = score_ticker(price, intraday, uw, forecast)
    result["ticker"] = ticker

    # Run ensemble scorer (4 agents vote)
    if _ENSEMBLE and _MASI_AGENT:
        try:
            es_bars  = _fetch_futures_bars("/ES", 5)
            nq_bars  = _fetch_futures_bars("/NQ", 5)
            es_quote = _fetch_futures_quote("/ES")
            nq_quote = _fetch_futures_quote("/NQ")
            es_price = es_quote.get("last", 0)
            nq_price = nq_quote.get("last", 0)
            ens = ensemble_score(price, intraday, es_bars, nq_bars, es_price, nq_price)
            ens["es_price"] = es_price
            ens["nq_price"] = nq_price
            ens["es_vwap"]  = _compute_vwap(es_bars[-40:]) if es_bars else 0
            ens["nq_vwap"]  = _compute_vwap(nq_bars[-40:]) if nq_bars else 0
            result["ensemble"] = ens
        except Exception as e:
            result["ensemble"] = {"action": "ERROR", "reason": str(e)}

    # Run Masi Agent (LLM reading bars + /ES + /NQ)
    if _MASI_AGENT:
        try:
            masi = run_masi_agent(intraday, uw, spx_vwap=intraday["current"]["vwap"])
            result["masi_agent"] = masi
        except Exception as e:
            result["masi_agent"] = {"action": "ERROR", "reason": str(e)}

    print("done")
    return result


def print_signal(r: dict) -> None:
    action = r["ACTION"]
    color  = ""  # plain text for WhatsApp/terminal compatibility

    arrow = "▲" if action == "BUY" else ("▼" if action == "SELL/SHORT" else "—")

    print(f"\n{'='*52}")
    print(f"  {r['ticker']:6s}  ${r['price']:.2f}   {arrow} {action}  ({r['confidence']})")
    print(f"{'='*52}")

    if action != "HOLD":
        print(f"  Entry:      ${r['entry']:.2f}")
        print(f"  Target:     ${r['target']:.2f}")
        print(f"  Stop Loss:  ${r['stop_loss']:.2f}")
        print(f"  Risk/Reward {r['risk_reward']}")
    else:
        print(f"  No trade — signals conflicting or insufficient edge.")

    print(f"\n  Score: {r['score']:+d}  |  Reasons:")
    for reason in r["reasons"]:
        print(f"    • {reason}")

    w = r["why"]
    print(f"\n  Forecast:  {w['forecast']}")
    print(f"  UW Flow:   {w['uw_flow']}")
    print(f"  RSI:       {w['daily_rsi']:.1f}")
    print(f"  Momentum:  {w['momentum']}")
    print(f"  Intraday:  {w['intraday']}")

    # Masi strategies fired
    masi = [s for s in r.get("masi_strategies", []) if s.get("name") != "HIGH_VOLUME_WINDOW"]
    if masi:
        print(f"\n  Masi Strategies ({len(masi)} fired):")
        for s in masi:
            print(f"    + {s['name']}: {s['reason'][:75]}")

    for warn in r.get("masi_warnings", []):
        print(f"  !! {warn}")

    # Masi exit plan
    ep = r.get("masi_exit_plan", {})
    if ep and r["ACTION"] != "HOLD":
        print(f"\n  Masi Exit Plan:")
        print(f"    T1 (70% out): ${ep.get('target_1',0):.2f}  R:R {ep.get('risk_reward_t1','?')}")
        print(f"    T2 (30% runner): ${ep.get('target_2',0):.2f}  R:R {ep.get('risk_reward_t2','?')}")
        print(f"    Stop Loss:    ${ep.get('stop_loss',0):.2f}")

    # Ensemble signal
    ens = r.get("ensemble", {})
    if ens and ens.get("action") not in (None, "ERROR", ""):
        print(f"\n{'='*52}")
        print(f"  ENSEMBLE (4 agents vote — 59.6% backtested)")
        print(f"{'='*52}")
        action_e = ens['action']
        arrow_e = '▲' if action_e=='BUY' else ('▼' if action_e=='SELL/SHORT' else '—')
        print(f"  {arrow_e} {action_e}  ({ens.get('confidence','?')})  Bulls:{ens.get('votes_bull',0)}/4  Bears:{ens.get('votes_bear',0)}/4")
        if action_e != 'HOLD':
            print(f"  Entry:    ${ens.get('entry',0):.2f}")
            print(f"  T1 (70%): ${ens.get('target_1',0):.2f}")
            print(f"  T2 (30%): ${ens.get('target_2',0):.2f}")
            print(f"  Stop:     ${ens.get('stop_loss',0):.2f}  R:R {ens.get('risk_reward','?')}")
        else:
            print(f"  No trade — agents disagree or outside Masi window")
        print(f"\n  Agent votes:")
        for name, res in ens.get('agents', {}).items():
            icon = '✅' if res['vote']=='bull' else ('❌' if res['vote']=='bear' else '◼')
            print(f"    {icon} {name:<22} score={res['score']:+d}  → {res['vote'].upper()}")
        if ens.get('es_price'):
            hv = '✅ In high-vol window' if ens.get('in_hv_window') else '⚠️  Outside high-vol window'
            print(f"  /ES: ${ens.get('es_price','?')} (VWAP ${ens.get('es_vwap',0):.2f})  /NQ: ${ens.get('nq_price','?')} (VWAP ${ens.get('nq_vwap',0):.2f})")
            print(f"  {hv}")

    # Masi Agent (LLM)
    ma = r.get("masi_agent", {})
    if ma and ma.get("action") not in (None, "ERROR", ""):
        print(f"\n{'='*52}")
        print(f"  MASI AGENT (Kimi K2 reads bars + /ES + /NQ)")
        print(f"{'='*52}")
        print(f"  Action:     {ma['action']}")
        print(f"  Setup:      {ma.get('setup','')}")
        print(f"  Entry:      ${ma.get('entry',0):.2f}")
        if ma.get('target_1'): print(f"  T1 (70%):   ${ma['target_1']:.2f}")
        if ma.get('target_2'): print(f"  T2 (30%):   ${ma['target_2']:.2f}")
        if ma.get('stop'):     print(f"  Stop:       ${ma['stop']:.2f}")
        print(f"  Confidence: {ma.get('confidence',0)}%")
        print(f"  Reason:     {ma.get('reason','')}")
        print(f"  /ES:  {ma.get('es_quote',{}).get('last','?')} (VWAP {ma.get('es_vwap','?')})")
        print(f"  /NQ:  {ma.get('nq_quote',{}).get('last','?')} (VWAP {ma.get('nq_vwap','?')})")
    elif ma.get("action") == "ERROR":
        print(f"\n  Masi Agent: error — {ma.get('reason','')}"[:80])


# ── Backtest subcommand ───────────────────────────────────────────────────────

def _run_backtest_cli(argv: list[str]) -> None:
    """Parse and run point-in-time backtest.

    Usage:
      python mirofish_signal.py backtest --ticker SPY --start 2026-04-01 --end 2026-04-25 --timeframe 2min
      python mirofish_signal.py backtest --ticker SPX --start 2026-04-01 --end 2026-04-25 \\
        --timeframe 2min --confirm-with ES,SPY
    """
    import argparse

    bt_dir = ROOT / "services" / "backtest"
    if str(bt_dir) not in sys.path:
        sys.path.insert(0, str(bt_dir))

    parser = argparse.ArgumentParser(
        description="MiroFish Point-in-Time Replay Backtester",
        prog="mirofish_signal.py backtest",
    )
    parser.add_argument("backtest_cmd")  # consumes the 'backtest' literal
    parser.add_argument("--ticker",       required=True, help="SPY | SPX | QQQ")
    parser.add_argument("--start",        required=True, help="YYYY-MM-DD")
    parser.add_argument("--end",          required=True, help="YYYY-MM-DD")
    parser.add_argument("--timeframe",    default="5min", help="2min | 5min | 15min")
    parser.add_argument("--confirm-with", default="",    help="Comma-separated: ES,SPY,QQQ")
    parser.add_argument("--masi",         action="store_true", help="Enable MASi LLM veto")
    parser.add_argument("--no-timesfm",   action="store_true", help="Disable TimesFM")
    parser.add_argument("--debug",        action="store_true")
    args = parser.parse_args(argv)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    confirm = [c.strip().upper() for c in args.confirm_with.split(",") if c.strip()]

    from point_in_time_replay import run_backtest
    result = run_backtest(
        ticker       = args.ticker,
        start        = args.start,
        end          = args.end,
        timeframe    = args.timeframe,
        confirm_with = confirm,
        use_masi     = args.masi,
        use_timesfm  = not args.no_timesfm,
    )

    import json
    print("\n" + "="*60)
    print("  BACKTEST COMPLETE")
    print("="*60)
    summary = result.get("summary", {})
    if summary:
        print(f"  Signals:       {summary.get('total_signals','?')}")
        print(f"  Win rate:      {summary.get('win_rate_pct','?')}%")
        print(f"  Profit factor: {summary.get('profit_factor','?')}")
        print(f"  Total R:       {summary.get('total_r','?')}")
        print(f"  Max DD (R):    {summary.get('max_drawdown_r','?')}")
        print(f"  Options mode:  {result.get('options_pnl_mode','?')}")
    print(f"  Output:        {result.get('output_dir','?')}")
    print("="*60 + "\n")


if __name__ == "__main__":
    # Intercept 'backtest' subcommand before normal signal logic
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        _run_backtest_cli(sys.argv[1:])
        sys.exit(0)

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["SPY"]

    print(f"\nMiroFish Signal Engine")
    print(f"Tickers: {', '.join(t.upper() for t in tickers)}\n")

    for ticker in tickers:
        try:
            result = run(ticker)
            print_signal(result)
        except Exception as exc:
            print(f"\n  {ticker.upper()}: ERROR — {exc}")

    print()
