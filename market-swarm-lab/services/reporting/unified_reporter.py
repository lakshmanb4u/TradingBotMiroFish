"""
Unified Reporter Service.

Combines signals from TimesFM, Reddit, MiroFish, and prediction markets
into a single directional call with confidence and divergence analysis.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class UnifiedReporter:
    def __init__(self) -> None:
        output_dir = os.getenv("REPORT_OUTPUT_DIR")
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(__file__).resolve().parents[2] / "state" / "reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def report(
        self,
        ticker: str,
        forecast: dict[str, Any],
        reddit_seed: dict[str, Any],
        normalized: dict[str, Any],
        simulation: dict[str, Any],
        seed: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = normalized.get("snapshot", {})
        sim_seed = normalized.get("simulation_seed", {})

        # ── Extract raw signal values

        # TimesFM
        forecast_direction = forecast.get("direction", "sideways")
        forecast_confidence = float(forecast.get("confidence", 0.5))

        # Reddit
        reddit_sentiment = float(reddit_seed.get("weighted_sentiment", snapshot.get("reddit_sentiment", 0.0)))
        reddit_feats = sim_seed.get("retail_sentiment", {})
        bullish_ratio = float(reddit_feats.get("bullish_ratio", 0.5))
        bearish_ratio = float(reddit_feats.get("bearish_ratio", 0.5))

        # MiroFish / simulation
        mirofish_final = simulation.get("final_direction", "rangebound")
        mirofish_score = float(simulation.get("outlook_score", 0.0))
        distribution = simulation.get("distribution", {})
        mirofish_bullish = float(distribution.get("bullish", 0.5))
        regime = simulation.get("regime", mirofish_final)

        # Prediction markets
        consensus = float(snapshot.get("prediction_market_consensus", 0.5))

        # SEC
        sec_risk = float(snapshot.get("sec_risk_score", 0.0))

        # News
        news_sentiment = float(snapshot.get("news_sentiment", 0.0))

        # ── Weighted vote for direction
        # timesfm weight 0.35
        timesfm_vote = 1.0 if forecast_direction == "up" else (-1.0 if forecast_direction == "down" else 0.0)
        # reddit weight 0.25
        reddit_vote = 1.0 if reddit_sentiment > 0 else (-1.0 if reddit_sentiment < 0 else 0.0)
        # mirofish weight 0.25
        mirofish_vote = 1.0 if mirofish_final == "bullish" else (-1.0 if mirofish_final == "bearish" else 0.0)
        # prediction market weight 0.15
        pred_vote = 1.0 if consensus > 0.55 else (-1.0 if consensus <= 0.45 else 0.0)

        total_vote = (
            timesfm_vote * 0.35
            + reddit_vote * 0.25
            + mirofish_vote * 0.25
            + pred_vote * 0.15
        )

        if total_vote > 0.1:
            direction = "bullish"
        elif total_vote < -0.1:
            direction = "bearish"
        else:
            direction = "sideways"

        # ── Confidence = weighted average of signal confidences, capped at 0.95
        timesfm_conf = forecast_confidence
        reddit_conf = min(1.0, abs(reddit_sentiment) + 0.3)
        mirofish_conf = min(1.0, abs(mirofish_score) / 100 + 0.3) if abs(mirofish_score) > 0 else mirofish_bullish
        pred_conf = abs(consensus - 0.5) * 2 + 0.3  # distance from 0.5 → confidence

        confidence = min(0.95, (
            timesfm_conf * 0.35
            + reddit_conf * 0.25
            + mirofish_conf * 0.25
            + min(1.0, pred_conf) * 0.15
        ))
        confidence = round(confidence, 4)

        # ── Divergence metrics
        forecast_up = forecast_direction == "up"
        reddit_norm = (reddit_sentiment + 1) / 2  # normalize to 0..1

        reddit_vs_forecast = round(
            abs(reddit_sentiment - (1.0 if forecast_up else -1.0)) / 2, 4
        )
        prediction_vs_forecast = round(
            abs(consensus - (0.7 if forecast_up else 0.3)), 4
        )
        mirofish_norm = (mirofish_vote + 1) / 2  # normalize to 0..1
        institutional_vs_retail = round(
            abs(mirofish_norm - reddit_norm) / 2, 4
        )

        # ── Key drivers (5 plain English strings)
        key_drivers = self._build_key_drivers(
            ticker, forecast_direction, forecast_confidence,
            reddit_sentiment, bullish_ratio, bearish_ratio,
            mirofish_final, mirofish_score, consensus, sec_risk,
        )

        # ── Signals dict
        signals = {
            "timesfm": {
                "direction": forecast_direction,
                "confidence": round(forecast_confidence, 4),
            },
            "reddit": {
                "sentiment": round(reddit_sentiment, 4),
                "bullish_ratio": round(bullish_ratio, 4),
                "bearish_ratio": round(bearish_ratio, 4),
            },
            "sec": {
                "risk_score": round(sec_risk, 4),
            },
            "news": {
                "sentiment": round(news_sentiment, 4),
            },
            "prediction_markets": {
                "consensus": round(consensus, 4),
            },
            "mirofish": {
                "regime": regime,
                "outlook_score": round(mirofish_score, 4),
            },
        }

        divergence = {
            "reddit_vs_forecast": reddit_vs_forecast,
            "prediction_vs_forecast": prediction_vs_forecast,
            "institutional_vs_retail": institutional_vs_retail,
        }

        # ── Markdown report
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        markdown_report = self._render_markdown(
            ticker=ticker,
            direction=direction,
            confidence=confidence,
            signals=signals,
            divergence=divergence,
            key_drivers=key_drivers,
            timestamp=timestamp,
        )

        # ── Save to file
        report_path = self.output_dir / f"{ticker.upper()}_unified_{timestamp}.md"
        report_path.write_text(markdown_report, encoding="utf-8")
        stored_at = str(report_path)

        return {
            "ticker": ticker.upper(),
            "direction": direction,
            "confidence": confidence,
            "signals": signals,
            "divergence": divergence,
            "key_drivers": key_drivers,
            "markdown_report": markdown_report,
            "stored_at": stored_at,
        }

    # ── helpers

    def _build_key_drivers(
        self,
        ticker: str,
        forecast_direction: str,
        forecast_confidence: float,
        reddit_sentiment: float,
        bullish_ratio: float,
        bearish_ratio: float,
        mirofish_final: str,
        mirofish_score: float,
        consensus: float,
        sec_risk: float,
    ) -> list[str]:
        drivers = []

        direction_label = "upward" if forecast_direction == "up" else ("downward" if forecast_direction == "down" else "sideways")
        drivers.append(
            f"TimesFM quantitative model projects a {direction_label} move with {forecast_confidence:.0%} confidence."
        )

        reddit_label = "bullish" if reddit_sentiment > 0 else ("bearish" if reddit_sentiment < 0 else "neutral")
        drivers.append(
            f"Reddit retail sentiment is {reddit_label} ({reddit_sentiment:+.2f}), "
            f"with {bullish_ratio:.0%} bullish and {bearish_ratio:.0%} bearish posts."
        )

        drivers.append(
            f"Multi-agent simulation (MiroFish) concluded {mirofish_final} with an outlook score of {mirofish_score:.1f}."
        )

        pred_label = "risk-on" if consensus > 0.55 else ("risk-off" if consensus < 0.45 else "neutral")
        drivers.append(
            f"Prediction markets are {pred_label} at {consensus:.0%} consensus probability."
        )

        risk_label = "elevated" if sec_risk > 0.15 else ("moderate" if sec_risk > 0.08 else "low")
        drivers.append(
            f"{ticker.upper()} SEC regulatory risk is {risk_label} (score: {sec_risk:.2f}), "
            f"{'a headwind' if sec_risk > 0.15 else 'a manageable factor'} for the outlook."
        )

        return drivers

    def _render_markdown(
        self,
        ticker: str,
        direction: str,
        confidence: float,
        signals: dict[str, Any],
        divergence: dict[str, Any],
        key_drivers: list[str],
        timestamp: str,
    ) -> str:
        lines = [
            f"# {ticker.upper()} Unified Market Report",
            "",
            f"Generated at: {timestamp}",
            "",
            "## Summary",
            "",
            f"**Direction:** {direction.upper()}",
            f"**Confidence:** {confidence:.1%}",
            "",
            "## Signals",
            "",
            f"| Signal | Value |",
            f"|--------|-------|",
            f"| TimesFM Direction | {signals['timesfm']['direction']} |",
            f"| TimesFM Confidence | {signals['timesfm']['confidence']:.1%} |",
            f"| Reddit Sentiment | {signals['reddit']['sentiment']:+.3f} |",
            f"| Reddit Bullish Ratio | {signals['reddit']['bullish_ratio']:.1%} |",
            f"| Reddit Bearish Ratio | {signals['reddit']['bearish_ratio']:.1%} |",
            f"| News Sentiment | {signals['news']['sentiment']:+.3f} |",
            f"| SEC Risk Score | {signals['sec']['risk_score']:.3f} |",
            f"| Prediction Market Consensus | {signals['prediction_markets']['consensus']:.1%} |",
            f"| MiroFish Regime | {signals['mirofish']['regime']} |",
            f"| MiroFish Outlook Score | {signals['mirofish']['outlook_score']:.1f} |",
            "",
            "## Divergence",
            "",
            f"- **Reddit vs Forecast:** {divergence['reddit_vs_forecast']:.3f}",
            f"- **Prediction Market vs Forecast:** {divergence['prediction_vs_forecast']:.3f}",
            f"- **Institutional vs Retail:** {divergence['institutional_vs_retail']:.3f}",
            "",
            "## Key Drivers",
            "",
        ]
        for i, driver in enumerate(key_drivers, 1):
            lines.append(f"{i}. {driver}")
        lines.extend([
            "",
            "## Conclusion",
            "",
            f"Based on a weighted combination of TimesFM (35%), Reddit sentiment (25%), "
            f"MiroFish agent simulation (25%), and prediction market consensus (15%), "
            f"the unified outlook for {ticker.upper()} is **{direction.upper()}** "
            f"with {confidence:.1%} confidence.",
            "",
        ])
        return "\n".join(lines)
