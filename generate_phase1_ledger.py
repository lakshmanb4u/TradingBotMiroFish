#!/usr/bin/env python3
"""
Generate Phase 1 Alert Ledger from no-lookahead-safe replay results.
Consolidates all full_runner replay trades into a comprehensive alert ledger.
"""

import pandas as pd
import json
import glob
import os
from datetime import datetime
from pathlib import Path

# Base paths
WORKSPACE = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace")
REPLAY_BASE = WORKSPACE / "market-swarm-lab/state/backtests/replay"
EXPORTS_DIR = WORKSPACE / "exports"
REPORTS_DIR = WORKSPACE / "reports"

# Create directories if they don't exist
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def parse_iso_timestamp(ts_str):
    """Parse ISO8601 timestamp with timezone to UTC and ET."""
    try:
        # Remove timezone info and parse
        if 'T' in ts_str:
            dt_str = ts_str.split('+')[0].split('-')
            # Reconstruct the datetime
            from datetime import datetime as dt
            dt_obj = dt.fromisoformat(ts_str.replace('Z', '+00:00'))
            utc_ts = dt_obj.isoformat()
            
            # Convert to ET (simple approximation)
            from datetime import timedelta, timezone
            et_offset = timedelta(hours=-4)  # EDT
            et_ts = (dt_obj + et_offset).isoformat()
            
            return utc_ts, et_ts
        return ts_str, ts_str
    except:
        return ts_str, ts_str

def get_outcome(exit_reason):
    """Map exit reason to outcome."""
    reason_map = {
        'target_1': 'TARGET1_HIT',
        'target_2': 'TARGET2_HIT',
        'stop': 'STOP_HIT',
        'eod': 'TIMEOUT',
        'timeout': 'TIMEOUT'
    }
    return reason_map.get(exit_reason, 'TIMEOUT')

def calculate_holding_time(entry_ts, exit_ts):
    """Calculate holding time in seconds."""
    try:
        entry = pd.to_datetime(entry_ts)
        exit_t = pd.to_datetime(exit_ts)
        diff = (exit_t - entry).total_seconds()
        return int(diff)
    except:
        return 0

def calculate_r_multiple(entry_price, stop_loss, actual_exit_price, side):
    """Calculate R multiple."""
    try:
        if side.upper() == 'BUY' or 'BUY' in side.upper():
            risk = abs(entry_price - stop_loss)
            profit = actual_exit_price - entry_price
            if risk > 0:
                return round(profit / risk, 4)
        else:  # SHORT/SELL
            risk = abs(stop_loss - entry_price)
            profit = entry_price - actual_exit_price
            if risk > 0:
                return round(profit / risk, 4)
        return 0.0
    except:
        return 0.0

def load_all_trades():
    """Load all trades from full_runner replays."""
    trade_dirs = sorted(glob.glob(str(REPLAY_BASE / "*_full_runner")))
    
    all_trades = []
    alert_id = 1
    
    for trade_dir in trade_dirs:
        trades_file = Path(trade_dir) / "trades.csv"
        if trades_file.exists():
            df = pd.read_csv(trades_file)
            
            # Add alert_id and convert each row
            for idx, row in df.iterrows():
                trade_dict = row.to_dict()
                trade_dict['alert_id'] = f"PHASE1_{alert_id:05d}"
                trade_dict['_row_index'] = idx
                all_trades.append(trade_dict)
                alert_id += 1
    
    return pd.DataFrame(all_trades)

