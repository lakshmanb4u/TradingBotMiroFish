from __future__ import annotations

import json
import sys
from pathlib import Path

from .db import init_infra
from .workflow import run_ticker_workflow


def main(argv: list[str]) -> int:
    tickers = argv or ["NVDA", "SPY"]
    init_infra()
    outputs = []
    for ticker in tickers:
        result = run_ticker_workflow(ticker=ticker, persist=True)
        outputs.append(
            {
                "ticker": result["ticker"],
                "json_path": result["report"]["json_path"],
                "markdown_path": result["report"]["markdown_path"],
                "forecast_direction": result["forecast"]["direction"],
                "simulation_regime": result["simulation"]["regime"],
            }
        )

    print(json.dumps({"demo_outputs": outputs}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
