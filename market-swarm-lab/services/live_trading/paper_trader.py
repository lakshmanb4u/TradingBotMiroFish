"""
Paper trading engine for live signals.
Simulates option fills, tracks positions, PnL, and journaling.
"""

import csv
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class PaperTrade:
    """Represents a paper option trade."""
    
    def __init__(
        self,
        alert_id: str,
        ticker: str,
        action: str,
        option_symbol: str,
        strike: float,
        expiry: str,
        entry_premium: float,
        contracts: int,
        underlying_entry: float,
        underlying_stop: float,
        exit_strategy: str,
        timestamp: str,
    ):
        self.alert_id = alert_id
        self.ticker = ticker
        self.action = action
        self.option_symbol = option_symbol
        self.strike = strike
        self.expiry = expiry
        self.entry_premium = entry_premium
        self.contracts = contracts
        self.underlying_entry = underlying_entry
        self.underlying_stop = underlying_stop
        self.exit_strategy = exit_strategy
        self.entry_time = timestamp
        
        self.exit_premium = 0.0
        self.exit_time = ""
        self.exit_reason = ""
        self.pnl_dollars = 0.0
        self.pnl_pct = 0.0
        self.status = "OPEN"
        self.highest_premium = entry_premium
        self.lowest_premium = entry_premium
    
    def mark_to_market(self, current_premium: float, current_time: str):
        """Update unrealized PnL."""
        self.highest_premium = max(self.highest_premium, current_premium)
        self.lowest_premium = min(self.lowest_premium, current_premium)
        
        if self.status != "OPEN":
            return
        
        unrealized = (current_premium - self.entry_premium) * self.contracts * 100
        if self.action == "BUY_PUT":
            unrealized = (self.entry_premium - current_premium) * self.contracts * 100
        
        return unrealized
    
    def close(self, exit_premium: float, exit_time: str, reason: str):
        """Close the position."""
        self.exit_premium = exit_premium
        self.exit_time = exit_time
        self.exit_reason = reason
        self.status = "CLOSED"
        
        if self.action == "BUY_CALL":
            self.pnl_dollars = (exit_premium - self.entry_premium) * self.contracts * 100
        else:  # BUY_PUT
            self.pnl_dollars = (self.entry_premium - exit_premium) * self.contracts * 100
        
        cost = self.entry_premium * self.contracts * 100
        self.pnl_pct = (self.pnl_dollars / cost * 100) if cost > 0 else 0
    
    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "ticker": self.ticker,
            "action": self.action,
            "option_symbol": self.option_symbol,
            "strike": self.strike,
            "expiry": self.expiry,
            "entry_premium": self.entry_premium,
            "contracts": self.contracts,
            "underlying_entry": self.underlying_entry,
            "underlying_stop": self.underlying_stop,
            "exit_strategy": self.exit_strategy,
            "entry_time": self.entry_time,
            "exit_premium": self.exit_premium,
            "exit_time": self.exit_time,
            "exit_reason": self.exit_reason,
            "pnl_dollars": round(self.pnl_dollars, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "status": self.status,
            "highest_premium": self.highest_premium,
            "lowest_premium": self.lowest_premium,
        }


