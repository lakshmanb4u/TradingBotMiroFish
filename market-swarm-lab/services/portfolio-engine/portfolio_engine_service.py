"""Portfolio engine: tracks positions, PnL, and trade journal."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PORTFOLIO_DIR = _ROOT / "state" / "portfolio"
_JOURNAL_PATH = _PORTFOLIO_DIR / "trade_journal.json"


class PortfolioEngineService:
    def record_trade(self, order: dict, outcome: dict | None = None) -> dict:
        _PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.now(timezone.utc).isoformat()

        realized_pnl: float | None = None
        unrealized_pnl: float | None = None
        exit_time: str | None = None
        status = "open"

        if outcome:
            realized_pnl = outcome.get("realized_pnl")
            unrealized_pnl = outcome.get("unrealized_pnl")
            exit_time = outcome.get("exit_time")
            status = "closed" if realized_pnl is not None else "open"

        record: dict = {
            "id": str(uuid.uuid4()),
            "ticker": order.get("ticker", ""),
            "trade": order.get("instrument", order.get("trade", "")),
            "strategy_type": order.get("strategy_type", ""),
            "confidence": float(order.get("confidence", 0.0)),
            "entry_time": order.get("created_at", now_iso),
            "exit_time": exit_time,
            "status": status,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "drivers": order.get("drivers", []),
            "source_audit_snapshot": order.get("source_audit_snapshot", {}),
            "risk_params": {
                "stop_loss_pct": order.get("stop_loss_pct"),
                "take_profit_pct": order.get("take_profit_pct"),
                "max_hold_time": order.get("max_hold_time"),
                "position_size_pct": order.get("position_size_pct"),
            },
        }

        journal = self._load_journal()
        journal.append(record)
        self._save_journal(journal)
        return record

    def get_portfolio_state(self) -> dict:
        journal = self._load_journal()

        open_positions = [t for t in journal if t.get("status") == "open"]
        closed_positions = [t for t in journal if t.get("status") == "closed"]

        realized_pnl = sum(
            float(t["realized_pnl"]) for t in closed_positions if t.get("realized_pnl") is not None
        )
        unrealized_pnl = sum(
            float(t["unrealized_pnl"]) for t in open_positions if t.get("unrealized_pnl") is not None
        )

        total_trades = len(closed_positions)
        winning = sum(
            1 for t in closed_positions
            if t.get("realized_pnl") is not None and float(t["realized_pnl"]) > 0
        )
        win_rate = round(winning / total_trades, 4) if total_trades > 0 else 0.0

        return {
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "realized_pnl": round(realized_pnl, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "total_trades": total_trades,
            "win_rate": win_rate,
        }

    def save_daily_summary(self, date_str: str) -> str:
        _PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
        state = self.get_portfolio_state()

        md_path = _PORTFOLIO_DIR / f"daily_summary_{date_str}.md"
        try:
            with open(md_path, "w") as f:
                f.write(f"# Portfolio Daily Summary — {date_str}\n\n")
                f.write("## Overview\n\n")
                f.write(f"| Metric | Value |\n|---|---|\n")
                f.write(f"| Total Trades | {state['total_trades']} |\n")
                f.write(f"| Win Rate | {state['win_rate']:.1%} |\n")
                f.write(f"| Realized PnL | {state['realized_pnl']:.4f} |\n")
                f.write(f"| Unrealized PnL | {state['unrealized_pnl']:.4f} |\n")
                f.write(f"| Open Positions | {len(state['open_positions'])} |\n\n")
                if state["open_positions"]:
                    f.write("## Open Positions\n\n")
                    for pos in state["open_positions"]:
                        f.write(f"- {pos.get('ticker')} {pos.get('trade')} (conf: {pos.get('confidence', 0):.2f})\n")
        except Exception:
            pass

        return str(md_path)

    # ------------------------------------------------------------------ helpers

    def _load_journal(self) -> list[dict]:
        if not _JOURNAL_PATH.exists():
            return []
        try:
            with open(_JOURNAL_PATH) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_journal(self, journal: list[dict]) -> None:
        _PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(_JOURNAL_PATH, "w") as f:
                json.dump(journal, f, indent=2)
        except Exception:
            pass
