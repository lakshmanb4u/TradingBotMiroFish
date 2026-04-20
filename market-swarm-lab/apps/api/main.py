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


@app.get("/debug/timesfm")
def debug_timesfm(ticker: str = Query(default="SPY")) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    for sd in [
        ROOT / "services" / "price-collector",
        ROOT / "services" / "forecasting",
        ROOT / "services" / "news-collector",
        ROOT / "services" / "seed-builder",
    ]:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from price_service import PriceService
    from forecasting_service import TimesFMForecastingService

    features = PriceService().features(ticker)
    close_prices = features["close_prices"]
    result = TimesFMForecastingService().forecast_from_prices(ticker, close_prices)
    return {
        **result,
        "input_length": len(close_prices),
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
        ROOT / "services" / "strategy-engine",
        ROOT / "services" / "risk-engine",
        ROOT / "services" / "execution-engine",
        ROOT / "services" / "portfolio-engine",
        ROOT / "services" / "backtester",
        ROOT / "services" / "macro-collector",
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

    # 4b2. Intraday — Massive.com live → fixture
    try:
        from intraday_service import IntradayService
        intraday = IntradayService().collect_intraday(ticker)
        normalized_bundle["intraday"] = intraday
        normalized_bundle.setdefault("source_audit", {})["intraday"] = intraday["source_audit"]["intraday"]
    except Exception:
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

    # 4d. TimesFM with real prices
    try:
        close_prices = (
            normalized_bundle.get("snapshot", {}).get("close_prices")
            or normalized_bundle.get("price", {}).get("close_prices", [])
        )
        if close_prices:
            timesfm_rich = TimesFMForecastingService().forecast_from_prices(ticker, close_prices)
            normalized_bundle["timesfm"] = timesfm_rich
    except Exception:
        pass

    # 4e. NewsService — richer news with sentiment + persistence
    try:
        from news_service import NewsService
        news_rich = NewsService().collect(ticker)
        normalized_bundle["news"] = news_rich
        normalized_bundle.setdefault("source_audit", {})["news"] = news_rich["source_audit"]["news"]
    except Exception:
        pass

    # 4g. Macro + sentiment collectors
    try:
        from vix_service import VIXService
        from fred_service import FREDService
        from stocktwits_service import StockTwitsService
        from earnings_calendar_service import EarningsCalendarService
        from reddit_spy_service import RedditSPYService

        vix_data = VIXService().collect()
        normalized_bundle["vix"] = vix_data
        normalized_bundle.setdefault("source_audit", {})["vix"] = vix_data["source_audit"]["vix"]

        macro_data = FREDService().collect()
        normalized_bundle["macro"] = macro_data
        normalized_bundle.setdefault("source_audit", {})["macro"] = macro_data["source_audit"]["macro"]

        st_data = StockTwitsService().collect(ticker)
        normalized_bundle["stocktwits"] = st_data

        earnings_data = EarningsCalendarService().collect(ticker)
        normalized_bundle["earnings"] = earnings_data
        normalized_bundle.setdefault("source_audit", {})["earnings"] = earnings_data["source_audit"]["earnings"]

        reddit_spy_data = RedditSPYService().collect(ticker)
        normalized_bundle["reddit_spy"] = reddit_spy_data
        normalized_bundle.setdefault("source_audit", {})["reddit_spy"] = reddit_spy_data["source_audit"]["reddit_spy"]
    except Exception as _me:
        pass

    # 4f. Divergence detection
    try:
        from divergence_engine import compute_divergence
        _reddit_for_div = normalized_bundle.get("reddit", {})
        _kalshi_for_div = normalized_bundle.get("snapshot", {}).get("kalshi_contracts")
        _forecast_for_div = normalized_bundle.get("timesfm") or forecast
        divergence = compute_divergence(_forecast_for_div, _reddit_for_div, _kalshi_for_div)
        normalized_bundle["divergence"] = divergence
    except Exception:
        pass

    # Part 8: ensure complete source_audit for all 5 sources
    _sa = normalized_bundle.setdefault("source_audit", {})

    if "price" in normalized_bundle:
        _sa.setdefault("ohlcv", normalized_bundle["price"].get("source_audit", {}).get("ohlcv", {
            "status": "fallback", "provider": "fixture", "record_count": 0,
        }))

    _snap_for_audit = normalized_bundle.get("snapshot", {})
    _sa.setdefault("sec", {
        "status": "live" if _snap_for_audit.get("sec_risk_score") is not None else "fallback",
        "provider": "sec_api",
        "record_count": len(
            normalized_bundle.get("simulation_seed", {}).get("sec_digest", [])
        ),
        "sample_ids": [],
    })

    _kalshi_contracts_audit = _snap_for_audit.get("kalshi_contracts", [])
    _sa.setdefault("kalshi", {
        "status": "live" if _kalshi_contracts_audit else "fallback",
        "provider": "kalshi",
        "record_count": len(_kalshi_contracts_audit),
        "sample_ids": [c.get("ticker_kalshi", "") for c in _kalshi_contracts_audit[:3]],
    })

    # 5. Build seed (build_reddit_context called internally via normalized_bundle["reddit"])
    seed = SeedBuilderService().build(ticker, normalized_bundle, forecast)

    # seed_pack (also built internally by build(), but ensure it's set)
    try:
        seed_pack = SeedBuilderService().build_seed_pack(ticker, normalized_bundle)
        seed["seed_pack"] = seed_pack
    except Exception:
        pass

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

    # 10b. Strategy + risk engine
    strategy_signal: dict = {}
    risk_eval: dict = {}
    try:
        from strategy_engine_service import StrategyEngineService
        from risk_engine_service import RiskEngineService
        strategy_context = {
            "timesfm": normalized_bundle.get("timesfm") or forecast,
            "divergence": normalized_bundle.get("divergence", {}),
            "price": normalized_bundle.get("price_rich") or normalized_bundle.get("price", {}),
            "reddit": normalized_bundle.get("reddit", {}),
            "news": normalized_bundle.get("news", {}),
            "simulation": simulation_result,
            "source_audit": normalized_bundle.get("source_audit", {}),
            "intraday": normalized_bundle.get("intraday", {}),
        }
        strategy_signal = StrategyEngineService().generate_signal(ticker, strategy_context, horizon="1d")
        risk_eval = RiskEngineService().evaluate(strategy_signal, strategy_context)
    except Exception:
        pass

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
        "seed_pack": seed.get("seed_pack", {}),
        "divergence": normalized_bundle.get("divergence", {}),
        "source_audit": normalized_bundle.get("source_audit", {}),
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
        "strategy_signal": strategy_signal,
        "risk_eval": risk_eval,
    }


