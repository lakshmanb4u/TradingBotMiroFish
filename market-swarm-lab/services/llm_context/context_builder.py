"""Context Builder — Compact market data summary for LLM calls.

Rules:
- Max input tokens: <8k
- Max output tokens: <500
- No raw OHLCV arrays
- No full logs or chat history
- Structured summary only
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def build_compact_context(
    price: dict[str, Any],
    ind: dict[str, Any],
    ens: dict[str, Any],
    regime: dict[str, Any],
    uw_ctx: dict[str, Any],
    bar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact context dict for LLM consumption.
    
    Returns a structured summary with ONLY essential fields.
    """
    # Price snapshot
    price_summary = {
        "last_price": round(price.get("last_price", 0), 2),
        "change_pct": round(price.get("change_percent", 0), 2),
        "volume": price.get("volume", 0),
        "vwap": round(ind.get("vwap", 0), 2) if ind else None,
    }
    
    # Technical indicators (key values only)
    tech_summary = {}
    if ind:
        tech_summary = {
            "ema9": round(ind.get("ema9", 0), 2),
            "ema21": round(ind.get("ema21", 0), 2),
            "ema50": round(ind.get("ema50", 0), 2),
            "rsi14": round(ind.get("rsi14", 0), 1),
            "volume_ratio": round(ind.get("volume_ratio", 1.0), 2),
        }
    
    # Ensemble votes
    ensemble_summary = {
        "action": ens.get("action", "HOLD"),
        "votes_bull": ens.get("votes_bull", 0),
        "votes_bear": ens.get("votes_bear", 0),
        "score": ens.get("score", 0),
        "confidence": ens.get("confidence", "50%"),
    }
    
    # Agent breakdown (scores only, no raw data)
    agents = ens.get("agents", {})
    agent_summary = {}
    for name, data in agents.items():
        agent_summary[name] = {
            "vote": data.get("vote", "neutral"),
            "score": data.get("score", 0),
        }
    ensemble_summary["agents"] = agent_summary
    
    # Regime
    regime_summary = {
        "regime": regime.get("regime", "CHOP"),
        "confidence": regime.get("confidence", 50),
        "bull_score": regime.get("bull_score", 0),
        "bear_score": regime.get("bear_score", 0),
    }
    
    # UW context (if available)
    uw_summary = {
        "available": uw_ctx.get("available", False),
        "flow_bias": uw_ctx.get("flow_bias", "neutral"),
        "flow_score": uw_ctx.get("flow_score", 0),
    }
    
    # Entry/stop/target (risk params)
    risk_summary = {
        "entry": round(ens.get("entry", 0), 2),
        "stop_loss": round(ens.get("stop_loss", 0), 2),
        "target_1": round(ens.get("target_1", 0), 2),
        "target_2": round(ens.get("target_2", 0), 2),
        "risk_reward": ens.get("risk_reward", "N/A"),
    }
    
    # TimesFM (if available)
    timesfm_summary = {}
    if "timesfm" in regime:
        tf = regime["timesfm"]
        timesfm_summary = {
            "available": tf.get("available", False),
            "direction": tf.get("direction", "unavailable"),
            "confidence": tf.get("confidence", 0),
        }
    
    return {
        "timestamp": datetime.now().isoformat(),
        "ticker": price.get("ticker", "SPY"),
        "price": price_summary,
        "technical": tech_summary,
        "ensemble": ensemble_summary,
        "regime": regime_summary,
        "unusual_whales": uw_summary,
        "risk_params": risk_summary,
        "timesfm": timesfm_summary,
    }


def format_for_llm(context: dict[str, Any]) -> str:
    """Format compact context into a concise string for LLM prompt.
    
    Target: <2k tokens (well under 8k limit).
    """
    lines = [
        f"Ticker: {context['ticker']} @ {context['price']['last_price']}",
        f"Change: {context['price']['change_pct']}% | Volume: {context['price']['volume']:,}",
        f"VWAP: {context['price']['vwap']} | RSI: {context['technical'].get('rsi14', 'N/A')}",
        f"EMA9: {context['technical'].get('ema9', 'N/A')} | EMA21: {context['technical'].get('ema21', 'N/A')}",
        f"Vol Ratio: {context['technical'].get('volume_ratio', 'N/A')}",
        "",
        f"Regime: {context['regime']['regime']} (conf: {context['regime']['confidence']}%)",
        f"TimesFM: {context['timesfm'].get('direction', 'N/A')} (conf: {context['timesfm'].get('confidence', 0)})",
        "",
        f"Ensemble: {context['ensemble']['action']} | Score: {context['ensemble']['score']}",
        f"Votes: bull={context['ensemble']['votes_bull']} bear={context['ensemble']['votes_bear']}",
        f"Confidence: {context['ensemble']['confidence']}",
        "",
        "Agent Votes:",
    ]
    
    for name, data in context["ensemble"].get("agents", {}).items():
        lines.append(f"  {name}: {data['vote']} (score={data['score']})")
    
    lines.extend([
        "",
        f"UW Flow: {context['unusual_whales']['flow_bias']} (score={context['unusual_whales']['flow_score']})",
        "",
        "Risk Params:",
        f"  Entry: {context['risk_params']['entry']}",
        f"  Stop: {context['risk_params']['stop_loss']}",
        f"  T1: {context['risk_params']['target_1']} | T2: {context['risk_params']['target_2']}",
        f"  R:R: {context['risk_params']['risk_reward']}",
    ])
    
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def validate_context_size(context: dict[str, Any] | str, max_tokens: int = 8000) -> tuple[bool, int]:
    """Validate context is under token limit.
    
    Returns: (is_valid, token_count)
    """
    if isinstance(context, dict):
        text = format_for_llm(context)
    else:
        text = context
    
    tokens = estimate_tokens(text)
    return tokens <= max_tokens, tokens
