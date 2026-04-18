from __future__ import annotations

from . import bootstrap  # noqa: F401
from .db import cache_report, persist_run_summary

from collector_service import MultiSourceCollector
from forecasting_service import TimesFMForecastingService
from mirofish_bridge_service import MiroFishBridgeService
from normalizer_service import UnifiedNormalizerService
from reporting_service import ReportingService


def run_ticker_workflow(ticker: str, persist: bool = True) -> dict:
    collector = MultiSourceCollector()
    normalizer = UnifiedNormalizerService()
    forecasting = TimesFMForecastingService()
    mirofish_bridge = MiroFishBridgeService()
    reporting = ReportingService()

    raw_bundle = collector.collect(ticker)
    normalized = normalizer.normalize(ticker=ticker, raw_bundle=raw_bundle)
    forecast = forecasting.forecast(ticker=ticker, normalized_bundle=normalized)
    simulation = mirofish_bridge.run(ticker=ticker, normalized_bundle=normalized, forecast=forecast)
    report = reporting.generate(
        ticker=ticker,
        raw_bundle=raw_bundle,
        normalized_bundle=normalized,
        forecast=forecast,
        simulation=simulation,
    )

    payload = {
        "ticker": ticker.upper(),
        "provider_modes": {
            "collector": raw_bundle.get("provider_modes", {}),
            "forecasting": forecast.get("provider_mode"),
            "mirofish_bridge": simulation.get("provider_mode"),
        },
        "raw": raw_bundle,
        "normalized": normalized,
        "forecast": forecast,
        "simulation": simulation,
        "report": report,
    }

    if persist:
        cache_report(ticker, payload)
        persist_run_summary(ticker, payload)

    return payload
