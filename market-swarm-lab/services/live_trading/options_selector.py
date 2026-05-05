"""
Options contract selector for live trading.
Selects liquid SPY/QQQ/NVDA options based on signal parameters.
"""

from datetime import date, datetime, timedelta
from typing import Any
import logging

_log = logging.getLogger(__name__)


class OptionsSelector:
    """Select appropriate options contracts for signals."""
    
    def __init__(self, config: dict):
        self.config = config.get("options", {})
        self.risk_config = config
    
    def select_contract(
        self,
        ticker: str,
        action: str,
        underlying_price: float,
        option_chain: list[dict] | None = None,
    ) -> dict | None:
        """
        Select best option contract for the signal.
        
        Args:
            ticker: Underlying ticker
            action: BUY_CALL or BUY_PUT
            underlying_price: Current underlying price
            option_chain: Optional pre-fetched option chain from Schwab
        
        Returns:
            Contract dict or None if no suitable contract found
        """
        is_call = action == "BUY_CALL"
        
        # Determine expiry
        dte = self._select_dte()
        expiry = self._calculate_expiry(dte)
        
        # Determine strike (ATM + slight directional bias)
        strike = self._select_strike(underlying_price, is_call)
        
        # If we have a real option chain, filter and rank
        if option_chain:
            return self._select_from_chain(
                option_chain, is_call, strike, expiry, underlying_price
            )
        
        # Fallback: synthetic contract info
        return self._synthetic_contract(ticker, is_call, strike, expiry, underlying_price)
    
    def _select_dte(self) -> int:
        """Select days to expiration based on config and time of day."""
        now = datetime.now()
        market_close = now.replace(hour=16, minute=0, second=0)
        
        # If it's after 2 PM, use 1DTE instead of 0DTE for safety
        if now.hour >= 14 and self.config.get("use_0dte_intraday_only", True):
            return min(1, self.config.get("max_dte", 2))
        
        return self.config.get("default_dte", 1)
    
    def _calculate_expiry(self, dte: int) -> str:
        """Calculate expiry date (next trading day)."""
        expiry = date.today() + timedelta(days=dte)
        # Skip weekends
        while expiry.weekday() >= 5:
            expiry += timedelta(days=1)
        return expiry.isoformat()
    
    def _select_strike(self, underlying: float, is_call: bool) -> float:
        """Select strike price (ATM with slight bias)."""
        # Round to nearest standard strike increment
        if underlying >= 500:
            increment = 5.0
        elif underlying >= 100:
            increment = 1.0
        else:
            increment = 0.5
        
        # For calls, slightly OTM; for puts, slightly OTM
        if is_call:
            strike = round(underlying * 1.01 / increment) * increment
        else:
            strike = round(underlying * 0.99 / increment) * increment
        
        return strike
    
    def _select_from_chain(
        self,
        chain: list[dict],
        is_call: bool,
        target_strike: float,
        target_expiry: str,
        underlying: float,
    ) -> dict | None:
        """Select best contract from available chain."""
        # Filter by type, expiry, and basic liquidity
        candidates = [
            c for c in chain
            if c.get("call_put") == ("CALL" if is_call else "PUT")
            and c.get("expiration") == target_expiry
            and c.get("volume", 0) >= self.config.get("min_volume", 100)
            and c.get("open_interest", 0) >= self.config.get("min_open_interest", 500)
        ]
        
        if not candidates:
            _log.warning("[options] No liquid contracts found for %s", target_expiry)
            return None
        
        # Score candidates by delta proximity and spread
        scored = []
        for c in candidates:
            delta = c.get("delta", 0.5)
            delta_score = 1.0 - abs(delta - 0.35)  # Prefer ~0.35 delta
            
            bid = c.get("bid", 0)
            ask = c.get("ask", 0)
            spread_pct = (ask - bid) / ((ask + bid) / 2) if (ask + bid) > 0 else 1.0
            spread_score = 1.0 - min(spread_pct / self.config.get("max_spread_pct", 0.10), 1.0)
            
            score = delta_score * 0.6 + spread_score * 0.4
            scored.append((score, c))
        
        scored.sort(reverse=True)
        best = scored[0][1]
        
        return {
            "symbol": best.get("symbol", ""),
            "strike": best.get("strike", 0),
            "expiry": best.get("expiration", ""),
            "call_put": best.get("call_put", ""),
            "delta": best.get("delta", 0),
            "premium": round((best.get("ask", 0) + best.get("bid", 0)) / 2, 2),
            "spread_pct": round((best.get("ask", 0) - best.get("bid", 0)) / ((best.get("ask", 0) + best.get("bid", 0)) / 2) * 100, 2) if (best.get("ask", 0) + best.get("bid", 0)) > 0 else 0,
            "volume": best.get("volume", 0),
            "open_interest": best.get("open_interest", 0),
        }
    
    def _synthetic_contract(
        self,
        ticker: str,
        is_call: bool,
        strike: float,
        expiry: str,
        underlying: float,
    ) -> dict:
        """Generate synthetic contract info when no chain available."""
        # Estimate premium using rough ATM approximation
        days_to_expiry = (datetime.fromisoformat(expiry).date() - date.today()).days
        days_to_expiry = max(days_to_expiry, 1)
        
        # Rough IV estimate (30% for SPY/QQQ, 50% for NVDA)
        iv = 0.50 if ticker == "NVDA" else 0.30
        
        # Very rough premium estimate (simplified B-S)
        premium = underlying * iv * (days_to_expiry / 365) ** 0.5
        
        option_type = "CALL" if is_call else "PUT"
        symbol = f"{ticker}{expiry.replace('-', '')}{option_type[0]}{int(strike * 1000):08d}"
        
        return {
            "symbol": symbol,
            "strike": strike,
            "expiry": expiry,
            "call_put": option_type,
            "delta": 0.35 if is_call else -0.35,
            "premium": round(premium, 2),
            "spread_pct": 5.0,  # Estimated
            "volume": 0,  # Unknown
            "open_interest": 0,  # Unknown
            "_synthetic": True,
            "_note": "Synthetic estimate - no live option chain available",
        }
    
    def validate_risk(self, contract: dict, account_value: float) -> tuple[bool, list[str]]:
        """
        Validate that contract meets risk parameters.
        
        Returns:
            (is_valid, list_of_risk_notes)
        """
        notes = []
        is_valid = True
        
        premium = contract.get("premium", 0)
        risk_pct = self.risk_config.get("risk_per_trade_pct", 0.5)
        max_risk_dollars = account_value * (risk_pct / 100)
        
        # Check premium vs risk budget
        contract_cost = premium * 100  # 1 contract = 100 shares
        if contract_cost > max_risk_dollars:
            notes.append(f"Premium ${contract_cost:.2f} exceeds risk budget ${max_risk_dollars:.2f}")
            is_valid = False
        
        # Check liquidity
        if contract.get("volume", 0) < self.config.get("min_volume", 100):
            notes.append(f"Volume {contract.get('volume', 0)} below minimum {self.config.get('min_volume', 100)}")
            is_valid = False
        
        if contract.get("open_interest", 0) < self.config.get("min_open_interest", 500):
            notes.append(f"OI {contract.get('open_interest', 0)} below minimum {self.config.get('min_open_interest', 500)}")
            is_valid = False
        
        # Check spread
        spread_pct = contract.get("spread_pct", 0)
        if spread_pct > self.config.get("max_spread_pct", 0.10) * 100:
            notes.append(f"Spread {spread_pct:.1f}% too wide (max {self.config.get('max_spread_pct', 0.10) * 100:.1f}%)")
            is_valid = False
        
        # Check delta range
        delta = abs(contract.get("delta", 0))
        if delta < self.config.get("preferred_delta_min", 0.30) or delta > self.config.get("preferred_delta_max", 0.40):
            notes.append(f"Delta {delta:.2f} outside preferred range 0.30-0.40")
        
        return is_valid, notes
