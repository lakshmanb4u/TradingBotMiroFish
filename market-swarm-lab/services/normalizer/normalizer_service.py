from __future__ import annotations

from statistics import mean
from typing import Any


class UnifiedNormalizerService:
    def normalize(self, ticker: str, raw_bundle: dict[str, Any]) -> dict[str, Any]:
        market_series = raw_bundle["market_data"]["series"]
        reddit_activity = raw_bundle["reddit"]["activity"]
        news_articles = raw_bundle["news"]["articles"]
        sec_items = raw_bundle["sec_filings"].get("filings", [])
        prediction_items = raw_bundle["prediction_markets"].get("markets", [])

        feature_window = self._build_feature_window(market_series, reddit_activity)
        latest = feature_window[-1]
        prediction_consensus = round(mean(item.get("probability_yes", 0.5) for item in prediction_items), 3) if prediction_items else 0.5
        news_sentiment = round(mean(article.get("sentiment", 0.0) for article in news_articles), 3) if news_articles else 0.0
        sec_risk_score = round(mean(item.get("risk_score", 0.0) for item in sec_items), 3) if sec_items else 0.0
        reddit_sentiment = latest["reddit_avg_sentiment"]

        key_narratives = self._extract_narratives(raw_bundle["reddit"].get("threads", []), news_articles, sec_items)
        agent_personas = self._build_agent_personas(ticker, reddit_sentiment, prediction_consensus, news_sentiment)

        return {
            "ticker": ticker.upper(),
            "snapshot": {
                "latest_close": latest["close"],
                "latest_vwap": latest["vwap"],
                "latest_rsi": latest["rsi"],
                "reddit_sentiment": reddit_sentiment,
                "reddit_mentions": latest["reddit_mentions"],
                "news_sentiment": news_sentiment,
                "prediction_market_consensus": prediction_consensus,
                "sec_risk_score": sec_risk_score,
            },
            "feature_window": feature_window,
            "timesfm_inputs": {
                "target_series": [row["close"] for row in feature_window],
                "numeric_features": [
                    {
                        "date": row["date"],
                        "volume": row["volume"],
                        "vwap": row["vwap"],
                        "rsi": row["rsi"],
                        "reddit_mentions": row["reddit_mentions"],
                        "reddit_comments": row["reddit_comments"],
                        "reddit_avg_sentiment": row["reddit_avg_sentiment"],
                        "reddit_bullish_ratio": row["reddit_bullish_ratio"],
                        "reddit_bearish_ratio": row["reddit_bearish_ratio"],
                    }
                    for row in feature_window
                ],
            },
            "simulation_seed": {
                "retail_sentiment": {
                    "score": reddit_sentiment,
                    "bullish_ratio": latest["reddit_bullish_ratio"],
                    "bearish_ratio": latest["reddit_bearish_ratio"],
                    "subreddit_activity": raw_bundle["reddit"].get("threads", []),
                },
                "agent_personas": agent_personas,
                "key_narratives": key_narratives,
                "news_digest": news_articles,
                "sec_digest": sec_items,
                "prediction_markets": prediction_items,
            },
        }

    def _build_feature_window(self, market_series: list[dict[str, Any]], reddit_activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
        closes = [point["close"] for point in market_series]
        rsi_values = self._rsi_series(closes)
        reddit_by_date = {item["date"]: item for item in reddit_activity}
        latest_reddit = reddit_activity[-1] if reddit_activity else {
            "mentions": 0,
            "comments": 0,
            "bullish_ratio": 0.0,
            "bearish_ratio": 0.0,
            "avg_sentiment": 0.0,
        }

        window = []
        for idx, point in enumerate(market_series):
            reddit_point = reddit_by_date.get(point["date"], latest_reddit)
            vwap = round(point.get("vwap") or ((point["high"] + point["low"] + point["close"]) / 3), 2)
            window.append(
                {
                    "date": point["date"],
                    "open": point["open"],
                    "high": point["high"],
                    "low": point["low"],
                    "close": point["close"],
                    "volume": point["volume"],
                    "vwap": vwap,
                    "rsi": rsi_values[idx],
                    "reddit_mentions": reddit_point.get("mentions", 0),
                    "reddit_comments": reddit_point.get("comments", 0),
                    "reddit_avg_sentiment": reddit_point.get("avg_sentiment", 0.0),
                    "reddit_bullish_ratio": reddit_point.get("bullish_ratio", 0.0),
                    "reddit_bearish_ratio": reddit_point.get("bearish_ratio", 0.0),
                }
            )
        return window

    def _rsi_series(self, closes: list[float], period: int = 5) -> list[float]:
        values = []
        for idx in range(len(closes)):
            if idx == 0:
                values.append(50.0)
                continue
            start = max(1, idx - period + 1)
            deltas = [closes[i] - closes[i - 1] for i in range(start, idx + 1)]
            gains = [delta for delta in deltas if delta > 0]
            losses = [-delta for delta in deltas if delta < 0]
            avg_gain = sum(gains) / max(len(deltas), 1)
            avg_loss = sum(losses) / max(len(deltas), 1)
            if avg_loss == 0:
                values.append(100.0)
                continue
            rs = avg_gain / avg_loss
            values.append(round(100 - (100 / (1 + rs)), 2))
        return values

    def _extract_narratives(
        self,
        reddit_threads: list[dict[str, Any]],
        news_articles: list[dict[str, Any]],
        sec_items: list[dict[str, Any]],
    ) -> list[str]:
        narratives = []
        narratives.extend(thread.get("title", "") for thread in reddit_threads[:3])
        narratives.extend(article.get("title", "") for article in news_articles[:2])
        narratives.extend(item.get("summary", "") for item in sec_items[:1])
        return [item for item in narratives if item]

    def _build_agent_personas(
        self,
        ticker: str,
        reddit_sentiment: float,
        prediction_consensus: float,
        news_sentiment: float,
    ) -> list[dict[str, Any]]:
        tilt = "bullish" if reddit_sentiment >= 0 else "bearish"
        return [
            {
                "name": f"{ticker} Retail Momentum Trader",
                "archetype": "retail",
                "stance": tilt,
                "weight": round(0.4 + abs(reddit_sentiment), 2),
            },
            {
                "name": f"{ticker} Sell-Side Analyst",
                "archetype": "analyst",
                "stance": "constructive" if news_sentiment >= 0 else "cautious",
                "weight": round(0.3 + abs(news_sentiment), 2),
            },
            {
                "name": f"{ticker} Prediction Market Arb",
                "archetype": "arb",
                "stance": "risk-on" if prediction_consensus >= 0.55 else "risk-off",
                "weight": round(prediction_consensus, 2),
            },
            {
                "name": f"{ticker} Compliance Watcher",
                "archetype": "sec",
                "stance": "monitoring",
                "weight": 0.35,
            },
        ]
