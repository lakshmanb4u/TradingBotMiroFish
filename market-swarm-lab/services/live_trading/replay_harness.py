"""
Replay harness for backtesting with historical data.
"""

import asyncio
import logging
from typing import List, Optional
from datetime import datetime
import csv
import json

from config import load_config, Config
from alert_engine import AlertEngine
from data_types import OrderFlowEvent, BarData, OrderSide, AlertStats

logger = logging.getLogger(__name__)


class ReplayHarness:
    """Replay historical data for backtesting alerts."""
    
    def __init__(self, config: Config):
        """Initialize replay harness."""
        self.config = config
        self.alert_engine = AlertEngine(config)
        self.results: List = []
    
    async def replay_events(self, events_file: str) -> AlertStats:
        """
        Replay historical order flow events.
        
        Args:
            events_file: Path to CSV file with events
            
        Returns:
            Alert statistics
        """
        logger.info(f"Replaying events from {events_file}")
        
        try:
            with open(events_file, 'r') as f:
                reader = csv.DictReader(f)
                events_by_bar: dict = {}
                
                for row in reader:
                    # Parse event
                    event = OrderFlowEvent(
                        timestamp=float(row['timestamp']),
                        symbol=row['symbol'],
                        price=float(row['price']),
                        size=float(row['size']),
                        side=OrderSide.BUY if row['side'] == 'BUY' else OrderSide.SELL,
                        order_id=row.get('order_id'),
                        is_market_order=row.get('is_market') == 'true',
                    )
                    
                    # Group by bar
                    bar_key = (event.symbol, int(event.timestamp / 60) * 60)
                    if bar_key not in events_by_bar:
                        events_by_bar[bar_key] = []
                    events_by_bar[bar_key].append(event)
                
                # Process bars in order
                for (symbol, bar_start), events in sorted(events_by_bar.items()):
                    # Process events
                    await self.alert_engine.process_events(events, symbol)
                    
                    # Simulate bar
                    if events:
                        bar = self._create_bar_from_events(events, bar_start)
                        alerts = await self.alert_engine.process_bar(bar)
                        
                        for alert in alerts:
                            self.results.append({
                                'timestamp': alert.timestamp,
                                'symbol': alert.symbol,
                                'type': alert.alert_type.value,
                                'severity': alert.severity.name,
                                'price': alert.price,
                                'volume': alert.volume,
                            })
                
                logger.info(f"Replay complete: {len(self.results)} alerts generated")
        
        except Exception as e:
            logger.error(f"Error replaying events: {e}")
            raise
        
        return self.alert_engine.get_stats()
    
    async def replay_bars(self, bars_file: str) -> AlertStats:
        """
        Replay historical OHLCV bars.
        
        Args:
            bars_file: Path to CSV file with bars
            
        Returns:
            Alert statistics
        """
        logger.info(f"Replaying bars from {bars_file}")
        
        try:
            with open(bars_file, 'r') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    # Parse bar
                    bar = BarData(
                        timestamp=float(row['timestamp']),
                        symbol=row['symbol'],
                        open=float(row['open']),
                        high=float(row['high']),
                        low=float(row['low']),
                        close=float(row['close']),
                        volume=float(row['volume']),
                        bid_volume=float(row.get('bid_volume', 0)),
                        ask_volume=float(row.get('ask_volume', 0)),
                    )
                    
                    # Process bar
                    alerts = await self.alert_engine.process_bar(bar)
                    
                    for alert in alerts:
                        self.results.append({
                            'timestamp': alert.timestamp,
                            'symbol': alert.symbol,
                            'type': alert.alert_type.value,
                            'severity': alert.severity.name,
                            'price': alert.price,
                            'volume': alert.volume,
                        })
                
                logger.info(f"Replay complete: {len(self.results)} alerts generated")
        
        except Exception as e:
            logger.error(f"Error replaying bars: {e}")
            raise
        
        return self.alert_engine.get_stats()
    
    def _create_bar_from_events(self, events: List[OrderFlowEvent], bar_start: float) -> BarData:
        """Create bar from events."""
        prices = [e.price for e in events]
        
        return BarData(
            timestamp=bar_start,
            symbol=events[0].symbol,
            open=events[0].price,
            high=max(prices),
            low=min(prices),
            close=events[-1].price,
            volume=sum(e.size for e in events),
            bid_volume=sum(e.size for e in events if e.side == OrderSide.BUY),
            ask_volume=sum(e.size for e in events if e.side == OrderSide.SELL),
        )
    
    def save_results(self, output_file: str):
        """Save replay results to JSON."""
        with open(output_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Results saved to {output_file}")
    
    def print_summary(self):
        """Print replay summary."""
        stats = self.alert_engine.get_stats()
        
        print("\n" + "=" * 60)
        print("REPLAY SUMMARY")
        print("=" * 60)
        print(f"Total Alerts: {stats.total_alerts}")
        print(f"WhatsApp Sent: {stats.whatsapp_sent}")
        print(f"Email Sent: {stats.email_sent}")
        
        print("\nBy Type:")
        for alert_type, count in stats.by_type.items():
            print(f"  {alert_type}: {count}")
        
        print("\nBy Severity:")
        for severity, count in stats.by_severity.items():
            print(f"  {severity}: {count}")
        
        print("\nBy Symbol:")
        for symbol, count in stats.by_symbol.items():
            print(f"  {symbol}: {count}")
        
        print("=" * 60 + "\n")


async def main():
    """Main entry point for replay."""
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python replay_harness.py <events_file|bars_file> <output_file>")
        print("Example: python replay_harness.py data/events.csv results/alerts.json")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    # Load config
    config = load_config()
    
    # Create harness
    harness = ReplayHarness(config)
    
    # Determine file type
    if 'events' in input_file.lower():
        await harness.replay_events(input_file)
    else:
        await harness.replay_bars(input_file)
    
    # Save results
    harness.save_results(output_file)
    harness.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
