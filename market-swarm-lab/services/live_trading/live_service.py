"""
Main live orderflow alert service.
Orchestrates feed adapters, detection engines, and alert delivery.
"""

import asyncio
import logging
import signal
from typing import List, Dict, Optional
from datetime import datetime

from config import load_config, Config
from feed_adapters import RithmicAdapter, BookmapAdapter, MockFeedAdapter
from alert_engine import AlertEngine
from delivery_whatsapp import WhatsAppDelivery
from data_types import OrderFlowEvent, BarData

logger = logging.getLogger(__name__)


class LiveOrderflowService:
    """Main service for live orderflow monitoring and alerting."""
    
    def __init__(self, config: Config):
        """Initialize service."""
        self.config = config
        
        # Setup logging
        logging.basicConfig(
            level=getattr(logging, config.service.log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Initialize components
        self.feed_adapters: Dict[str, object] = {}
        self.alert_engine = AlertEngine(config)
        
        # Initialize delivery
        self.whatsapp_delivery = None
        if config.alert.whatsapp_enabled:
            self.whatsapp_delivery = WhatsAppDelivery(
                account_sid=config.whatsapp.account_sid,
                auth_token=config.whatsapp.auth_token,
                from_phone=config.whatsapp.phone_number,
                to_phone=config.whatsapp.recipient_phone,
                enabled=True
            )
            self.alert_engine.register_alert_callback(self._on_alert)
        
        # State
        self.running = False
        self.event_buffers: Dict[str, List[OrderFlowEvent]] = {}
    
    async def initialize(self):
        """Initialize service."""
        logger.info("Initializing Live Orderflow Alert Service")
        
        # Create feed adapters
        for symbol in self.config.bookmap.symbols:
            if self.config.service.mock_feed_mode:
                adapter = MockFeedAdapter(symbol, speed_factor=1.0)
            else:
                # Use Bookmap adapter for real data
                adapter = BookmapAdapter(
                    symbol,
                    host=self.config.bookmap.host,
                    port=self.config.bookmap.port,
                    api_key=self.config.bookmap.api_key
                )
            
            adapter.register_callback(self._on_feed_event)
            self.feed_adapters[symbol] = adapter
            self.event_buffers[symbol] = []
        
        logger.info(f"Initialized adapters for {len(self.feed_adapters)} symbols")
    
    async def start(self):
        """Start the service."""
        logger.info("Starting Live Orderflow Alert Service")
        self.running = True
        
        # Connect all adapters
        for symbol, adapter in self.feed_adapters.items():
            await adapter.connect()
            await adapter.subscribe([symbol])
        
        # Start processing loop
        asyncio.create_task(self._process_loop())
        
        logger.info("Service started")
    
    async def stop(self):
        """Stop the service."""
        logger.info("Stopping Live Orderflow Alert Service")
        self.running = False
        
        # Disconnect adapters
        for adapter in self.feed_adapters.values():
            await adapter.disconnect()
        
        logger.info("Service stopped")
    
    async def _on_feed_event(self, event_type: str, data: dict):
        """Handle feed event."""
        try:
            if event_type == "trade":
                event = data.get("event")
                if event:
                    symbol = event.symbol
                    if symbol not in self.event_buffers:
                        self.event_buffers[symbol] = []
                    self.event_buffers[symbol].append(event)
                    
            elif event_type == "bar":
                bar = data.get("bar")
                if bar:
                    # Process buffered events for this bar
                    events = self.event_buffers.get(bar.symbol, [])
                    if events:
                        await self.alert_engine.process_events(events, bar.symbol)
                        self.event_buffers[bar.symbol] = []
                    
                    # Process bar
                    alerts = await self.alert_engine.process_bar(bar)
                    
        except Exception as e:
            logger.error(f"Error handling feed event: {e}", exc_info=True)
    
    async def _process_loop(self):
        """Main processing loop."""
        logger.info("Starting processing loop")
        
        while self.running:
            try:
                await asyncio.sleep(self.config.service.tick_interval_ms / 1000.0)
                
                # Periodic cleanup and stats logging
                if int(datetime.now().timestamp()) % 60 == 0:
                    stats = self.alert_engine.get_stats()
                    logger.info(f"Alert stats: total={stats.total_alerts}, "
                              f"whatsapp_sent={stats.whatsapp_sent}, "
                              f"email_sent={stats.email_sent}")
                    
                    if self.whatsapp_delivery:
                        whatsapp_stats = self.whatsapp_delivery.get_stats()
                        logger.info(f"WhatsApp stats: {whatsapp_stats}")
                
            except Exception as e:
                logger.error(f"Error in processing loop: {e}", exc_info=True)
    
    async def _on_alert(self, alert):
        """Handle generated alert."""
        logger.info(f"Alert generated: {alert.symbol} {alert.alert_type.value}")
        
        # Send via WhatsApp
        if self.whatsapp_delivery and self.config.alert.whatsapp_enabled:
            success = await self.whatsapp_delivery.send_alert(alert)
            if success:
                alert.whatsapp_sent = True
                self.alert_engine.stats.whatsapp_sent += 1


async def main():
    """Main entry point."""
    try:
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded successfully")
        
        # Create service
        service = LiveOrderflowService(config)
        await service.initialize()
        
        # Setup signal handlers
        def signal_handler(signum, frame):
            asyncio.create_task(service.stop())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start service
        await service.start()
        
        # Run forever
        while service.running:
            await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
