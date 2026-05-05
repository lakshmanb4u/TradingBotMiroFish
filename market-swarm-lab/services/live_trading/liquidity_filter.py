"""
Liquidity filter for live trading.
Validates option and stock liquidity before allowing trades.
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class LiquidityFilter:
    """Filters trades based on liquidity requirements."""
    
    def __init__(self, config: dict):
        self.config = config.get("liquidity_filters", {})
        self.min_option_volume = self.config.get("min_option_volume", 50)
        self.min_option_oi = self.config.get("min_option_oi", 200)
        self.max_spread_pct = self.config.get("max_spread_pct", 0.15)
        self.min_stock_volume = self.config.get("min_stock_volume_10d_avg", 1_000_000)
        self.min_market_cap = self.config.get("min_market_cap_billions", 1.0)
        self.reject_halted = self.config.get("reject_if_halted", True)
    
    def validate_option_contract(self, contract: dict) -> tuple[bool, list[str]]:
        """
        Validate option contract liquidity.
        
        Returns:
            (is_valid, list_of_rejection_reasons)
        """
        reasons = []
        
        # Volume check
        volume = contract.get("volume", 0)
        if volume < self.min_option_volume:
            reasons.append(
                f"Option volume {volume} below minimum {self.min_option_volume}"
            )
        
        # Open interest check
        oi = contract.get("open_interest", 0)
        if oi < self.min_option_oi:
            reasons.append(
                f"Option OI {oi} below minimum {self.min_option_oi}"
            )
        
        # Spread check
        bid = contract.get("bid", 0)
        ask = contract.get("ask", 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / ((ask + bid) / 2)
            if spread_pct > self.max_spread_pct:
                reasons.append(
                    f"Spread {spread_pct:.1%} exceeds max {self.max_spread_pct:.1%}"
                )
        
        is_valid = len(reasons) == 0
        return is_valid, reasons
    
    def validate_stock(self, quote: dict) -> tuple[bool, list[str]]:
        """
        Validate stock liquidity.
        
        Args:
            quote: Dict with keys like volume, avg_volume, market_cap, halted, etc.
        
        Returns:
            (is_valid, list_of_rejection_reasons)
        """
        reasons = []
        
        # Halted check
        if self.reject_halted and quote.get("halted", False):
            reasons.append("Stock is halted")
        
        # Volume check
        avg_volume = quote.get("avg_volume_10d", quote.get("volume", 0))
        if avg_volume < self.min_stock_volume:
            reasons.append(
                f"Avg volume {avg_volume:,} below minimum {self.min_stock_volume:,}"
            )
        
        # Market cap check (if available)
        market_cap = quote.get("market_cap", 0)
        if market_cap > 0 and market_cap < self.min_market_cap * 1e9:
            reasons.append(
                f"Market cap ${market_cap/1e9:.2f}B below minimum ${self.min_market_cap}B"
            )
        
        # Hard to borrow check (for shorts)
        if quote.get("is_hard_to_borrow", False):
            reasons.append("Stock is hard to borrow")
        
        is_valid = len(reasons) == 0
        return is_valid, reasons
    
    def validate_trade(
        self,
        ticker: str,
        option_contract: dict | None,
        stock_quote: dict,
    ) -> tuple[bool, list[str]]:
        """
        Full validation for a trade.
        
        Returns:
            (is_valid, list_of_rejection_reasons)
        """
        all_reasons = []
        
        # Validate stock
        stock_valid, stock_reasons = self.validate_stock(stock_quote)
        all_reasons.extend(stock_reasons)
        
        # Validate option if provided
        if option_contract:
            opt_valid, opt_reasons = self.validate_option_contract(option_contract)
            all_reasons.extend(opt_reasons)
        
        is_valid = len(all_reasons) == 0
        
        if not is_valid:
            _log.info("[liquidity] %s rejected: %s", ticker, "; ".join(all_reasons))
        
        return is_valid, all_reasons