def _build_signal_context(ticker: str, ROOT: "Path") -> tuple[dict, dict]:
    """Shared helper: collect live context and return (normalized_bundle, strategy_context)."""
    import sys
    from pathlib import Path as _Path

    SERVICE_DIRS = [
        ROOT / "services" / "price-collector",
        ROOT / "services" / "news-collector",
        ROOT / "services" / "reddit-collector",
        ROOT / "services" / "forecasting",
        ROOT / "services" / "seed-builder",
    ]
    for sd in SERVICE_DIRS:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    nb: dict = {"source_audit": {}}

    try:
        from price_service import PriceService
        price_rich = PriceService().collect(ticker)
        nb["price_rich"] = price_rich
        nb["source_audit"]["ohlcv"] = price_rich.get("source_audit", {}).get(
            "ohlcv", {"status": "fallback", "provider": "fixture", "record_count": 0}
        )
        close_prices = price_rich.get("close_prices", [])
    except Exception:
        nb["source_audit"]["ohlcv"] = {"status": "fallback", "provider": "fixture", "record_count": 0}
        close_prices = []

    try:
        from news_service import NewsService
        news_rich = NewsService().collect(ticker)
        nb["news"] = news_rich
        nb["source_audit"]["news"] = news_rich.get("source_audit", {}).get("news", {"status": "fallback"})
    except Exception:
        try:
            from news_collector_service import NewsCollectorService
            news_data = NewsCollectorService().collect(ticker)
            nb["news"] = news_data
            nb["source_audit"]["news"] = news_data.get("source_audit", {}).get("news", {"status": "fallback"})
        except Exception:
            nb["source_audit"]["news"] = {"status": "fallback"}

    try:
        from reddit_collector_service import RedditCollectorService
        reddit_data = RedditCollectorService().collect_subreddit(ticker=ticker)
        nb["reddit"] = reddit_data
        provider_mode = reddit_data.get("provider_mode", "fixture_fallback")
        nb["source_audit"]["reddit"] = {
            "status": "live" if provider_mode in ("apify_live", "oauth_live") else "fallback",
        }
    except Exception:
        pass

    try:
        from forecasting_service import TimesFMForecastingService
        if close_prices:
            timesfm = TimesFMForecastingService().forecast_from_prices(ticker, close_prices)
            nb["timesfm"] = timesfm
    except Exception:
        pass

    try:
        from divergence_engine import compute_divergence
        divergence = compute_divergence(
            nb.get("timesfm", {}),
            nb.get("reddit", {}),
            None,
        )
        nb["divergence"] = divergence
    except Exception:
        pass

    try:
        from intraday_service import IntradayService
        intraday = IntradayService().collect_intraday(ticker)
        nb["intraday"] = intraday
        nb["source_audit"]["intraday"] = intraday["source_audit"]["intraday"]
    except Exception:
        pass

    strategy_context = {
        "timesfm": nb.get("timesfm", {}),
        "divergence": nb.get("divergence", {}),
        "price": nb.get("price_rich", {}),
        "reddit": nb.get("reddit", {}),
        "news": nb.get("news", {}),
        "simulation": {"final_confidence": 0.5},
        "source_audit": nb.get("source_audit", {}),
        "intraday": nb.get("intraday", {}),
    }
    return nb, strategy_context


