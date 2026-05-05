"""
Ticker classifier for watchlist management.
Maps tickers to sectors, priorities, and trading eligibility.
"""

from typing import Literal

TickerPriority = Literal["highest", "medium", "low", "watch_only"]
TickerClass = Literal["mega_cap", "ai_semis", "software_infra", "momentum", "macro_etf", "speculative"]


class TickerClassifier:
    """Classifies and manages the trading universe."""
    
    # Classification mapping
    CLASSIFICATION: dict[TickerClass, dict] = {
        "mega_cap": {
            "tickers": ["SPY", "QQQ", "VOO", "AAPL", "AMZN", "MSFT", "META", "GOOG"],
            "priority": "highest",
            "max_position_pct": 5.0,
            "enabled_for_trading": True,
        },
        "ai_semis": {
            "tickers": ["NVDA", "AMD", "ARM", "MU", "AVGO", "WDC", "STX", "SNDK", "CLS", "VRT", "LITE", "KLAC", "CDNS"],
            "priority": "highest",
            "max_position_pct": 3.0,
            "enabled_for_trading": True,
            "sympathy_groups": [
                ["NVDA", "AMD", "ARM"],
                ["MU", "WDC", "STX", "SNDK"],
                ["AVGO", "CLS", "VRT", "LITE"],
                ["KLAC", "CDNS"],
            ],
        },
        "software_infra": {
            "tickers": ["SNOW", "MDB", "ESTC", "NBIS", "HUBS"],
            "priority": "medium",
            "max_position_pct": 2.0,
            "enabled_for_trading": True,
            "sympathy_groups": [
                ["SNOW", "MDB", "ESTC"],
            ],
        },
        "momentum": {
            "tickers": ["HOOD", "COIN", "AFRM", "APP", "TTD", "SEZL", "UPST", "IREN", "RDDT", "RXRX", "OSCR"],
            "priority": "medium",
            "max_position_pct": 2.0,
            "enabled_for_trading": True,
            "sympathy_groups": [
                ["HOOD", "COIN"],
                ["AFRM", "UPST"],
                ["APP", "TTD"],
            ],
        },
        "macro_etf": {
            "tickers": ["ARKK", "XLY", "SPX"],
            "priority": "highest",
            "max_position_pct": 5.0,
            "enabled_for_trading": True,
        },
        "speculative": {
            "tickers": ["AMPX", "ONDS", "AXON", "PGY", "RZLV", "ATXRF"],
            "priority": "low",
            "max_position_pct": 1.0,
            "enabled_for_trading": False,
            "require_extra_confirmation": True,
        },
    }
    
    # Priority ordering for scanning
    PRIORITY_ORDER: dict[TickerPriority, list[str]] = {
        "highest": ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "ARM", "MU", "AVGO", "COIN", "HOOD"],
        "medium": ["WDC", "STX", "VRT", "CLS", "APP", "TTD", "AFRM", "SNOW", "MDB", "ESTC"],
        "low": ["SNDK", "LITE", "KLAC", "CDNS", "NBIS", "SEZL", "UPST", "IREN", "RDDT", "RXRX", "OSCR"],
        "watch_only": ["AMPX", "ONDS", "AXON", "PGY", "RZLV", "ATXRF"],
    }
    
    # Sector definitions for heatmap
    SECTORS = {
        "ai_semis": ["NVDA", "AMD", "ARM", "MU", "AVGO"],
        "software": ["SNOW", "MDB", "ESTC", "HUBS"],
        "fintech": ["HOOD", "COIN", "AFRM", "UPST"],
        "mega_tech": ["AAPL", "AMZN", "MSFT", "META", "GOOG"],
        "momentum": ["APP", "TTD", "SEZL", "RDDT"],
        "macro": ["SPY", "QQQ", "ARKK", "XLY"],
    }
    
    def __init__(self):
        self._ticker_to_class: dict[str, TickerClass] = {}
        self._ticker_to_priority: dict[str, TickerPriority] = {}
        self._build_indices()
    
    def _build_indices(self):
        """Build lookup indices."""
        for class_name, config in self.CLASSIFICATION.items():
            for ticker in config["tickers"]:
                self._ticker_to_class[ticker] = class_name
                self._ticker_to_priority[ticker] = config["priority"]
        
        # Override with explicit priority order
        for priority, tickers in self.PRIORITY_ORDER.items():
            for ticker in tickers:
                self._ticker_to_priority[ticker] = priority
    
    def get_class(self, ticker: str) -> TickerClass | None:
        """Get classification for a ticker."""
        return self._ticker_to_class.get(ticker)
    
    def get_priority(self, ticker: str) -> TickerPriority:
        """Get scan priority for a ticker."""
        return self._ticker_to_priority.get(ticker, "low")
    
    def is_trading_enabled(self, ticker: str) -> bool:
        """Check if ticker is enabled for trading."""
        class_name = self.get_class(ticker)
        if not class_name:
            return False
        return self.CLASSIFICATION[class_name].get("enabled_for_trading", False)
    
    def get_max_position_pct(self, ticker: str) -> float:
        """Get max position size for a ticker."""
        class_name = self.get_class(ticker)
        if not class_name:
            return 1.0
        return self.CLASSIFICATION[class_name].get("max_position_pct", 1.0)
    
    def get_sympathy_group(self, ticker: str) -> list[str]:
        """Get sympathy group for a ticker."""
        class_name = self.get_class(ticker)
        if not class_name:
            return []
        
        groups = self.CLASSIFICATION[class_name].get("sympathy_groups", [])
        for group in groups:
            if ticker in group:
                return [t for t in group if t != ticker]
        return []
    
    def get_all_tickers(self) -> list[str]:
        """Get all tickers in the universe."""
        tickers = []
        for config in self.CLASSIFICATION.values():
            tickers.extend(config["tickers"])
        return sorted(set(tickers))
    
    def get_trading_tickers(self) -> list[str]:
        """Get tickers enabled for trading."""
        return [t for t in self.get_all_tickers() if self.is_trading_enabled(t)]
    
    def get_tickers_by_priority(self, priority: TickerPriority) -> list[str]:
        """Get tickers for a specific priority level."""
        return self.PRIORITY_ORDER.get(priority, [])
    
    def get_sector_tickers(self, sector: str) -> list[str]:
        """Get tickers for a sector."""
        return self.SECTORS.get(sector, [])
    
    def get_ticker_info(self, ticker: str) -> dict:
        """Get full info for a ticker."""
        class_name = self.get_class(ticker)
        if not class_name:
            return {"ticker": ticker, "known": False}
        
        config = self.CLASSIFICATION[class_name]
        return {
            "ticker": ticker,
            "known": True,
            "class": class_name,
            "priority": self.get_priority(ticker),
            "trading_enabled": config.get("enabled_for_trading", False),
            "max_position_pct": config.get("max_position_pct", 1.0),
            "sympathy": self.get_sympathy_group(ticker),
        }
