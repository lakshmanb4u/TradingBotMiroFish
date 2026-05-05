"""
Bar history loader for live trading.
Pre-loads intraday historical bars before each scan session.
Caches bars to disk for fast subsequent loads.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class BarHistoryLoader:
    """Loads and manages intraday bar history for live signal generation."""
    
    CACHE_DIR = Path("state/live/bars")
    
    # Minimum bars required for indicators
    MIN_BARS_EMA50 = 150      # 50 * 3 (EMA needs ~3x period)
    MIN_BARS_RSI = 30         # 14 * 2 + 1
    MIN_BARS_VWAP = 20
    MIN_BARS_ATR = 15         # 14 + 1
    MIN_BARS_VOLUME = 20      # for avg volume
    
    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    def load_bars(self, ticker: str, days_back: int = 5, freq_min: int = 5) -> tuple[list[dict], str, int]:
        """
        Load historical bars for ticker.
        
        Returns:
            (bars, source, count)
            bars: list of {ts, open, high, low, close, volume}
        """
        # 1. Try cache first
        bars, source = self._load_cache(ticker, freq_min)
        if bars:
            _log.info("[bars] %s: loaded %d bars from %s", ticker, len(bars), source)
            # Refresh cache with recent data
            return bars, source, len(bars)
        
        # 2. Try Schwab intraday
        bars = self._fetch_schwab(ticker, days_back, freq_min)
        if bars and len(bars) >= self.MIN_BARS_ATR:
            self._save_cache(ticker, freq_min, bars)
            return bars, "schwab", len(bars)
        
        # 3. Try yfinance fallback
        bars = self._fetch_yfinance(ticker, days_back, freq_min)
        if bars and len(bars) >= self.MIN_BARS_ATR:
            self._save_cache(ticker, freq_min, bars)
            return bars, "yfinance", len(bars)
        
        # 4. Return whatever we have (might be empty)
        return bars or [], "unavailable", len(bars) if bars else 0
    
    def _load_cache(self, ticker: str, freq_min: int) -> tuple[list[dict] | None, str]:
        """Try loading from parquet or JSON cache."""
        cache_file = self.CACHE_DIR / f"{ticker}_{freq_min}min.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                bars = data.get("bars", [])
                # Check freshness (less than 1 day old)
                cached_time = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
                age_hours = (datetime.now() - cached_time).total_seconds() / 3600
                if age_hours < 24:
                    return bars, f"cache:{cache_file.name}"
            except Exception as e:
                _log.warning("[bars] Cache load failed for %s: %s", ticker, e)
        return None, ""
    
    def _save_cache(self, ticker: str, freq_min: int, bars: list[dict]):
        """Save bars to JSON cache."""
        cache_file = self.CACHE_DIR / f"{ticker}_{freq_min}min.json"
        data = {
            "ticker": ticker,
            "freq_min": freq_min,
            "cached_at": datetime.now().isoformat(),
            "bar_count": len(bars),
            "bars": bars,
        }
        cache_file.write_text(json.dumps(data, indent=2, default=str))
    
    def _fetch_schwab(self, ticker: str, days_back: int, freq_min: int) -> list[dict] | None:
        """Fetch from Schwab API."""
        try:
            import requests
            from schwab_auth import get_valid_token
            
            token = get_valid_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            base = "https://api.schwabapi.com"
            
            end_date = date.today()
            start_date = end_date - timedelta(days=days_back + 2)
            
            all_bars = []
            d = start_date
            while d <= end_date:
                if d.weekday() >= 5:
                    d += timedelta(days=1)
                    continue
                try:
                    r = requests.get(
                        f"{base}/marketdata/v1/pricehistory",
                        headers=headers,
                        params={
                            "symbol": ticker,
                            "periodType": "day",
                            "period": 1,
                            "frequencyType": "minute",
                            "frequency": freq_min,
                            "needExtendedHoursData": "false",
                        },
                        timeout=15,
                    )
                    data = r.json()
                    candles = data.get("candles", [])
                    for c in candles:
                        ts = datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc)
                        all_bars.append({
                            "ts": ts.isoformat(),
                            "open": float(c["open"]),
                            "high": float(c["high"]),
                            "low": float(c["low"]),
                            "close": float(c["close"]),
                            "volume": int(c["volume"]),
                        })
                except Exception as e:
                    _log.warning("[bars] Schwab day %s failed: %s", d, e)
                d += timedelta(days=1)
            
            # Sort and filter
            all_bars.sort(key=lambda b: b["ts"])
            return all_bars
            
        except Exception as e:
            _log.warning("[bars] Schwab fetch failed: %s", e)
        return None
    
    def _fetch_yfinance(self, ticker: str, days_back: int, freq_min: int) -> list[dict] | None:
        """Fetch from yfinance."""
        try:
            import warnings
            warnings.filterwarnings("ignore")
            import yfinance as yf
            
            # yfinance intraday: max ~60 days for 5m
            t = yf.Ticker(ticker)
            hist = t.history(
                start=(date.today() - timedelta(days=days_back + 2)).isoformat(),
                end=(date.today() + timedelta(days=1)).isoformat(),
                interval=f"{freq_min}m",
            )
            if hist.empty:
                return None
            
            bars = []
            for idx, row in hist.iterrows():
                ts = idx.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                bars.append({
                    "ts": ts.isoformat(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                })
            
            bars.sort(key=lambda b: b["ts"])
            return bars
            
        except Exception as e:
            _log.warning("[bars] yfinance fetch failed: %s", e)
        return None
    
    def append_latest(self, bars: list[dict], price: float, volume: int) -> list[dict]:
        """Append latest quote as a synthetic bar."""
        now = datetime.now(timezone.utc)
        bars.append({
            "ts": now.isoformat(),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        })
        return bars
    
    def compute_indicators(self, bars: list[dict]) -> tuple[dict, list[str]]:
        """
        Compute indicators from bar history.
        
        Returns:
            (indicators_dict, ready_status)
        """
        if len(bars) < self.MIN_BARS_ATR:
            return {}, ["DATA_INSUFFICIENT"]
        
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        
        issues = []
        
        # EMA
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        ema50 = self._ema(closes, 50)
        
        if len(bars) < self.MIN_BARS_EMA50:
            ema50 = ema21  # fallback
            issues.append(f"EMA50_approx ({len(bars)} bars)")
        
        # VWAP (today's bars only)
        vwap = self._vwap(bars)
        
        # RSI
        rsi = self._rsi(closes, 14)
        if len(bars) < self.MIN_BARS_RSI:
            issues.append(f"RSI_approx ({len(bars)} bars)")
        
        # ATR
        atr = self._atr(bars)
        if len(bars) < self.MIN_BARS_ATR:
            issues.append(f"ATR_approx ({len(bars)} bars)")
        
        # Volume ratio
        avg_vol = self._avg_volume(volumes)
        latest_vol = volumes[-1] if volumes else 1
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0
        
        # Trend
        ema_bull = ema9 > ema21 if ema9 and ema21 else False
        above_ema9 = closes[-1] > ema9 if ema9 else False
        price_vs_vwap = "above" if closes[-1] > vwap else "below"
        
        indicators = {
            "last": closes[-1],
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "vwap": vwap,
            "rsi14": rsi,
            "atr14": atr,
            "avg_volume": avg_vol,
            "volume_ratio": round(vol_ratio, 3),
            "price_vs_vwap": price_vs_vwap,
            "ema_bull": ema_bull,
            "above_ema9": above_ema9,
        }
        
        return indicators, issues
    
    def _ema(self, prices: list[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2.0 / (period + 1)
        e = prices[0]
        for p in prices[1:]:
            e = p * k + e * (1 - k)
        return round(e, 4)
    
    def _vwap(self, bars: list[dict]) -> float:
        if not bars:
            return 0.0
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_bars = [b for b in bars if b["ts"].startswith(today_str)]
        if not today_bars:
            today_bars = bars[-20:]  # fallback
        
        total_cv = sum(((b["high"] + b["low"] + b["close"]) / 3) * b["volume"] for b in today_bars)
        total_vol = sum(b["volume"] for b in today_bars)
        return round(total_cv / total_vol, 4) if total_vol > 0 else 0.0
    
    def _rsi(self, prices: list[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
        losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)
    
    def _atr(self, bars: list[dict], period: int = 14) -> float:
        if len(bars) < 2:
            return bars[-1]["close"] * 0.005 if bars else 0.01
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i]["high"] - bars[i]["low"],
                abs(bars[i]["high"] - bars[i-1]["close"]),
                abs(bars[i]["low"] - bars[i-1]["close"]),
            )
            trs.append(tr)
        return round(sum(trs[-period:]) / len(trs[-period:]), 4) if trs else 0.01
    
    def _avg_volume(self, volumes: list[int]) -> float:
        if not volumes:
            return 1.0
        return sum(volumes[-20:]) / len(volumes[-20:])
    
    def get_data_status(self, ticker: str, bars: list[dict]) -> dict:
        """Get data readiness status for a ticker."""
        count = len(bars)
        return {
            "ticker": ticker,
            "bars_loaded": count,
            "ema50_ready": count >= self.MIN_BARS_EMA50,
            "rsi_ready": count >= self.MIN_BARS_RSI,
            "vwap_ready": count >= self.MIN_BARS_VWAP,
            "atr_ready": count >= self.MIN_BARS_ATR,
            "indicators_ready": count >= self.MIN_BARS_ATR,
        }
