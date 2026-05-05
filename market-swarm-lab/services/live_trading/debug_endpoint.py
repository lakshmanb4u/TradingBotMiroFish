"""
Debug endpoint for live trading status.
Provides visibility into current state without exposing sensitive data.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)


class DebugEndpoint:
    """Simple debug endpoint for live trading monitoring."""
    
    def __init__(self, config: dict, state_dir: Path = Path("state/live")):
        self.config = config
        self.state_dir = state_dir
        self.last_signal = None
        self.token_usage = {
            "tokens_today": 0,
            "calls_today": 0,
            "last_call": None,
        }
    
    def update_last_signal(self, alert: dict):
        """Record last generated signal."""
        self.last_signal = {
            "timestamp": datetime.now().isoformat(),
            "ticker": alert.get("ticker"),
            "action": alert.get("action"),
            "confidence": alert.get("confidence"),
            "regime": alert.get("regime"),
        }
    
    def record_token_usage(self, tokens_used: int):
        """Record Kimi token usage."""
        self.token_usage["tokens_today"] += tokens_used
        self.token_usage["calls_today"] += 1
        self.token_usage["last_call"] = datetime.now().isoformat()
    
    def get_status(self) -> dict:
        """Get current live trading status."""
        # Load paper positions if they exist
        positions = {"count": 0, "positions": []}
        positions_file = self.state_dir / "paper_positions.json"
        if positions_file.exists():
            try:
                data = json.loads(positions_file.read_text())
                positions = {
                    "count": len(data.get("open_positions", [])),
                    "positions": data.get("open_positions", []),
                    "account_value": data.get("account_value", 0),
                }
            except Exception as e:
                _log.warning("[debug] Failed to load positions: %s", e)
        
        # Source audit
        source_audit = {
            "intraday_bars": {"provider": "yfinance/schwab", "status": "live"},
            "unusual_whales": {"provider": "local_snapshots", "status": "checked_per_day"},
            "timesfm": {"provider": "local_model", "status": "live"},
            "masi": {"provider": "kimi_k2", "status": "confirm_only"},
        }
        
        return {
            "timestamp": datetime.now().isoformat(),
            "mode": self.config.get("mode", "unknown"),
            "active_tickers": self.config.get("tickers", []),
            "threshold_profile": self.config.get("threshold_profile", "unknown"),
            "exit_strategy": self.config.get("exit_strategy", "unknown"),
            "allow_live_orders": self.config.get("allow_live_orders", False),
            "require_human_confirmation": self.config.get("require_human_confirmation", True),
            "last_signal": self.last_signal,
            "open_paper_positions": positions,
            "source_audit": source_audit,
            "kimi_token_usage": self.token_usage,
            "daily_limits": {
                "max_trades": self.config.get("max_trades_per_day", 3),
                "risk_per_trade_pct": self.config.get("risk_per_trade_pct", 0.5),
            },
        }
    
    def format_status_text(self) -> str:
        """Format status as human-readable text."""
        status = self.get_status()
        
        lines = [
            "=" * 60,
            "MiroFish Live Trading - Debug Status",
            "=" * 60,
            f"Mode: {status['mode']}",
            f"Tickers: {', '.join(status['active_tickers'])}",
            f"Threshold: {status['threshold_profile']}",
            f"Exit Strategy: {status['exit_strategy']}",
            f"Live Orders: {'ENABLED ⚠️' if status['allow_live_orders'] else 'DISABLED ✅'}",
            f"Human Confirmation: {'Required' if status['require_human_confirmation'] else 'Not Required'}",
            "",
            "--- Last Signal ---",
        ]
        
        if status['last_signal']:
            ls = status['last_signal']
            lines.extend([
                f"Time: {ls['timestamp']}",
                f"Ticker: {ls['ticker']}",
                f"Action: {ls['action']}",
                f"Confidence: {ls['confidence']}%",
                f"Regime: {ls['regime']}",
            ])
        else:
            lines.append("No signals yet today")
        
        lines.extend([
            "",
            "--- Paper Positions ---",
            f"Open: {status['open_paper_positions']['count']}",
        ])
        
        if status['open_paper_positions']['count'] > 0:
            for p in status['open_paper_positions']['positions']:
                lines.append(f"  {p.get('action')} {p.get('option_symbol')} x{p.get('contracts', 0)}")
        
        lines.extend([
            "",
            "--- Kimi Usage ---",
            f"Tokens Today: {status['kimi_token_usage']['tokens_today']:,}",
            f"Calls Today: {status['kimi_token_usage']['calls_today']}",
            f"Last Call: {status['kimi_token_usage']['last_call'] or 'Never'}",
            "",
            "--- Daily Limits ---",
            f"Max Trades: {status['daily_limits']['max_trades']}",
            f"Risk/Trade: {status['daily_limits']['risk_per_trade_pct']}%",
            "",
            "--- Source Audit ---",
        ])
        
        for source, info in status['source_audit'].items():
            lines.append(f"{source}: {info['provider']} ({info['status']})")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def save_status(self):
        """Save status to file for external monitoring."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        status_file = self.state_dir / "debug_status.json"
        status_file.write_text(json.dumps(self.get_status(), indent=2))


# Simple HTTP server for debug endpoint (optional)
def create_debug_server(config: dict, port: int = 8765):
    """Create a simple HTTP debug server."""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        
        endpoint = DebugEndpoint(config)
        
        class DebugHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/debug/live-trading":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(endpoint.get_status(), indent=2).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, format, *args):
                # Suppress default logging
                pass
        
        server = HTTPServer(("", port), DebugHandler)
        _log.info("[debug] Debug server running on port %d", port)
        return server
    except ImportError:
        _log.warning("[debug] HTTP server not available")
        return None