@app.get("/signal")
def get_signal(
    ticker: str = Query(default="SPY"),
    horizon: str = Query(default="1d"),
) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    for sd in [
        ROOT / "services" / "strategy-engine",
        ROOT / "services" / "risk-engine",
    ]:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from strategy_engine_service import StrategyEngineService
    from risk_engine_service import RiskEngineService

    try:
        _nb, strategy_context = _build_signal_context(ticker, ROOT)
        signal = StrategyEngineService().generate_signal(ticker, strategy_context, horizon)
        risk = RiskEngineService().evaluate(signal, strategy_context)
        return {"signal": signal, "risk": risk}
    except Exception as exc:
        return {"error": str(exc), "signal": {}, "risk": {}}


@app.get("/trade-plan")
def get_trade_plan(
    ticker: str = Query(default="SPY"),
    horizon: str = Query(default="1d"),
) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    for sd in [
        ROOT / "services" / "strategy-engine",
        ROOT / "services" / "risk-engine",
        ROOT / "services" / "execution-engine",
        ROOT / "services" / "portfolio-engine",
    ]:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from strategy_engine_service import StrategyEngineService
    from risk_engine_service import RiskEngineService
    from execution_engine_service import ExecutionEngineService
    from portfolio_engine_service import PortfolioEngineService

    try:
        _nb, strategy_context = _build_signal_context(ticker, ROOT)
        signal = StrategyEngineService().generate_signal(ticker, strategy_context, horizon)
        risk = RiskEngineService().evaluate(signal, strategy_context)

        order_ticket: dict = {}
        portfolio_record: dict = {}
        if risk.get("approved"):
            order_ticket = ExecutionEngineService().execute(signal, risk, ticker)
            # Enrich order with signal metadata before recording
            enriched_order = {
                **order_ticket,
                "strategy_type": signal.get("strategy_type", ""),
                "drivers": signal.get("drivers", []),
                "source_audit_snapshot": strategy_context.get("source_audit", {}),
                "position_size_pct": risk.get("position_size_pct", 0.0),
            }
            portfolio_record = PortfolioEngineService().record_trade(enriched_order)

        return {
            "signal": signal,
            "risk": risk,
            "trade_plan": order_ticket,
            "portfolio_record": portfolio_record,
            "execution_mode": order_ticket.get("mode", "paper"),
        }
    except Exception as exc:
        return {"error": str(exc), "signal": {}, "risk": {}, "trade_plan": {}, "execution_mode": "paper"}


