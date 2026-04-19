"""Divergence detection across forecast signals."""
from __future__ import annotations


def compute_divergence(
    forecast: dict,
    reddit_data: dict,
    kalshi_data: dict | list | None,
) -> dict:
    # 1. TimesFM direction → numeric score
    direction = forecast.get("direction", "neutral")
    _dir_map = {
        "bullish": 1.0, "up": 1.0,
        "neutral": 0.0, "sideways": 0.0,
        "bearish": -1.0, "down": -1.0,
    }
    timesfm_score = float(_dir_map.get(direction, 0.0))

    # 2. Reddit sentiment from bullish/bearish ratio
    features = reddit_data.get("features", {})
    bull = float(features.get("bullish_ratio", 0.0))
    bear = float(features.get("bearish_ratio", 0.0))
    reddit_score = float(max(-1.0, min(1.0, bull - bear)))

    # 3. Kalshi direction from avg YES probability
    kalshi_score = 0.0
    if kalshi_data:
        if isinstance(kalshi_data, list):
            contracts = kalshi_data
        else:
            contracts = [kalshi_data]
        yes_probs = [
            float(c.get("yes_price", c.get("probability_yes", 0.5)))
            for c in contracts
            if isinstance(c, dict)
        ]
        if yes_probs:
            avg_yes = sum(yes_probs) / len(yes_probs)
            if avg_yes > 0.55:
                kalshi_score = 1.0
            elif avg_yes < 0.45:
                kalshi_score = -1.0

    # Pairwise divergences (normalized 0-1)
    timesfm_vs_reddit = round(abs(timesfm_score - reddit_score) / 2.0, 4)
    timesfm_vs_kalshi = round(abs(timesfm_score - kalshi_score) / 2.0, 4) if kalshi_data else 0.0
    reddit_vs_kalshi = round(abs(reddit_score - kalshi_score) / 2.0, 4) if kalshi_data else 0.0

    divergences = [timesfm_vs_reddit]
    if kalshi_data:
        divergences.extend([timesfm_vs_kalshi, reddit_vs_kalshi])

    divergence_score = round(sum(divergences) / len(divergences), 4)
    alignment_score = round(1.0 - divergence_score, 4)

    if divergence_score > 0.5:
        signal = "reversal_candidate"
    elif alignment_score > 0.7:
        signal = "trend_confirmation"
    else:
        signal = "mixed"

    return {
        "timesfm_score": timesfm_score,
        "reddit_score": reddit_score,
        "kalshi_score": kalshi_score,
        "timesfm_vs_reddit": timesfm_vs_reddit,
        "timesfm_vs_kalshi": timesfm_vs_kalshi,
        "reddit_vs_kalshi": reddit_vs_kalshi,
        "divergence_score": divergence_score,
        "alignment_score": alignment_score,
        "signal": signal,
    }
