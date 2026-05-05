"""
Sector heatmap generator for live trading.
Tracks sector breadth, momentum, and risk-on/risk-off scores.
"""

import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)


class SectorHeatmap:
    """Generates sector-level market analysis."""
    
    def __init__(self, classifier):
        self.classifier = classifier
        self._last_heatmap = None
        self._cycle_count = 0
    
    def compute_heatmap(self, ticker_data: dict[str, dict]) -> dict:
        """
        Compute sector heatmap from ticker price data.
        
        Args:
            ticker_data: Dict of {ticker: {price, change_pct, volume, ...}}
        
        Returns:
            Heatmap dict with sector scores
        """
        sectors = {}
        
        for sector_name, tickers in self.classifier.SECTORS.items():
            sector_tickers = [t for t in tickers if t in ticker_data]
            if not sector_tickers:
                continue
            
            # Compute sector metrics
            changes = [ticker_data[t].get("change_pct", 0) for t in sector_tickers]
            volumes = [ticker_data[t].get("volume", 0) for t in sector_tickers]
            
            avg_change = sum(changes) / len(changes) if changes else 0
            advancers = sum(1 for c in changes if c > 0)
            decliners = sum(1 for c in changes if c < 0)
            total = len(changes)
            
            # Breadth score: -100 (all down) to +100 (all up)
            breadth = ((advancers - decliners) / total * 100) if total > 0 else 0
            
            # Momentum score: weighted by magnitude
            momentum = avg_change
            
            # Volume intensity vs 10-day average (simplified)
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            
            sectors[sector_name] = {
                "avg_change_pct": round(avg_change, 2),
                "breadth": round(breadth, 1),
                "momentum": round(momentum, 2),
                "advancers": advancers,
                "decliners": decliners,
                "total": total,
                "avg_volume": int(avg_volume),
                "strongest_ticker": self._strongest_ticker(sector_tickers, ticker_data),
                "weakest_ticker": self._weakest_ticker(sector_tickers, ticker_data),
            }
        
        # Compute risk-on/risk-off score
        risk_score = self._compute_risk_score(sectors, ticker_data)
        
        heatmap = {
            "timestamp": datetime.now().isoformat(),
            "sectors": sectors,
            "risk_on_score": risk_score["risk_on"],
            "risk_off_score": risk_score["risk_off"],
            "market_bias": risk_score["bias"],
            "top_sector": self._top_sector(sectors),
            "bottom_sector": self._bottom_sector(sectors),
        }
        
        self._last_heatmap = heatmap
        return heatmap
    
    def _strongest_ticker(self, tickers: list[str], data: dict) -> dict:
        """Find strongest ticker in sector."""
        best = max(tickers, key=lambda t: data[t].get("change_pct", -999))
        return {
            "ticker": best,
            "change_pct": round(data[best].get("change_pct", 0), 2),
        }
    
    def _weakest_ticker(self, tickers: list[str], data: dict) -> dict:
        """Find weakest ticker in sector."""
        worst = min(tickers, key=lambda t: data[t].get("change_pct", 999))
        return {
            "ticker": worst,
            "change_pct": round(data[worst].get("change_pct", 0), 2),
        }
    
    def _top_sector(self, sectors: dict) -> str:
        """Get top performing sector."""
        if not sectors:
            return "N/A"
        return max(sectors.items(), key=lambda x: x[1]["avg_change_pct"])[0]
    
    def _bottom_sector(self, sectors: dict) -> str:
        """Get bottom performing sector."""
        if not sectors:
            return "N/A"
        return min(sectors.items(), key=lambda x: x[1]["avg_change_pct"])[0]
    
    def _compute_risk_score(self, sectors: dict, ticker_data: dict) -> dict:
        """Compute risk-on vs risk-off score."""
        # Risk-on: momentum, AI/semis, fintech strong
        # Risk-off: macro ETFs weak, mega tech defensive
        
        risk_on_sectors = ["momentum", "ai_semis", "fintech"]
        risk_off_sectors = ["macro", "mega_tech"]
        
        risk_on_score = 0
        risk_off_score = 0
        
        for sector in risk_on_sectors:
            if sector in sectors:
                risk_on_score += sectors[sector]["avg_change_pct"]
        
        for sector in risk_off_sectors:
            if sector in sectors:
                risk_off_score += sectors[sector]["avg_change_pct"]
        
        # Normalize
        risk_on_score = round(risk_on_score / len(risk_on_sectors), 2) if risk_on_sectors else 0
        risk_off_score = round(risk_off_score / len(risk_off_sectors), 2) if risk_off_sectors else 0
        
        if risk_on_score > risk_off_score + 0.5:
            bias = "RISK_ON"
        elif risk_off_score > risk_on_score + 0.5:
            bias = "RISK_OFF"
        else:
            bias = "NEUTRAL"
        
        return {
            "risk_on": risk_on_score,
            "risk_off": risk_off_score,
            "bias": bias,
        }
    
    def format_heatmap_text(self, heatmap: dict) -> str:
        """Format heatmap as human-readable text."""
        lines = [
            "=" * 60,
            "SECTOR HEATMAP",
            f"Time: {heatmap['timestamp']}",
            "=" * 60,
            "",
        ]
        
        # Sector details
        for sector, data in heatmap["sectors"].items():
            emoji = "🟢" if data["avg_change_pct"] > 0 else "🔴" if data["avg_change_pct"] < 0 else "⚪"
            lines.append(
                f"{emoji} {sector.upper():<12} "
                f"{data['avg_change_pct']:>+6.2f}%  "
                f"Breadth: {data['breadth']:>+6.1f}  "
                f"({data['advancers']}/{data['total']} up)"
            )
            lines.append(
                f"   Strongest: {data['strongest_ticker']['ticker']} "
                f"({data['strongest_ticker']['change_pct']:+.2f}%)  "
                f"Weakest: {data['weakest_ticker']['ticker']} "
                f"({data['weakest_ticker']['change_pct']:+.2f}%)"
            )
            lines.append("")
        
        # Risk summary
        lines.extend([
            "-" * 60,
            f"Risk-On Score:  {heatmap['risk_on_score']:>+.2f}%",
            f"Risk-Off Score: {heatmap['risk_off_score']:>+.2f}%",
            f"Market Bias:    {heatmap['market_bias']}",
            f"Top Sector:     {heatmap['top_sector']}",
            f"Bottom Sector:  {heatmap['bottom_sector']}",
            "=" * 60,
        ])
        
        return "\n".join(lines)
    
    def should_print(self, config: dict) -> bool:
        """Check if heatmap should be printed this cycle."""
        self._cycle_count += 1
        every_n = config.get("sector_heatmap", {}).get("print_every_n_cycles", 6)
        return self._cycle_count % every_n == 0
