from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from .db import init_infra
from .workflow import run_ticker_workflow


class RunResponse(BaseModel):
    ticker: str
    provider_modes: dict
    raw: dict
    normalized: dict
    forecast: dict
    simulation: dict
    report: dict


class RunRequest(BaseModel):
    persist: bool = Field(default=True)


app = FastAPI(title="market-swarm-lab", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    init_infra()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "market-swarm-lab"}


@app.post("/v1/tickers/{ticker}/run", response_model=RunResponse)
def run_ticker(ticker: str, request: RunRequest | None = None) -> dict:
    persist = True if request is None else request.persist
    return run_ticker_workflow(ticker=ticker, persist=persist)


@app.get("/debug/price")
def debug_price(ticker: str = Query(default="SPY")) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    pc_dir = str(ROOT / "services" / "price-collector")
    if pc_dir not in sys.path:
        sys.path.insert(0, pc_dir)

    from price_service import PriceService

    data = PriceService().collect(ticker)
    return {
        "provider_mode": data.get("provider_mode"),
        "sample_series": data.get("series", [])[-5:],
        "close_prices": data.get("close_prices", [])[-10:],
        "daily_returns": data.get("daily_returns", [])[-10:],
        "rolling_volatility_5d": data.get("rolling_volatility_5d"),
        "rolling_volatility_10d": data.get("rolling_volatility_10d"),
        "momentum": data.get("momentum"),
        "price_trend": data.get("price_trend"),
        "vwap": data.get("vwap"),
        "rsi_14": data.get("rsi_14"),
        "source_audit": data.get("source_audit", {}),
        "raw_artifact_path": data.get("raw_artifact_path"),
        "parquet_path": data.get("parquet_path"),
    }


@app.get("/debug/news")
def debug_news(ticker: str = Query(default="SPY")) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    nc_dir = str(ROOT / "services" / "news-collector")
    if nc_dir not in sys.path:
        sys.path.insert(0, nc_dir)

    from news_collector_service import NewsCollectorService

    data = NewsCollectorService().collect(ticker)
    return {
        "provider_mode": data.get("provider_mode"),
        "sample_articles": data.get("articles", [])[:3],
        "headlines": data.get("headlines", [])[:5],
        "bullish_themes": data.get("bullish_themes", []),
        "bearish_themes": data.get("bearish_themes", []),
        "sentiment_score": data.get("sentiment_score"),
        "sentiment_label": data.get("sentiment_label"),
        "source_audit": data.get("source_audit", {}),
    }


@app.get("/debug/reddit")
def debug_reddit(ticker: str = Query(default="SPY")) -> dict:
    import sys
    import os
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    rc_dir = str(ROOT / "services" / "reddit-collector")
    if rc_dir not in sys.path:
        sys.path.insert(0, rc_dir)

    from reddit_collector_service import RedditCollectorService

    data = RedditCollectorService().collect_subreddit(ticker=ticker)
    posts = data.get("threads", [])
    comments = data.get("comments", [])
    return {
        "provider_mode": data.get("provider_mode"),
        "sample_posts": posts[:3],
        "sample_comments": comments[:5],
        "features": data.get("features", {}),
        "source_audit": data.get("source_audit", {}),
    }


