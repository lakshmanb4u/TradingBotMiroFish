from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ReportingService:
    def __init__(self) -> None:
        output_dir = os.getenv("REPORT_OUTPUT_DIR")
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(__file__).resolve().parents[2] / "state" / "reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        ticker: str,
        raw_bundle: dict[str, Any],
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any],
        simulation: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = self.output_dir / f"{ticker.upper()}_{timestamp}.json"
        markdown_path = self.output_dir / f"{ticker.upper()}_{timestamp}.md"

        payload = {
            "ticker": ticker.upper(),
            "generated_at": timestamp,
            "provider_modes": raw_bundle.get("provider_modes", {}),
            "snapshot": normalized_bundle["snapshot"],
            "forecast": forecast,
            "simulation": simulation,
            "raw_source_counts": {
                "news_articles": len(raw_bundle["news"].get("articles", [])),
                "reddit_threads": len(raw_bundle["reddit"].get("threads", [])),
                "prediction_markets": len(raw_bundle["prediction_markets"].get("markets", [])),
                "sec_filings": len(raw_bundle["sec_filings"].get("filings", [])),
            },
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        markdown_path.write_text(self._render_markdown(payload, raw_bundle, normalized_bundle), encoding="utf-8")

        return {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "summary": self._summary_line(payload),
        }

    def _summary_line(self, payload: dict[str, Any]) -> str:
        forecast = payload["forecast"]
        simulation = payload["simulation"]
        regime = simulation.get("regime") or simulation.get("final_direction", "unknown")
        outlook = simulation.get("outlook_score", simulation.get("distribution", {}).get("bullish", 0))
        return (
            f"{payload['ticker']} forecast {forecast.get('direction','?')} to {forecast.get('forecast_close_5d', '?')} "
            f"with simulation regime {regime} and outlook score {outlook}."
        )

    def _render_markdown(self, payload: dict[str, Any], raw_bundle: dict[str, Any], normalized_bundle: dict[str, Any]) -> str:
        snapshot = payload["snapshot"]
        forecast = payload["forecast"]
        simulation = payload["simulation"]
        narratives = normalized_bundle.get("simulation_seed", {}).get("key_narratives", [])
        news = raw_bundle["news"].get("articles", [])[:3]
        markets = raw_bundle["prediction_markets"].get("markets", [])[:3]
        sec = raw_bundle["sec_filings"].get("filings", [])[:2]
        regime = simulation.get("regime") or simulation.get("final_direction", "unknown")
        outlook = simulation.get("outlook_score", simulation.get("distribution", {}).get("bullish", 0))

        lines = [
            f"# {payload['ticker']} market-swarm-lab report",
            "",
            f"Generated at: {payload['generated_at']}",
            "",
            "## Executive summary",
            "",
            f"- Latest close: {snapshot['latest_close']}",
            f"- Forecast 5d: {forecast['forecast_close_5d']} ({forecast['direction']}, confidence {forecast['confidence']})",
            f"- MiroFish bridge regime: {regime} (score {outlook})",
            f"- Reddit sentiment: {snapshot['reddit_sentiment']} from {snapshot['reddit_mentions']} mentions",
            f"- Prediction market consensus: {snapshot['prediction_market_consensus']}",
            "",
            "## Why Reddit matters here",
            "",
            "Reddit is fed into two paths:",
            "- retail sentiment and narratives for the agent simulation seed",
            "- numeric features such as mentions, comment volume, bullish ratio, bearish ratio, and average sentiment for the forecast input window",
            "",
            "## Key narratives",
            "",
        ]
        lines.extend(f"- {item}" for item in narratives)
        lines.extend([
            "",
            "## Forecast drivers",
            "",
            f"- Short trend: {forecast['drivers']['short_trend']}",
            f"- Reddit impulse: {forecast['drivers']['reddit_impulse']}",
            f"- RSI drag: {forecast['drivers']['rsi_drag']}",
            "",
            "## Simulation rounds",
            "",
        ])
        lines.extend(
            f"- Round {item.get('round',i+1)}: {item.get('focus', item.get('signal',''))} | score: {item.get('score', item.get('running_score','?'))}"
            for i, item in enumerate(simulation.get("rounds", []))
        )
        lines.extend(["", "## News", ""])
        lines.extend(f"- {item['source']}: {item['title']}" for item in news)
        lines.extend(["", "## Prediction markets", ""])
        lines.extend(
            f"- {item['venue']}: {item['contract']} | yes probability {item['probability_yes']}"
            for item in markets
        )
        lines.extend(["", "## SEC filings", ""])
        lines.extend(f"- {item['form']}: {item['summary']}" for item in sec)
        return "\n".join(lines) + "\n"
