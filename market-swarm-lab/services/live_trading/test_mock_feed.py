"""
Test runner for mock feed mode.
Generates synthetic data and tests the alert pipeline.
"""

import asyncio
import logging
from datetime import datetime
import json

from config import load_config, Config
from feed_adapters import MockFeedAdapter
from alert_engine import AlertEngine
from data_types import OrderFlowEvent, BarData, OrderSide

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockFeedTest:
    """Test harness for mock feed."""
    
    def __init__(self, config: Config):
        self.config = config
        self.alert_engine = AlertEngine(config)
        self.alerts = []
        
        # Register callback
        self.alert_engine.register_alert_callback(self._on_alert)
    
    async def _on_alert(self, alert):
        """Capture alert."""
        self.alerts.append({
            'timestamp': alert.timestamp,
            'symbol': alert.symbol,
            'type': alert.alert_type.value,
            'severity': alert.severity.name,
            'price': alert.price,
            'volume': alert.volume,
            'confidence': getattr(alert.absorption_signal, 'confidence', 0) if alert.absorption_signal else 0,
        })
        logger.info(f"Alert: {alert.symbol} {alert.alert_type.value} @ ${alert.price:.2f}")
    
    async def run(self, symbols: list = None, duration_seconds: int = 30):
        """
        Run mock feed test.
        
        Args:
            symbols: List of symbols to test (default: ES, NQ)
            duration_seconds: How long to run test
        """
        if symbols is None:
            symbols = ["ES", "NQ"]
        
        logger.info(f"Starting mock feed test for {symbols} ({duration_seconds}s)")
        
        # Create mock adapters
        adapters = {}
        for symbol in symbols:
            adapter = MockFeedAdapter(symbol, speed_factor=1.0)
            adapter.register_callback(self._on_feed_event)
            adapters[symbol] = adapter
        
        # Connect and subscribe
        for symbol, adapter in adapters.items():
            await adapter.connect()
            await adapter.subscribe([symbol])
        
        # Run for specified duration
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < duration_seconds:
            await asyncio.sleep(0.1)
        
        # Disconnect
        for adapter in adapters.values():
            await adapter.disconnect()
        
        logger.info(f"Mock feed test complete")
        self._print_results()
    
    async def _on_feed_event(self, event_type: str, data: dict):
        """Handle feed event."""
        try:
            if event_type == "bar":
                bar = data.get("bar")
                if bar:
                    logger.debug(f"Bar: {bar.symbol} {bar.close:.2f}")
                    alerts = await self.alert_engine.process_bar(bar)
        
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
    
    def _print_results(self):
        """Print test results."""
        print("\n" + "=" * 60)
        print("MOCK FEED TEST RESULTS")
        print("=" * 60)
        print(f"Total Alerts Generated: {len(self.alerts)}")
        
        if self.alerts:
            print("\nAlerts by Type:")
            by_type = {}
            for alert in self.alerts:
                atype = alert['type']
                by_type[atype] = by_type.get(atype, 0) + 1
            
            for atype, count in sorted(by_type.items()):
                print(f"  {atype}: {count}")
            
            print("\nAlerts by Severity:")
            by_severity = {}
            for alert in self.alerts:
                severity = alert['severity']
                by_severity[severity] = by_severity.get(severity, 0) + 1
            
            for severity in ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']:
                if severity in by_severity:
                    print(f"  {severity}: {by_severity[severity]}")
            
            print("\nSample Alerts:")
            for alert in self.alerts[:5]:
                print(f"  {alert['symbol']} {alert['type']} @ ${alert['price']:.2f} "
                      f"(Vol: {alert['volume']:.0f}, Conf: {alert['confidence']:.1%})")
        
        print("=" * 60 + "\n")
    
    def save_results(self, output_file: str):
        """Save results to JSON."""
        with open(output_file, 'w') as f:
            json.dump(self.alerts, f, indent=2)
        logger.info(f"Results saved to {output_file}")


async def main():
    """Main entry point."""
    config = load_config()
    
    # Override to mock mode
    config.service.mock_feed_mode = True
    
    # Run test
    test = MockFeedTest(config)
    await test.run(symbols=["ES", "NQ", "GC"], duration_seconds=20)
    
    # Save results
    test.save_results("/tmp/mock_feed_test_results.json")


if __name__ == "__main__":
    asyncio.run(main())