@app.get("/run-demo")
def run_demo(ticker: str = Query(default="NVDA")) -> dict:
    import sys
    import os
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIRS = [
        ROOT / "services" / "collector",
        ROOT / "services" / "reddit-collector",
        ROOT / "services" / "normalizer",
        ROOT / "services" / "forecasting",
        ROOT / "services" / "mirofish-bridge",
        ROOT / "services" / "reporting",
        ROOT / "services" / "seed-builder",
        ROOT / "services" / "agent-seeder",
        ROOT / "services" / "price-collector",
        ROOT / "services" / "news-collector",
    ]
    for sd in SERVICE_DIRS:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from collector_service import MultiSourceCollector
    from normalizer_service import UnifiedNormalizerService
    from forecasting_service import TimesFMForecastingService
    from reddit_collector_service import RedditCollectorService
    from price_collector_service import PriceCollectorService
    from news_collector_service import NewsCollectorService
    from seed_builder_service import SeedBuilderService
    from agent_seeder_service import AgentSeederService
    from prompt_generator import SimulationPromptGenerator
    from mirofish_bridge_service import MiroFishBridgeService
    from unified_reporter import UnifiedReporter

    # 1. Collect
    raw_bundle = MultiSourceCollector().collect(ticker)

    # 2. Normalize
    normalized_bundle = UnifiedNormalizerService().normalize(ticker, raw_bundle)

    # 3. Forecast
    forecast = TimesFMForecastingService().forecast(ticker, normalized_bundle)

    # 4. Reddit — Apify → OAuth → fixture priority via collect_subreddit()
    reddit_data = RedditCollectorService().collect_subreddit(ticker=ticker)
    normalized_bundle["reddit"] = reddit_data

    # Enrich source_audit with reddit provenance
    threads = reddit_data.get("threads", [])
    provider_mode = reddit_data.get("provider_mode", "fixture_fallback")
    normalized_bundle.setdefault("source_audit", {})["reddit"] = {
        "status": "live" if provider_mode in ("apify_live", "oauth_live") else "fallback",
        "provider": (
            "apify" if provider_mode == "apify_live"
            else ("oauth" if provider_mode == "oauth_live" else "fixture")
        ),
        "record_count": len(threads),
        "sample_post_titles": [t.get("title", "") for t in threads[:3]],
    }

    # reddit_data is also used as reddit_seed for the unified reporter
    reddit_seed = reddit_data

    # 4b. Price — Alpha Vantage live → fixture
    try:
        price_data = PriceCollectorService().collect(ticker)
    except Exception as _price_exc:
        import logging as _logging
        _logging.getLogger(__name__).error("Price collection failed: %s", _price_exc)
        price_data = {
            "ticker": ticker.upper(), "provider_mode": "fixture_fallback",
            "series": [], "close_prices": [], "returns": [],
            "volatility": 0.0, "avg_volume": 0.0, "price_trend": "flat",
            "source_audit": {"ohlcv": {"status": "fallback", "provider": "fixture",
                                       "record_count": 0, "date_range": {"from": "", "to": ""}}},
        }
    normalized_bundle["price"] = price_data
    normalized_bundle.setdefault("source_audit", {})["ohlcv"] = price_data["source_audit"]["ohlcv"]

    if "snapshot" in normalized_bundle:
        normalized_bundle["snapshot"]["close_prices"] = price_data.get("close_prices", [])
        normalized_bundle["snapshot"]["returns"] = price_data.get("returns", [])
        normalized_bundle["snapshot"]["volatility"] = price_data.get("volatility", 0.0)
        normalized_bundle["snapshot"]["price_trend"] = price_data.get("price_trend", "flat")

    try:
        from price_service import PriceService
        price_rich = PriceService().collect(ticker)
        if "snapshot" in normalized_bundle:
            snap = normalized_bundle["snapshot"]
            snap["rsi_14"] = price_rich.get("rsi_14", 50.0)
            snap["rolling_volatility_5d"] = price_rich.get("rolling_volatility_5d", 0.0)
            snap["rolling_volatility_10d"] = price_rich.get("rolling_volatility_10d", 0.0)
            snap["momentum"] = price_rich.get("momentum", 0.0)
            snap["vwap"] = price_rich.get("vwap", 0.0)
        normalized_bundle["price_rich"] = price_rich
    except Exception as _pe:
        pass

    # 4c. News — NewsAPI live → AV news → fixture
    try:
        news_data = NewsCollectorService().collect(ticker)
    except Exception as _news_exc:
        import logging as _logging
        _logging.getLogger(__name__).error("News collection failed: %s", _news_exc)
        news_data = {
            "ticker": ticker.upper(), "provider_mode": "fixture_fallback",
            "articles": [], "headlines": [], "summaries": [],
            "bullish_themes": [], "bearish_themes": [],
            "sentiment_score": 0.0, "sentiment_label": "neutral",
            "source_audit": {"news": {"status": "fallback", "provider": "fixture",
                                      "record_count": 0, "sample_headlines": []}},
        }
    normalized_bundle["news"] = news_data
    normalized_bundle.setdefault("source_audit", {})["news"] = news_data["source_audit"]["news"]

    # 5. Build seed (build_reddit_context called internally via normalized_bundle["reddit"])
    seed = SeedBuilderService().build(ticker, normalized_bundle, forecast)

    # 6. Seed agents
    seeder = AgentSeederService()
    agent_roster_result = seeder.seed_agents(seed, forecast, normalized_bundle)

    # 7. Run simulation
    simulation_result = seeder.run_simulation(agent_roster_result)

    # 8. Generate prompt
    prompt_result = SimulationPromptGenerator().generate(ticker, seed, agent_roster_result)

    # 9. MiroFish simulate
    mirofish_result = MiroFishBridgeService().simulate(prompt_result["mirofish_seed_packet"])

    # Merge simulation results into mirofish_result for unified reporter
    if "final_direction" not in mirofish_result:
        mirofish_result["final_direction"] = simulation_result.get("final_direction", "rangebound")
    if "outlook_score" not in mirofish_result:
        mirofish_result["outlook_score"] = simulation_result.get("buy_sell_ratio", 1.0)

    # 10. Generate trade signal
    trade_signal = seeder.generate_trade_signal(simulation_result, forecast, normalized_bundle)

    # 11. Unified report
    report = UnifiedReporter().report(
        ticker, forecast, reddit_seed, normalized_bundle, mirofish_result, seed,
        trade_signal=trade_signal,
    )

    return {
        "ticker": ticker.upper(),
        "raw_bundle_keys": list(raw_bundle.keys()),
        "forecast": forecast,
        "reddit_seed": reddit_seed,
        "seed": seed,
        "agent_roster_summary": {
            "total_agents": len(agent_roster_result.get("agent_roster", [])),
            "archetypes": {k: v["count"] for k, v in agent_roster_result.get("archetypes", {}).items()},
        },
        "simulation": simulation_result,
        "prompt": {
            "simulation_prompt": prompt_result["simulation_prompt"],
            "agent_summary": prompt_result["agent_summary"],
        },
        "mirofish": mirofish_result,
        "report": report,
        "trade_signal": trade_signal,
    }
