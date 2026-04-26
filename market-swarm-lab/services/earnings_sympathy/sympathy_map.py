"""SympathyMapper — maps a reporting ticker to its sympathy peer list.

Static map is defined here; config/sympathy_map.json can override or extend it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "config" / "sympathy_map.json"

# Static peer map — reporters → sympathy tickers
_STATIC_MAP: dict[str, list[str]] = {
    "INTC":  ["AMD", "ARM", "NVDA", "SMH", "SOXX"],
    "AMD":   ["INTC", "ARM", "NVDA", "SMH", "SOXX"],
    "NVDA":  ["AMD", "ARM", "INTC", "SMH", "SOXX", "MRVL"],
    "ARM":   ["AMD", "NVDA", "INTC", "QCOM", "MRVL"],
    "QCOM":  ["ARM", "AMD", "AVGO", "MRVL", "SWKS"],
    "AVGO":  ["QCOM", "MRVL", "SWKS", "SMH"],
    "MRVL":  ["AVGO", "QCOM", "INTC", "SMH"],
    "AMAT":  ["KLAC", "LRCX", "ASML", "SMH"],
    "KLAC":  ["AMAT", "LRCX", "ASML", "SMH"],
    "LRCX":  ["AMAT", "KLAC", "ASML", "SMH"],
    "TXN":   ["MCHP", "NXPI", "ON", "SMH"],
    "NXPI":  ["TXN", "MCHP", "ON", "SWKS"],
    "MSFT":  ["AMZN", "GOOGL", "META", "NVDA", "CRM"],
    "GOOGL": ["META", "SNAP", "PINS", "TTD", "MSFT"],
    "META":  ["GOOGL", "SNAP", "PINS", "TTD"],
    "SNAP":  ["META", "PINS", "GOOGL", "TTD"],
    "PINS":  ["META", "SNAP", "GOOGL"],
    "TTD":   ["GOOGL", "META", "MGNI", "PUBM"],
    "AMZN":  ["SHOP", "WMT", "EBAY", "ETSY", "MELI"],
    "SHOP":  ["AMZN", "BIGC", "WIX", "ETSY"],
    "EBAY":  ["AMZN", "ETSY", "SHOP", "MELI"],
    "ETSY":  ["EBAY", "AMZN", "SHOP"],
    "AAPL":  ["QCOM", "AVGO", "SWKS", "TSM", "FOXC"],
    "TSLA":  ["RIVN", "LCID", "GM", "F", "NIO", "LI"],
    "RIVN":  ["TSLA", "LCID", "NIO", "F", "GM"],
    "CRM":   ["MSFT", "NOW", "WDAY", "DDOG", "MDB"],
    "NOW":   ["CRM", "MSFT", "WDAY", "SMAR"],
    "DDOG":  ["MSFT", "SNOW", "ESTC", "SPLK"],
    "SNOW":  ["DDOG", "MSFT", "AMZN", "MDB"],
    "MDB":   ["SNOW", "DDOG", "MSFT", "AMZN"],
    "NFLX":  ["DIS", "PARA", "AMZN", "SPOT"],
    "DIS":   ["NFLX", "PARA", "WBD", "SPOT"],
    "SPOT":  ["NFLX", "DIS", "PARA"],
    "V":     ["MA", "AXP", "PYPL", "SQ"],
    "MA":    ["V", "AXP", "PYPL", "SQ"],
    "PYPL":  ["SQ", "V", "MA", "AFRM", "UPST"],
    "SQ":    ["PYPL", "V", "MA"],
    "JPM":   ["BAC", "WFC", "C", "GS", "MS"],
    "BAC":   ["JPM", "WFC", "C", "GS"],
    "GS":    ["MS", "JPM", "BAC"],
    "XOM":   ["CVX", "COP", "PXD", "OXY"],
    "CVX":   ["XOM", "COP", "PXD"],
    "UNH":   ["HUM", "CVS", "CI", "MOH"],
    "LLY":   ["NVO", "MRNA", "PFE", "BMY"],
    "SMH":   ["SOXX", "AMD", "NVDA", "INTC", "ARM"],
    "SOXX":  ["SMH", "AMD", "NVDA", "INTC"],
}


class SympathyMapper:
    """Map a reporting ticker to its sympathy peer list."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _CONFIG_PATH
        self._override: dict[str, list[str]] = self._load_override()

    def _load_override(self) -> dict[str, list[str]]:
        if not self._config_path.exists():
            return {}
        try:
            data = json.loads(self._config_path.read_text())
            if isinstance(data, dict):
                return {k.upper(): [t.upper() for t in v] for k, v in data.items()}
        except Exception as exc:
            _log.warning("[sympathy_map] override load error: %s", exc)
        return {}

    def get_sympathy_tickers(self, reporter: str) -> list[str]:
        """Return sympathy tickers for a reporter. Config override takes precedence."""
        reporter = reporter.upper()
        if reporter in self._override:
            tickers = self._override[reporter]
        else:
            tickers = _STATIC_MAP.get(reporter, [])
        # Exclude the reporter itself
        return [t for t in tickers if t != reporter]

    def get_sector(self, reporter: str) -> str:
        """Return sector string used for ETF fallback (e.g. 'semiconductors')."""
        sector_map = {
            "semiconductors": ["INTC", "AMD", "NVDA", "ARM", "QCOM", "AVGO", "MRVL",
                               "AMAT", "KLAC", "LRCX", "TXN", "NXPI", "SMH", "SOXX"],
            "cloud_software": ["MSFT", "CRM", "NOW", "DDOG", "SNOW", "MDB", "WDAY"],
            "social_media":   ["META", "GOOGL", "SNAP", "PINS", "TTD"],
            "ecommerce":      ["AMZN", "SHOP", "EBAY", "ETSY", "MELI"],
            "fintech":        ["PYPL", "SQ", "V", "MA", "AXP"],
            "banking":        ["JPM", "BAC", "WFC", "C", "GS", "MS"],
            "consumer_tech":  ["AAPL", "NFLX", "DIS", "SPOT"],
            "ev":             ["TSLA", "RIVN", "LCID", "NIO", "GM", "F"],
            "biotech":        ["LLY", "NVO", "MRNA", "PFE"],
        }
        reporter = reporter.upper()
        for sector, tickers in sector_map.items():
            if reporter in tickers:
                return sector
        return "general"

    def all_reporters(self) -> list[str]:
        combined = set(_STATIC_MAP.keys()) | set(self._override.keys())
        return sorted(combined)
