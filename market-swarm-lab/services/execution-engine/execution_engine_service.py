"""Execution engine: paper trading only by default. Live mode requires explicit env var."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper")  # paper | disabled | live
LIVE_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

_ORDERS_DIR = _ROOT / "state" / "orders"


class ExecutionEngineService:
    def execute(self, signal: dict, risk: dict, ticker: str) -> dict:
        mode = EXECUTION_MODE

        if mode == "disabled":
            return {"status": "disabled", "mode": "disabled"}

        # Live guardrail — never execute live unless both flags are set
        if mode == "live" and not LIVE_ENABLED:
            mode = "paper"

        if not risk.get("approved", False):
            return {"status": "rejected", "reason": risk.get("risk_notes", [])}

        now_iso = datetime.now(timezone.utc).isoformat()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        trade = signal.get("trade", "HOLD")
        order_type = "BUY_TO_OPEN" if trade in ("CALL", "PUT") else "NONE"

        option_plan: dict = signal.get("option_plan") or {}

        order: dict = {
            "mode": mode,
            "ticker": ticker.upper(),
            "order_type": order_type,
            "instrument": signal.get("option_type", "CALL"),
            "expiry_days": option_plan.get("expiry_days", 0),
            "strike_selection": option_plan.get("strike_selection", "ATM"),
            "holding_period": option_plan.get("holding_period", "none"),
            "quantity": 1,
            "entry_style": signal.get("entry_style", "limit"),
            "status": "queued",
            "confidence": signal.get("confidence", 0.0),
            "stop_loss_pct": risk.get("stop_loss_pct", 0.30),
            "take_profit_pct": risk.get("take_profit_pct", 0.60),
            "max_hold_time": risk.get("max_hold_time", "1d"),
            "created_at": now_iso,
        }

        self._persist_order(ticker.upper(), ts, order)
        return order

    def get_open_orders(self, ticker: str | None = None) -> list[dict]:
        _ORDERS_DIR.mkdir(parents=True, exist_ok=True)
        orders: list[dict] = []
        for path in _ORDERS_DIR.glob("*.json"):
            try:
                with open(path) as f:
                    order = json.load(f)
                if ticker is None or order.get("ticker", "").upper() == ticker.upper():
                    orders.append(order)
            except Exception:
                continue
        return orders

    def _persist_order(self, ticker: str, ts: str, order: dict) -> None:
        _ORDERS_DIR.mkdir(parents=True, exist_ok=True)
        path = _ORDERS_DIR / f"{ticker}_{ts}.json"
        try:
            with open(path, "w") as f:
                json.dump(order, f, indent=2)
        except Exception:
            pass
