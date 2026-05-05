"""
Alert formatter for live trading signals.
Produces structured alerts for console, log, WhatsApp, and webhook delivery.
"""

from datetime import datetime
from typing import Any


def format_alert(
    ticker: str,
    action: str,
    underlying_entry: float,
    underlying_stop: float,
    exit_strategy: str,
    confidence: int,
    regime: str,
    votes_bull: int,
    votes_bear: int,
    reason: str,
    risk_notes: list[str],
    human_confirmation_required: bool = True,
    option_contract: dict | None = None,
) -> dict:
    """Format a trade alert as a structured dict."""
    
    votes_total = votes_bull + votes_bear
    votes_str = f"{max(votes_bull, votes_bear)}/{votes_total}"
    
    alert = {
        "timestamp": datetime.now().isoformat(),
        "ticker": ticker,
        "action": action,
        "underlying_entry": round(underlying_entry, 2),
        "underlying_stop": round(underlying_stop, 2),
        "exit_strategy": exit_strategy,
        "confidence": confidence,
        "regime": regime,
        "votes": votes_str,
        "reason": reason,
        "risk_notes": risk_notes or [],
        "human_confirmation_required": human_confirmation_required,
    }
    
    if option_contract:
        alert["option_contract"] = option_contract
    
    return alert


def format_alert_text(alert: dict) -> str:
    """Format alert as human-readable text for WhatsApp/console."""
    lines = [
        f"🚨 TRADE ALERT: {alert['ticker']}",
        f"",
        f"Action: {alert['action']}",
        f"Entry: ${alert['underlying_entry']}",
        f"Stop: ${alert['underlying_stop']}",
        f"Strategy: {alert['exit_strategy']}",
        f"Confidence: {alert['confidence']}%",
        f"Regime: {alert['regime']}",
        f"Votes: {alert['votes']}",
        f"",
        f"Reason: {alert['reason']}",
    ]
    
    if alert.get('risk_notes'):
        lines.append("")
        lines.append("Risk Notes:")
        for note in alert['risk_notes']:
            lines.append(f"  • {note}")
    
    if alert.get('option_contract'):
        opt = alert['option_contract']
        lines.append("")
        lines.append(f"Option: {opt.get('symbol', 'N/A')}")
        lines.append(f"  Strike: ${opt.get('strike', 'N/A')}")
        lines.append(f"  Expiry: {opt.get('expiry', 'N/A')}")
        lines.append(f"  Delta: {opt.get('delta', 'N/A')}")
        lines.append(f"  Premium: ${opt.get('premium', 'N/A')}")
    
    if alert.get('human_confirmation_required'):
        lines.append("")
        lines.append("⚠️ HUMAN CONFIRMATION REQUIRED")
        lines.append("Reply CONFIRM to execute paper trade")
    
    return "\n".join(lines)


def format_alert_json(alert: dict) -> str:
    """Format alert as JSON string."""
    import json
    return json.dumps(alert, indent=2)