def build_ledger_row(trade_row):
    """Build a complete ledger row with all required fields."""
    
    # Extract basic fields
    alert_id = trade_row['alert_id']
    symbol = trade_row['ticker']
    side = 'BUY' if 'BUY' in str(trade_row['action']).upper() else 'SELL'
    
    # Parse timestamps
    alert_ts_utc, alert_ts_et = parse_iso_timestamp(str(trade_row['signal_ts']))
    entry_ts_utc, entry_ts_et = parse_iso_timestamp(str(trade_row['entry_ts']))
    exit_ts_utc, exit_ts_et = parse_iso_timestamp(str(trade_row['exit_ts']))
    
    # Prices
    entry_price = float(trade_row['entry_price'])
    stop = float(trade_row['stop_loss'])
    target1 = float(trade_row['target_1'])
    target2 = float(trade_row['target_2'])
    exit_price = float(trade_row['exit_price'])
    
    # Outcome
    outcome = get_outcome(str(trade_row['exit_reason']))
    
    # R-multiple
    r_multiple = calculate_r_multiple(entry_price, stop, exit_price, side)
    
    # Holding time
    holding_seconds = calculate_holding_time(entry_ts_utc, exit_ts_utc)
    
    # MFE/MAE
    mfe = float(trade_row.get('mfe', 0))
    mae = float(trade_row.get('mae', 0))
    
    # Confidence (extract percentage)
    confidence_str = str(trade_row.get('confidence', '0%')).rstrip('%')
    try:
        confidence = int(confidence_str) / 100.0
    except:
        confidence = 0.0
    
    # Scores (using votes as proxy)
    votes_bull = int(trade_row.get('votes_bull', 0))
    votes_bear = int(trade_row.get('votes_bear', 0))
    
    # Calculate scores
    total_votes = votes_bull + votes_bear
    tape_acceleration_score = votes_bull / max(total_votes, 1) if side == 'BUY' else votes_bear / max(total_votes, 1)
    continuation_quality_score = confidence
    participation_ratio = total_votes / 3.0  # Max 3 votes
    
    # Regime and reason
    regime = str(trade_row.get('regime', 'UNKNOWN'))
    reason_codes = str(trade_row.get('uw_bias', 'neutral')).upper()
    
    return {
        'alert_id': alert_id,
        'symbol': symbol,
        'side': side,
        'alert_timestamp_utc': alert_ts_utc,
        'alert_timestamp_et': alert_ts_et,
        'entry_timestamp_utc': entry_ts_utc,
        'entry_timestamp_et': entry_ts_et,
        'entry_price': round(entry_price, 2),
        'stop': round(stop, 2),
        'target1': round(target1, 2),
        'target2': round(target2, 2),
        'exit_timestamp_utc': exit_ts_utc,
        'exit_timestamp_et': exit_ts_et,
        'exit_price': round(exit_price, 2),
        'outcome': outcome,
        'r_multiple': r_multiple,
        'holding_seconds': holding_seconds,
        'mfe': round(mfe, 4),
        'mae': round(mae, 4),
        'confidence': round(confidence, 4),
        'tape_acceleration_score': round(tape_acceleration_score, 4),
        'continuation_quality_score': round(continuation_quality_score, 4),
        'participation_ratio': round(participation_ratio, 4),
        'regime': regime,
        'reason_codes': reason_codes
    }