class PaperTrader:
    """Paper trading engine - simulates fills and tracks performance."""
    
    STATE_DIR = Path("state/live")
    
    def __init__(self, config: dict, account_value: float = 100000.0):
        self.config = config
        self.account_value = account_value
        self.positions: list[PaperTrade] = []
        self.trade_history: list[PaperTrade] = []
        self.daily_stats = {
            "trades_today": 0,
            "pnl_today": 0.0,
            "wins_today": 0,
            "losses_today": 0,
        }
        self._load_state()
    
    def _load_state(self):
        """Load existing paper positions from disk."""
        positions_file = self.STATE_DIR / "paper_positions.json"
        if positions_file.exists():
            try:
                data = json.loads(positions_file.read_text())
                for p in data.get("open_positions", []):
                    self.positions.append(self._dict_to_trade(p))
                _log.info("[paper] Loaded %d open positions", len(self.positions))
            except Exception as e:
                _log.warning("[paper] Failed to load state: %s", e)
    
    def _save_state(self):
        """Save current state to disk."""
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        
        state = {
            "timestamp": datetime.now().isoformat(),
            "account_value": self.account_value,
            "open_positions": [p.to_dict() for p in self.positions if p.status == "OPEN"],
            "daily_stats": self.daily_stats,
        }
        
        positions_file = self.STATE_DIR / "paper_positions.json"
        positions_file.write_text(json.dumps(state, indent=2))
    
    def _dict_to_trade(self, data: dict) -> PaperTrade:
        """Reconstruct PaperTrade from dict."""
        trade = PaperTrade(
            alert_id=data["alert_id"],
            ticker=data["ticker"],
            action=data["action"],
            option_symbol=data["option_symbol"],
            strike=data["strike"],
            expiry=data["expiry"],
            entry_premium=data["entry_premium"],
            contracts=data["contracts"],
            underlying_entry=data["underlying_entry"],
            underlying_stop=data["underlying_stop"],
            exit_strategy=data["exit_strategy"],
            timestamp=data["entry_time"],
        )
        trade.status = data.get("status", "OPEN")
        trade.exit_premium = data.get("exit_premium", 0)
        trade.exit_time = data.get("exit_time", "")
        trade.exit_reason = data.get("exit_reason", "")
        trade.pnl_dollars = data.get("pnl_dollars", 0)
        trade.pnl_pct = data.get("pnl_pct", 0)
        return trade
    
    def can_trade_today(self) -> bool:
        """Check if we've hit daily trade limits."""
        max_trades = self.config.get("max_trades_per_day", 3)
        return self.daily_stats["trades_today"] < max_trades
    
    def calculate_position_size(self, premium: float) -> int:
        """Calculate number of contracts based on risk rules."""
        risk_pct = self.config.get("risk_per_trade_pct", 0.5)
        max_risk = self.account_value * (risk_pct / 100)
        contract_cost = premium * 100  # 100 shares per contract
        
        if contract_cost <= 0:
            return 0
        
        contracts = int(max_risk / contract_cost)
        return max(1, contracts)  # At least 1 contract
    
    def open_position(self, alert: dict, option_contract: dict) -> PaperTrade | None:
        """Open a new paper position from an alert."""
        if not self.can_trade_today():
            _log.warning("[paper] Daily trade limit reached")
            return None
        
        premium = option_contract.get("premium", 0)
        if premium <= 0:
            _log.warning("[paper] Invalid premium: %s", premium)
            return None
        
        contracts = self.calculate_position_size(premium)
        if contracts <= 0:
            _log.warning("[paper] Position size too small")
            return None
        
        trade = PaperTrade(
            alert_id=alert.get("timestamp", datetime.now().isoformat()),
            ticker=alert["ticker"],
            action=alert["action"],
            option_symbol=option_contract.get("symbol", ""),
            strike=option_contract.get("strike", 0),
            expiry=option_contract.get("expiry", ""),
            entry_premium=premium,
            contracts=contracts,
            underlying_entry=alert["underlying_entry"],
            underlying_stop=alert["underlying_stop"],
            exit_strategy=alert["exit_strategy"],
            timestamp=datetime.now().isoformat(),
        )
        
        self.positions.append(trade)
        self.daily_stats["trades_today"] += 1
        self._save_state()
        self._journal_trade(trade, "OPEN")
        
        _log.info("[paper] Opened %s %s x%d @ $%.2f", 
                  trade.action, trade.option_symbol, trade.contracts, trade.entry_premium)
        return trade
    
    def close_position(self, trade: PaperTrade, exit_premium: float, reason: str):
        """Close an existing position."""
        trade.close(exit_premium, datetime.now().isoformat(), reason)
        self.trade_history.append(trade)
        
        # Update daily stats
        self.daily_stats["pnl_today"] += trade.pnl_dollars
        if trade.pnl_dollars > 0:
            self.daily_stats["wins_today"] += 1
        else:
            self.daily_stats["losses_today"] += 1
        
        self._save_state()
        self._journal_trade(trade, "CLOSE")
        
        _log.info("[paper] Closed %s PnL: $%.2f (%.1f%%) Reason: %s",
                  trade.option_symbol, trade.pnl_dollars, trade.pnl_pct, reason)
    
    def update_positions(self, current_prices: dict[str, float]):
        """Mark all open positions to market."""
        for trade in self.positions:
            if trade.status != "OPEN":
                continue
            
            # Estimate current option premium from underlying
            # Very rough: assume 0.5 delta for estimation
            underlying = current_prices.get(trade.ticker, 0)
            if underlying <= 0:
                continue
            
            # Simple intrinsic + time value estimate
            if trade.action == "BUY_CALL":
                intrinsic = max(0, underlying - trade.strike)
            else:
                intrinsic = max(0, trade.strike - underlying)
            
            # Rough time value (simplified)
            time_value = trade.entry_premium * 0.3  # Assume 30% time value decay
            estimated_premium = intrinsic + time_value
            
            trade.mark_to_market(estimated_premium, datetime.now().isoformat())
    
    def get_open_positions_summary(self) -> dict:
        """Get summary of open positions."""
        open_positions = [p for p in self.positions if p.status == "OPEN"]
        total_unrealized = sum(
            (p.highest_premium - p.entry_premium) * p.contracts * 100
            if p.action == "BUY_CALL" else
            (p.entry_premium - p.lowest_premium) * p.contracts * 100
            for p in open_positions
        )
        
        return {
            "count": len(open_positions),
            "positions": [p.to_dict() for p in open_positions],
            "total_unrealized_pnl": round(total_unrealized, 2),
        }
    
    def _journal_trade(self, trade: PaperTrade, event: str):
        """Write trade to journal CSV."""
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        journal_file = self.STATE_DIR / "trade_journal.csv"
        
        fieldnames = [
            "timestamp", "event", "alert_id", "ticker", "action",
            "option_symbol", "strike", "expiry", "contracts",
            "entry_premium", "exit_premium", "pnl_dollars", "pnl_pct",
            "status", "exit_reason", "underlying_entry", "underlying_stop",
        ]
        
        file_exists = journal_file.exists()
        with open(journal_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            
            writer.writerow({
                "timestamp": datetime.now().isoformat(),
                "event": event,
                "alert_id": trade.alert_id,
                "ticker": trade.ticker,
                "action": trade.action,
                "option_symbol": trade.option_symbol,
                "strike": trade.strike,
                "expiry": trade.expiry,
                "contracts": trade.contracts,
                "entry_premium": trade.entry_premium,
                "exit_premium": trade.exit_premium if event == "CLOSE" else "",
                "pnl_dollars": trade.pnl_dollars if event == "CLOSE" else "",
                "pnl_pct": trade.pnl_pct if event == "CLOSE" else "",
                "status": trade.status,
                "exit_reason": trade.exit_reason if event == "CLOSE" else "",
                "underlying_entry": trade.underlying_entry,
                "underlying_stop": trade.underlying_stop,
            })
    
    def write_daily_summary(self):
        """Write daily summary markdown."""
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        summary_file = self.STATE_DIR / f"daily_summary_{today}.md"
        
        lines = [
            f"# Daily Summary: {today}",
            "",
            f"**Account Value:** ${self.account_value:,.2f}",
            f"**Trades Today:** {self.daily_stats['trades_today']}",
            f"**Wins:** {self.daily_stats['wins_today']}",
            f"**Losses:** {self.daily_stats['losses_today']}",
            f"**PnL Today:** ${self.daily_stats['pnl_today']:,.2f}",
            "",
            "## Open Positions",
            "",
        ]
        
        open_positions = [p for p in self.positions if p.status == "OPEN"]
        if open_positions:
            for p in open_positions:
                lines.append(f"- {p.action} {p.option_symbol} x{p.contracts} @ ${p.entry_premium}")
        else:
            lines.append("No open positions")
        
        lines.extend(["", "## Closed Trades", ""])
        
        closed_today = [p for p in self.trade_history 
                       if p.exit_time.startswith(today)]
        if closed_today:
            for p in closed_today:
                emoji = "🟢" if p.pnl_dollars > 0 else "🔴"
                lines.append(
                    f"- {emoji} {p.option_symbol} PnL: ${p.pnl_dollars:,.2f} ({p.pnl_pct:.1f}%) "
                    f"Reason: {p.exit_reason}"
                )
        else:
            lines.append("No closed trades today")
        
        summary_file.write_text("\n".join(lines))