@app.get("/debug/macro")
def debug_macro() -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    mc_dir = str(ROOT / "services" / "macro-collector")
    if mc_dir not in sys.path:
        sys.path.insert(0, mc_dir)

    from vix_service import VIXService
    from fred_service import FREDService

    vix_data = VIXService().collect()
    macro_data = FREDService().collect()
    return {
        "vix": {
            "vix_current": vix_data.get("vix_current"),
            "vix_5d_avg": vix_data.get("vix_5d_avg"),
            "vix_regime": vix_data.get("vix_regime"),
            "vix_trend": vix_data.get("vix_trend"),
            "fear_signal": vix_data.get("fear_signal"),
            "source_audit": vix_data.get("source_audit", {}),
        },
        "fred": {
            "fed_funds_rate": macro_data.get("fed_funds_rate"),
            "yield_curve": macro_data.get("yield_curve"),
            "yield_curve_signal": macro_data.get("yield_curve_signal"),
            "credit_spread": macro_data.get("credit_spread"),
            "credit_signal": macro_data.get("credit_signal"),
            "consumer_sentiment": macro_data.get("consumer_sentiment"),
            "macro_regime": macro_data.get("macro_regime"),
            "source_audit": macro_data.get("source_audit", {}),
        },
    }


@app.get("/debug/intraday")
def debug_intraday(ticker: str = Query(default="SPY")) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    pc_dir = str(ROOT / "services" / "price-collector")
    if pc_dir not in sys.path:
        sys.path.insert(0, pc_dir)

    from intraday_service import IntradayService

    data = IntradayService().collect_intraday(ticker)
    return {
        "bars": data.get("bars", [])[-10:],
        "intraday_trend": data.get("intraday_trend"),
        "intraday_momentum": data.get("intraday_momentum"),
        "intraday_volatility": data.get("intraday_volatility"),
        "source_audit": data.get("source_audit", {}),
    }


@app.get("/debug/sentiment")
def debug_sentiment(ticker: str = Query(default="SPY")) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    mc_dir = str(ROOT / "services" / "macro-collector")
    if mc_dir not in sys.path:
        sys.path.insert(0, mc_dir)

    from stocktwits_service import StockTwitsService
    from reddit_spy_service import RedditSPYService

    st_data = StockTwitsService().collect(ticker)
    reddit_spy_data = RedditSPYService().collect(ticker)
    return {
        "stocktwits": {
            "ticker": st_data.get("ticker"),
            "bullish_count": st_data.get("bullish_count"),
            "bearish_count": st_data.get("bearish_count"),
            "sentiment_score": st_data.get("sentiment_score"),
            "sentiment_label": st_data.get("sentiment_label"),
            "message_volume": st_data.get("message_volume"),
            "sample_messages": st_data.get("sample_messages", []),
            "source_audit": st_data.get("source_audit", {}),
        },
        "reddit_spy": {
            "ticker": reddit_spy_data.get("ticker"),
            "subreddits_fetched": reddit_spy_data.get("subreddits_fetched"),
            "total_posts": reddit_spy_data.get("total_posts"),
            "relevant_posts": reddit_spy_data.get("relevant_posts"),
            "bullish_pct": reddit_spy_data.get("bullish_pct"),
            "bearish_pct": reddit_spy_data.get("bearish_pct"),
            "avg_sentiment": reddit_spy_data.get("avg_sentiment"),
            "sentiment_label": reddit_spy_data.get("sentiment_label"),
            "top_posts": reddit_spy_data.get("top_posts", [])[:5],
            "key_themes": reddit_spy_data.get("key_themes", []),
            "source_audit": reddit_spy_data.get("source_audit", {}),
        },
    }


@app.post("/backtest")
def run_backtest(
    ticker: str = Query(default="SPY"),
    horizon: str = Query(default="1d"),
) -> dict:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    for sd in [
        ROOT / "services" / "backtester",
        ROOT / "services" / "strategy-engine",
        ROOT / "services" / "risk-engine",
        ROOT / "services" / "forecasting",
    ]:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from backtester_service import BacktesterService

    try:
        result = BacktesterService().run(ticker, horizon)
        return result
    except Exception as exc:
        return {"error": str(exc), "ticker": ticker.upper(), "horizon": horizon}
