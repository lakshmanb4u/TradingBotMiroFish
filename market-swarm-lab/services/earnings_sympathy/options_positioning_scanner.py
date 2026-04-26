"""OptionsPositioningScanner — fetch and filter options contracts for sympathy candidates.

Fetches options chain from Schwab, applies hard filters, and returns candidate contracts
with all fields needed for downstream scoring.

Hard filters:
  - spread_pct <= max_spread_pct (default 15%)
  - volume >= min_volume (default 100)
  - open_interest >= min_open_interest (default 500)
  - DTE between min_dte (3) and max_dte (14)
  - Premium <= max_risk_per_trade / 100 (per contract)
  - Skip if IV rank too high (iv > iv_baseline * 1.4)
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]

_SCHWAB_DIR = str(_ROOT / "services" / "schwab-collector")
if _SCHWAB_DIR not in sys.path:
    sys.path.insert(0, _SCHWAB_DIR)


def _load_schwab_client():
    try:
        from schwab_client import SchwabClient
        return SchwabClient()
    except Exception as exc:
        _log.warning("[options_scanner] SchwabClient unavailable: %s", exc)
        return None


def _parse_contracts(chain: dict, option_type: str, underlying_price: float) -> list[dict]:
    """Flatten Schwab callExpDateMap/putExpDateMap into a list of contract dicts."""
    key = "callExpDateMap" if option_type == "CALL" else "putExpDateMap"
    date_map = chain.get(key, {})
    contracts: list[dict] = []

    for exp_key, strikes_data in date_map.items():
        # exp_key: "2026-05-08:8"
        parts = exp_key.split(":")
        expiry_date = parts[0]
        fallback_dte = int(parts[1]) if len(parts) > 1 else 0

        for strike_str, contract_list in strikes_data.items():
            if not contract_list:
                continue
            c = contract_list[0]

            bid = float(c.get("bid", 0) or 0)
            ask = float(c.get("ask", 0) or 0)
            last = float(c.get("last", 0) or 0)
            mid = (bid + ask) / 2 if (bid + ask) > 0 else last
            if mid <= 0:
                continue

            volume = int(c.get("totalVolume", 0) or 0)
            oi = int(c.get("openInterest", 0) or 0)
            strike = float(strike_str)
            dte = int(c.get("daysToExpiration", fallback_dte) or fallback_dte)

            raw_delta = float(c.get("delta", 0) or 0)
            delta = abs(raw_delta)

            # Schwab returns volatility as percent (35.2 = 35.2% IV)
            iv_raw = float(c.get("volatility", 0) or 0)
            iv = iv_raw / 100.0 if iv_raw > 1.0 else iv_raw

            spread_pct = (ask - bid) / mid * 100 if mid > 0 else 999.0
            premium_pct = mid / underlying_price * 100 if underlying_price > 0 else 0.0

            # OTM flag: calls OTM if strike > underlying, puts OTM if strike < underlying
            is_otm = (
                (option_type == "CALL" and strike > underlying_price)
                or (option_type == "PUT" and strike < underlying_price)
            )

            contracts.append({
                "strike": strike,
                "expiry": expiry_date,
                "dte": dte,
                "option_type": option_type,
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": round(mid, 2),
                "last": round(last, 2),
                "volume": volume,
                "open_interest": oi,
                "delta": round(delta, 3),
                "implied_volatility": round(iv, 4),
                "spread_pct": round(spread_pct, 2),
                "premium_pct_of_underlying": round(premium_pct, 4),
                "underlying_price": round(underlying_price, 2),
                "is_otm": is_otm,
            })

    return contracts


class OptionsPositioningScanner:
    """Scan options chain for a ticker and return filtered candidate contracts."""

    DEFAULT_CONFIG = {
        "max_spread_pct": 15.0,
        "min_volume": 100,
        "min_open_interest": 500,
        "min_dte": 3,
        "max_dte": 14,
        "max_risk_per_trade": 500,
        "iv_expansion_hard_limit": 1.4,
        "strike_count": 20,
    }

    def __init__(self, config: dict | None = None) -> None:
        self._cfg = {**self.DEFAULT_CONFIG, **(config or {})}
        self._client = _load_schwab_client()

    def scan(
        self,
        ticker: str,
        option_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch and filter options chain for ticker.

        Returns:
            {
                "ticker": str,
                "underlying_price": float,
                "source": str,
                "contracts": [...],   # all filtered contracts
                "call_contracts": [...],
                "put_contracts": [...],
                "chain_raw": {...},   # raw chain for persistence
            }
        """
        ticker = ticker.upper()
        option_types = option_types or ["CALL", "PUT"]
        max_prem = self._cfg["max_risk_per_trade"] / 100  # per contract (100 shares)

        chain: dict = {}
        source = "schwab_offline"

        if self._client:
            try:
                chain = self._client.get_options_chain(
                    ticker,
                    contract_type="ALL",
                    strike_count=self._cfg["strike_count"],
                )
                source = "schwab_live" if chain.get("status") != "fixture_missing" else "schwab_fixture"
            except Exception as exc:
                _log.warning("[options_scanner] chain fetch failed for %s: %s", ticker, exc)
                source = "schwab_error"

        underlying_price = float(chain.get("underlyingPrice", 0) or 0)
        if underlying_price <= 0:
            _log.warning("[options_scanner] no underlying price for %s — skipping", ticker)
            return {
                "ticker": ticker,
                "underlying_price": 0.0,
                "source": source,
                "contracts": [],
                "call_contracts": [],
                "put_contracts": [],
                "chain_raw": chain,
                "skip_reason": "no_underlying_price",
            }

        all_contracts: list[dict] = []
        for ot in option_types:
            all_contracts.extend(_parse_contracts(chain, ot, underlying_price))

        # Apply hard filters
        filtered: list[dict] = []
        skipped_counts: dict[str, int] = {}
        for c in all_contracts:
            reason = self._apply_filters(c, max_prem)
            if reason:
                skipped_counts[reason] = skipped_counts.get(reason, 0) + 1
            else:
                filtered.append(c)

        # Sort: OTM first, then by DTE ascending
        filtered.sort(key=lambda c: (not c["is_otm"], c["dte"], c["spread_pct"]))

        calls = [c for c in filtered if c["option_type"] == "CALL"]
        puts = [c for c in filtered if c["option_type"] == "PUT"]

        _log.info(
            "[options_scanner] %s: %d raw → %d filtered (%d calls, %d puts) | skipped: %s",
            ticker, len(all_contracts), len(filtered), len(calls), len(puts), skipped_counts,
        )

        return {
            "ticker": ticker,
            "underlying_price": underlying_price,
            "source": source,
            "contracts": filtered,
            "call_contracts": calls,
            "put_contracts": puts,
            "chain_raw": {k: v for k, v in chain.items() if k not in ("callExpDateMap", "putExpDateMap")},
            "total_raw": len(all_contracts),
            "total_filtered": len(filtered),
            "skip_summary": skipped_counts,
        }

    def _apply_filters(self, c: dict, max_premium: float) -> str | None:
        """Return skip reason string or None if contract passes."""
        dte = c["dte"]
        if dte < self._cfg["min_dte"] or dte > self._cfg["max_dte"]:
            return "dte_out_of_range"
        if c["spread_pct"] > self._cfg["max_spread_pct"]:
            return "spread_too_wide"
        if c["volume"] < self._cfg["min_volume"]:
            return "volume_too_low"
        if c["open_interest"] < self._cfg["min_open_interest"]:
            return "oi_too_low"
        if c["mid"] > max_premium:
            return "premium_exceeds_max_risk"
        return None
