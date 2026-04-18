"""
Unified normalizer service.

Converts raw collector bundles into:
  - documents     (cleaned text, labeled sentiment)
  - time_series   (aligned numeric rows)
  - market_signals (snapshot scalars)

Reddit content is typed as reddit_post / reddit_comment with comment trees preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean
from typing import Any

_REDDIT_NLP = str(Path(__file__).resolve().parents[1] / "reddit-collector")
if _REDDIT_NLP not in sys.path:
    sys.path.insert(0, _REDDIT_NLP)

from nlp import build_comment_tree, extract_features, score_text  # noqa: E402


class UnifiedNormalizerService:
    def normalize(self, ticker: str, raw_bundle: dict[str, Any]) -> dict[str, Any]:
        market_series = raw_bundle["market_data"]["series"]
        reddit_data   = raw_bundle["reddit"]
        news_articles = raw_bundle["news"].get("articles", [])
        sec_items     = raw_bundle["sec_filings"].get("filings", [])
        pred_markets  = raw_bundle["prediction_markets"].get("markets", [])
        reddit_threads = reddit_data.get("threads", [])
        reddit_activity = reddit_data.get("activity", [])

        documents    = self._build_documents(reddit_threads, news_articles, sec_items)
        time_series  = self._build_feature_window(market_series, reddit_activity)
        market_sigs  = self._build_market_signals(
            time_series, reddit_activity, news_articles, sec_items, pred_markets
        )

        reddit_feats = extract_features(reddit_threads) if reddit_threads else _empty_reddit_feats()

        return {
            "ticker": ticker.upper(),
            "snapshot": {
                "latest_close":               market_sigs["latest_close"],
                "latest_vwap":                market_sigs["latest_vwap"],
                "latest_rsi":                 market_sigs["latest_rsi"],
                "reddit_sentiment":           market_sigs["reddit_sentiment"],
                "reddit_mentions":            market_sigs["reddit_mentions"],
                "reddit_disagreement_index":  reddit_feats["disagreement_index"],
                "news_sentiment":             market_sigs["news_sentiment"],
                "prediction_market_consensus": market_sigs["prediction_market_consensus"],
                "sec_risk_score":             market_sigs["sec_risk_score"],
            },
            "documents":      documents,
            "feature_window": time_series,
            "timesfm_inputs": {
                "target_series": [row["close"] for row in time_series],
                "numeric_features": [
                    {
                        "date":                  row["date"],
                        "volume":                row["volume"],
                        "vwap":                  row["vwap"],
                        "rsi":                   row["rsi"],
                        "reddit_mentions":       row["reddit_mentions"],
                        "reddit_comments":       row["reddit_comments"],
                        "reddit_avg_sentiment":  row["reddit_avg_sentiment"],
                        "reddit_bullish_ratio":  row["reddit_bullish_ratio"],
                        "reddit_bearish_ratio":  row["reddit_bearish_ratio"],
                        "reddit_disagreement":   row.get("reddit_disagreement_index", 0.0),
                        "reddit_engagement_vel": row.get("reddit_engagement_velocity", 0.0),
                    }
                    for row in time_series
                ],
            },
            "simulation_seed": {
                "retail_sentiment": {
                    "score":          market_sigs["reddit_sentiment"],
                    "bullish_ratio":  reddit_feats["bullish_ratio"],
                    "bearish_ratio":  reddit_feats["bearish_ratio"],
                    "disagreement":   reddit_feats["disagreement_index"],
                    "subreddit_activity": reddit_threads[:5],
                },
                "agent_personas": self._build_personas(
                    ticker, market_sigs, reddit_feats
                ),
                "key_narratives": [d["text"][:120] for d in documents[:6] if d.get("text")],
                "news_digest":    news_articles[:3],
                "sec_digest":     sec_items[:2],
                "prediction_markets": pred_markets[:3],
            },
        }

    # ─────────────────────────── documents

    def _build_documents(
        self,
        reddit_threads: list[dict],
        news_articles: list[dict],
        sec_items: list[dict],
    ) -> list[dict[str, Any]]:
        docs: list[dict] = []

        for thread in reddit_threads:
            title = thread.get("title", "")
            body  = thread.get("body", "")
            text  = (title + " " + body).strip()
            sent  = score_text(text)
            doc: dict[str, Any] = {
                "type":      "reddit_post",
                "source":    thread.get("subreddit", "reddit"),
                "text":      text,
                "sentiment": sent["sentiment"],
                "label":     sent["label"],
                "score":     thread.get("score", 0),
                "url":       thread.get("permalink", ""),
            }
            raw_comments = thread.get("comments", [])
            if raw_comments:
                flat_comments = [
                    {
                        "id":    c.get("id", "") if isinstance(c, dict) else "",
                        "parent_id": c.get("parent_id", "") if isinstance(c, dict) else "",
                        "body":  c.get("body", c) if isinstance(c, dict) else str(c),
                        "score": c.get("score", 0) if isinstance(c, dict) else 0,
                    }
                    for c in raw_comments
                ]
                doc["comment_tree"] = build_comment_tree(flat_comments)
                doc["comment_docs"] = [
                    {
                        "type":      "reddit_comment",
                        "source":    thread.get("subreddit", "reddit"),
                        "text":      c.get("body", ""),
                        "sentiment": score_text(c.get("body", ""))["sentiment"],
                        "label":     score_text(c.get("body", ""))["label"],
                        "depth":     c.get("depth", 0),
                    }
                    for c in raw_comments[:10]
                    if isinstance(c, dict)
                ]
            docs.append(doc)

        for article in news_articles:
            text = (article.get("title", "") + " " + (article.get("summary") or "")).strip()
            sent = score_text(text)
            docs.append({
                "type":      "news_article",
                "source":    article.get("source", "news"),
                "text":      text,
                "sentiment": article.get("sentiment", sent["sentiment"]),
                "label":     sent["label"],
                "published_at": article.get("published_at", ""),
            })

        for filing in sec_items:
            text = filing.get("summary", "")
            sent = score_text(text)
            docs.append({
                "type":      "sec_filing",
                "source":    "edgar",
                "form":      filing.get("form", ""),
                "text":      text,
                "sentiment": sent["sentiment"],
                "label":     sent["label"],
                "risk_score": filing.get("risk_score", 0.0),
                "filed_at":  filing.get("filed_at", ""),
            })

        return docs

    # ─────────────────────────── time series

    def _build_feature_window(
        self,
        market_series: list[dict],
        reddit_activity: list[dict],
    ) -> list[dict[str, Any]]:
        closes     = [p["close"] for p in market_series]
        rsi_vals   = self._rsi_series(closes)
        by_date    = {a["date"]: a for a in reddit_activity}
        latest_r   = reddit_activity[-1] if reddit_activity else {}

        rows = []
        for idx, pt in enumerate(market_series):
            r = by_date.get(pt["date"], latest_r)
            vwap = round(pt.get("vwap") or ((pt["high"] + pt["low"] + pt["close"]) / 3), 4)
            rows.append({
                "date":                      pt["date"],
                "open":                      pt["open"],
                "high":                      pt["high"],
                "low":                       pt["low"],
                "close":                     pt["close"],
                "volume":                    pt["volume"],
                "vwap":                      vwap,
                "rsi":                       rsi_vals[idx],
                "reddit_mentions":           r.get("mentions", 0),
                "reddit_comments":           r.get("comments", 0),
                "reddit_avg_sentiment":      r.get("avg_sentiment", 0.0),
                "reddit_bullish_ratio":      r.get("bullish_ratio", 0.0),
                "reddit_bearish_ratio":      r.get("bearish_ratio", 0.0),
                "reddit_disagreement_index": r.get("disagreement_index", 0.0),
                "reddit_engagement_velocity": r.get("engagement_velocity", 0.0),
            })
        return rows

    def _rsi_series(self, closes: list[float], period: int = 5) -> list[float]:
        vals = [50.0]
        for i in range(1, len(closes)):
            start  = max(1, i - period + 1)
            deltas = [closes[j] - closes[j - 1] for j in range(start, i + 1)]
            gains  = [d for d in deltas if d > 0]
            losses = [-d for d in deltas if d < 0]
            ag = sum(gains) / max(len(deltas), 1)
            al = sum(losses) / max(len(deltas), 1)
            vals.append(100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2))
        return vals

    # ─────────────────────────── market signals

    def _build_market_signals(
        self,
        time_series: list[dict],
        reddit_activity: list[dict],
        news_articles: list[dict],
        sec_items: list[dict],
        pred_markets: list[dict],
    ) -> dict[str, Any]:
        latest = time_series[-1] if time_series else {}
        r      = reddit_activity[-1] if reddit_activity else {}
        news_s = round(mean(a.get("sentiment", 0.0) for a in news_articles), 3) if news_articles else 0.0
        sec_r  = round(mean(f.get("risk_score", 0.0) for f in sec_items), 3) if sec_items else 0.0
        pred_c = round(mean(m.get("probability_yes", 0.5) for m in pred_markets), 3) if pred_markets else 0.5
        return {
            "latest_close":               latest.get("close", 0.0),
            "latest_vwap":                latest.get("vwap", 0.0),
            "latest_rsi":                 latest.get("rsi", 50.0),
            "reddit_sentiment":           r.get("avg_sentiment", 0.0),
            "reddit_mentions":            r.get("mentions", 0),
            "news_sentiment":             news_s,
            "prediction_market_consensus": pred_c,
            "sec_risk_score":             sec_r,
        }

    # ─────────────────────────── personas

    def _build_personas(
        self,
        ticker: str,
        sigs: dict[str, Any],
        reddit_feats: dict[str, Any],
    ) -> list[dict[str, Any]]:
        tilt = "bullish" if sigs["reddit_sentiment"] >= 0 else "bearish"
        return [
            {"name": f"{ticker} Retail Momentum Trader", "archetype": "retail",   "stance": tilt,                                                                  "weight": round(0.4 + abs(sigs["reddit_sentiment"]), 2)},
            {"name": f"{ticker} Sell-Side Analyst",       "archetype": "analyst",  "stance": "constructive" if sigs["news_sentiment"] >= 0 else "cautious",          "weight": round(0.3 + abs(sigs["news_sentiment"]), 2)},
            {"name": f"{ticker} Prediction Market Arb",   "archetype": "arb",      "stance": "risk-on" if sigs["prediction_market_consensus"] >= 0.55 else "risk-off","weight": round(sigs["prediction_market_consensus"], 2)},
            {"name": f"{ticker} Compliance Watcher",      "archetype": "sec",      "stance": "monitoring",                                                           "weight": 0.35},
        ]


def _empty_reddit_feats() -> dict[str, Any]:
    return {
        "bullish_ratio": 0.0,
        "bearish_ratio": 0.0,
        "disagreement_index": 0.0,
        "avg_sentiment": 0.0,
        "post_count": 0,
        "comment_count": 0,
        "engagement_velocity": 0.0,
    }