def main():
    print("[Phase 1 Ledger] Loading all replay trades...")
    
    # Load trades
    trades_df = load_all_trades()
    print(f"[Phase 1 Ledger] Loaded {len(trades_df)} trades from replay")
    
    if len(trades_df) == 0:
        print("[ERROR] No trades found!")
        return
    
    # Build ledger
    print("[Phase 1 Ledger] Building alert ledger...")
    ledger_rows = []
    for idx, row in trades_df.iterrows():
        try:
            ledger_row = build_ledger_row(row)
            ledger_rows.append(ledger_row)
        except Exception as e:
            print(f"[ERROR] Failed to process row {idx}: {e}")
    
    ledger_df = pd.DataFrame(ledger_rows)
    
    # Export to CSV
    csv_path = EXPORTS_DIR / "phase1_alert_ledger.csv"
    ledger_df.to_csv(csv_path, index=False)
    print(f"[Phase 1 Ledger] Exported ledger to {csv_path}")
    print(f"[Phase 1 Ledger] Total rows: {len(ledger_df)}")
    
    # Calculate summary statistics
    wins = len(ledger_df[ledger_df['outcome'].isin(['TARGET1_HIT', 'TARGET2_HIT'])])
    losses = len(ledger_df[ledger_df['outcome'] == 'STOP_HIT'])
    timeouts = len(ledger_df[ledger_df['outcome'] == 'TIMEOUT'])
    
    win_rate = wins / len(ledger_df) if len(ledger_df) > 0 else 0
    total_r = ledger_df['r_multiple'].sum()
    avg_r = ledger_df['r_multiple'].mean()
    avg_holding = ledger_df['holding_seconds'].mean()
    
    # Profit factor (simplified)
    winning_r = ledger_df[ledger_df['r_multiple'] > 0]['r_multiple'].sum()
    losing_r = abs(ledger_df[ledger_df['r_multiple'] < 0]['r_multiple'].sum())
    profit_factor = winning_r / max(losing_r, 0.01)
    
    # Find best and worst
    best_alert = ledger_df.loc[ledger_df['r_multiple'].idxmax()] if len(ledger_df) > 0 else None
    worst_alert = ledger_df.loc[ledger_df['r_multiple'].idxmin()] if len(ledger_df) > 0 else None
    
    summary_stats = {
        'total_alerts': len(ledger_df),
        'wins': wins,
        'losses': losses,
        'timeouts': timeouts,
        'win_rate': round(win_rate, 4),
        'profit_factor': round(profit_factor, 4),
        'avg_r': round(avg_r, 4),
        'total_r': round(total_r, 4),
        'avg_holding_seconds': round(avg_holding, 0),
        'best_alert_r': round(best_alert['r_multiple'], 4) if best_alert is not None else 0,
        'worst_alert_r': round(worst_alert['r_multiple'], 4) if worst_alert is not None else 0,
    }
    
    # Generate examples markdown
    print("[Phase 1 Ledger] Generating examples report...")
    examples_md = "# Phase 1 Alert Examples\n\n"
    examples_md += f"## Summary\n\n"
    examples_md += f"- **Total Alerts:** {summary_stats['total_alerts']}\n"
    examples_md += f"- **Wins:** {summary_stats['wins']}\n"
    examples_md += f"- **Losses:** {summary_stats['losses']}\n"
    examples_md += f"- **Timeouts:** {summary_stats['timeouts']}\n"
    examples_md += f"- **Win Rate:** {summary_stats['win_rate']:.2%}\n"
    examples_md += f"- **Profit Factor:** {summary_stats['profit_factor']:.2f}\n"
    examples_md += f"- **Avg R:** {summary_stats['avg_r']:.4f}\n"
    examples_md += f"- **Total R:** {summary_stats['total_r']:.4f}\n"
    examples_md += f"- **Avg Holding Time:** {summary_stats['avg_holding_seconds']:.0f}s\n\n"
    
    examples_md += "## Sample Alerts (First 10)\n\n"
    for idx, alert in ledger_df.head(10).iterrows():
        examples_md += f"### Alert {alert['alert_id']}\n\n"
        examples_md += f"- **Symbol:** {alert['symbol']}\n"
        examples_md += f"- **Side:** {alert['side']}\n"
        examples_md += f"- **Entry:** {alert['entry_timestamp_et']} @ {alert['entry_price']}\n"
        examples_md += f"- **Exit:** {alert['exit_timestamp_et']} @ {alert['exit_price']}\n"
        examples_md += f"- **Stop/Target:** {alert['stop']}/{alert['target1']}/{alert['target2']}\n"
        examples_md += f"- **Outcome:** {alert['outcome']}\n"
        examples_md += f"- **R-Multiple:** {alert['r_multiple']}\n"
        examples_md += f"- **Confidence:** {alert['confidence']:.2%}\n"
        examples_md += f"- **Holding:** {alert['holding_seconds']}s\n"
        examples_md += f"- **MFE/MAE:** {alert['mfe']}/{alert['mae']}\n"
        examples_md += f"- **Regime:** {alert['regime']}\n\n"
    
    examples_path = REPORTS_DIR / "phase1_alert_examples.md"
    with open(examples_path, 'w') as f:
        f.write(examples_md)
    print(f"[Phase 1 Ledger] Generated examples to {examples_path}")
    
    # Generate entry/exit summary
    print("[Phase 1 Ledger] Generating entry/exit summary...")
    summary_md = "# Phase 1 Entry/Exit Summary\n\n"
    summary_md += f"## Overall Summary\n\n"
    summary_md += f"- **Total Alerts:** {summary_stats['total_alerts']}\n"
    summary_md += f"- **Winners (TARGET1 + TARGET2):** {summary_stats['wins']} ({summary_stats['win_rate']:.2%})\n"
    summary_md += f"- **Losers (STOP_HIT):** {summary_stats['losses']}\n"
    summary_md += f"- **Timeouts:** {summary_stats['timeouts']}\n"
    summary_md += f"- **Profit Factor:** {summary_stats['profit_factor']:.2f}\n"
    summary_md += f"- **Avg R per Trade:** {summary_stats['avg_r']:.4f}\n"
    summary_md += f"- **Total R Across All Trades:** {summary_stats['total_r']:.4f}\n"
    summary_md += f"- **Avg Holding Time:** {int(summary_stats['avg_holding_seconds'])} seconds (~{int(summary_stats['avg_holding_seconds']/60)} minutes)\n\n"
    
    summary_md += f"## Best Alert\n\n"
    if best_alert is not None:
        summary_md += f"- **ID:** {best_alert['alert_id']}\n"
        summary_md += f"- **Symbol/Side:** {best_alert['symbol']} {best_alert['side']}\n"
        summary_md += f"- **Entry:** {best_alert['entry_price']} @ {best_alert['entry_timestamp_et']}\n"
        summary_md += f"- **Exit:** {best_alert['exit_price']} @ {best_alert['exit_timestamp_et']}\n"
        summary_md += f"- **R-Multiple:** {best_alert['r_multiple']}\n"
        summary_md += f"- **Outcome:** {best_alert['outcome']}\n\n"
    
    summary_md += f"## Worst Alert\n\n"
    if worst_alert is not None:
        summary_md += f"- **ID:** {worst_alert['alert_id']}\n"
        summary_md += f"- **Symbol/Side:** {worst_alert['symbol']} {worst_alert['side']}\n"
        summary_md += f"- **Entry:** {worst_alert['entry_price']} @ {worst_alert['entry_timestamp_et']}\n"
        summary_md += f"- **Exit:** {worst_alert['exit_price']} @ {worst_alert['exit_timestamp_et']}\n"
        summary_md += f"- **R-Multiple:** {worst_alert['r_multiple']}\n"
        summary_md += f"- **Outcome:** {worst_alert['outcome']}\n\n"
    
    summary_md += "## Outcome Breakdown\n\n"
    outcome_counts = ledger_df['outcome'].value_counts()
    for outcome, count in outcome_counts.items():
        pct = (count / len(ledger_df)) * 100
        summary_md += f"- **{outcome}:** {count} ({pct:.1f}%)\n"
    
    summary_path = REPORTS_DIR / "phase1_entry_exit_summary.md"
    with open(summary_path, 'w') as f:
        f.write(summary_md)
    print(f"[Phase 1 Ledger] Generated summary to {summary_path}")
    
    # Return summary for WhatsApp output
    return summary_stats, ledger_df.head(10)

if __name__ == "__main__":
    summary_stats, sample_alerts = main()
    
    # Print WhatsApp-formatted summary
    print("\n" + "="*60)
    print("PHASE 1 ALERT LEDGER SUMMARY (WhatsApp Format)")
    print("="*60)
    print(f"\n📊 TOTAL: {summary_stats['total_alerts']} alerts")
    print(f"✅ WINS: {summary_stats['wins']} | ❌ LOSSES: {summary_stats['losses']} | ⏱️ TIMEOUTS: {summary_stats['timeouts']}")
    print(f"📈 WIN RATE: {summary_stats['win_rate']:.1%}")
    print(f"💰 PROFIT FACTOR: {summary_stats['profit_factor']:.2f}")
    print(f"📊 AVG R: {summary_stats['avg_r']:.4f} | TOTAL R: {summary_stats['total_r']:.2f}")
    print(f"⏳ AVG HOLDING: {int(summary_stats['avg_holding_seconds']/60)}m {int(summary_stats['avg_holding_seconds']%60)}s")
    print(f"🎯 BEST: +{summary_stats['best_alert_r']} | 📉 WORST: {summary_stats['worst_alert_r']}")
    print("\n" + "="*60)
