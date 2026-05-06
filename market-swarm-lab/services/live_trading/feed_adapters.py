"""
Feed adapters for Rithmic and Bookmap data sources.
Provides unified interface for market data ingestion.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Callable, Dict
from datetime import datetime
import json
import random

from data_types import OrderFlowEvent, BarData, OrderSide, PriceLevelData

logger = logging.getLogger(__name__)


class FeedAdapter(ABC):
    """Base class for feed adapters."""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.connected = False
        self.callbacks: List[Callable] = []
    
    @abstractmethod
    async def connect(self):
        """Connect to feed."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Disconnect from feed."""
        pass
    
    @abstractmethod
    async def subscribe(self, symbols: List[str]):
        """Subscribe to symbols."""
        pass
    
    def register_callback(self, callback: Callable):
        """Register callback for events."""
        self.callbacks.append(callback)
    
    async def _emit_event(self, event_type: str, data: dict):
        """Emit event to all registered callbacks."""
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, data)
                else:
                    callback(event_type, data)
            except Exception as e:
                logger.error(f"Error in callback: {e}")


class RithmicAdapter(FeedAdapter):
    """Adapter for Rithmic feed data."""
    
    def __init__(self, symbol: str, host: str = "localhost", port: int = 8000):
        super().__init__(symbol)
        self.host = host
        self.port = port
        self.websocket = None
    
    async def connect(self):
        """Connect to Rithmic feed."""
        try:
            # In production, use websockets.connect()
            # For now, simulate connection
            logger.info(f"Connecting to Rithmic {self.host}:{self.port}")
            self.connected = True
            await self._emit_event("connected", {"feed": "rithmic", "symbol": self.symbol})
        except Exception as e:
            logger.error(f"Failed to connect to Rithmic: {e}")
            self.connected = False
    
    async def disconnect(self):
        """Disconnect from Rithmic."""
        self.connected = False
        if self.websocket:
            await self.websocket.close()
        await self._emit_event("disconnected", {"feed": "rithmic"})
    
    async def subscribe(self, symbols: List[str]):
        """Subscribe to Rithmic symbols."""
        logger.info(f"Subscribing to symbols: {symbols}")
        for symbol in symbols:
            await self._emit_event("subscribed", {"feed": "rithmic", "symbol": symbol})
    
    async def _handle_message(self, message: dict):
        """Handle incoming message from Rithmic."""
        if message.get("type") == "trade":
            event = OrderFlowEvent(
                timestamp=message.get("timestamp", datetime.now().timestamp()),
                symbol=message.get("symbol", self.symbol),
                price=message.get("price", 0.0),
                size=message.get("size", 0.0),
                side=OrderSide.BUY if message.get("side") == "B" else OrderSide.SELL,
                is_market_order=True,
            )
            await self._emit_event("trade", {"event": event})
        
        elif message.get("type") == "order":
            event = OrderFlowEvent(
                timestamp=message.get("timestamp", datetime.now().timestamp()),
                symbol=message.get("symbol", self.symbol),
                price=message.get("price", 0.0),
                size=message.get("size", 0.0),
                side=OrderSide.BUY if message.get("side") == "B" else OrderSide.SELL,
                order_id=message.get("order_id"),
                is_market_order=False,
            )
            await self._emit_event("order", {"event": event})
        
        elif message.get("type") == "bar":
            bar = BarData(
                timestamp=message.get("timestamp", datetime.now().timestamp()),
                symbol=message.get("symbol", self.symbol),
                open=message.get("open", 0.0),
                high=message.get("high", 0.0),
                low=message.get("low", 0.0),
                close=message.get("close", 0.0),
                volume=message.get("volume", 0.0),
                bid_volume=message.get("bid_volume", 0.0),
                ask_volume=message.get("ask_volume", 0.0),
            )
            await self._emit_event("bar", {"bar": bar})


class BookmapAdapter(FeedAdapter):
    """Adapter for Bookmap API data."""
    
    def __init__(self, symbol: str, host: str = "localhost", port: int = 9000, api_key: str = ""):
        super().__init__(symbol)
        self.host = host
        self.port = port
        self.api_key = api_key
        self.depth_levels: Dict[str, List[PriceLevelData]] = {}
    
    async def connect(self):
        """Connect to Bookmap API."""
        try:
            logger.info(f"Connecting to Bookmap {self.host}:{self.port}")
            self.connected = True
            await self._emit_event("connected", {"feed": "bookmap", "symbol": self.symbol})
        except Exception as e:
            logger.error(f"Failed to connect to Bookmap: {e}")
            self.connected = False
    
    async def disconnect(self):
        """Disconnect from Bookmap."""
        self.connected = False
        await self._emit_event("disconnected", {"feed": "bookmap"})
    
    async def subscribe(self, symbols: List[str]):
        """Subscribe to Bookmap symbols."""
        logger.info(f"Subscribing to Bookmap symbols: {symbols}")
        for symbol in symbols:
            self.depth_levels[symbol] = []
            await self._emit_event("subscribed", {"feed": "bookmap", "symbol": symbol})
    
    async def update_depth(self, symbol: str, levels: List[dict]):
        """Update order book depth."""
        price_levels = []
        for level in levels:
            pl = PriceLevelData(
                price=level.get("price", 0.0),
                bid_volume=level.get("bid_size", 0.0),
                ask_volume=level.get("ask_size", 0.0),
                bid_orders=level.get("bid_orders", 0),
                ask_orders=level.get("ask_orders", 0),
            )
            price_levels.append(pl)
        
        self.depth_levels[symbol] = price_levels
        await self._emit_event("depth_update", {"symbol": symbol, "levels": price_levels})
    
    async def _handle_message(self, message: dict):
        """Handle incoming message from Bookmap."""
        if message.get("type") == "depth":
            await self.update_depth(message.get("symbol", self.symbol), message.get("levels", []))
        
        elif message.get("type") == "trade":
            event = OrderFlowEvent(
                timestamp=message.get("timestamp", datetime.now().timestamp()),
                symbol=message.get("symbol", self.symbol),
                price=message.get("price", 0.0),
                size=message.get("size", 0.0),
                side=OrderSide.BUY if message.get("side") == "B" else OrderSide.SELL,
                is_market_order=True,
            )
            await self._emit_event("trade", {"event": event})


class MockFeedAdapter(FeedAdapter):
    """Mock feed for testing without live data."""
    
    def __init__(self, symbol: str, speed_factor: float = 1.0):
        super().__init__(symbol)
        self.speed_factor = speed_factor
        self.running = False
        self.base_price = 100.0
    
    async def connect(self):
        """Mock connection."""
        logger.info(f"Mock feed connecting for {self.symbol}")
        self.connected = True
        self.running = True
        self.base_price = random.uniform(100, 500)
        await self._emit_event("connected", {"feed": "mock", "symbol": self.symbol})
    
    async def disconnect(self):
        """Mock disconnection."""
        self.connected = False
        self.running = False
        await self._emit_event("disconnected", {"feed": "mock"})
    
    async def subscribe(self, symbols: List[str]):
        """Mock subscription."""
        logger.info(f"Mock feed subscribing to: {symbols}")
        for symbol in symbols:
            await self._emit_event("subscribed", {"feed": "mock", "symbol": symbol})
        
        # Start generating synthetic data
        asyncio.create_task(self._generate_synthetic_data())
    
    async def _generate_synthetic_data(self):
        """Generate synthetic orderflow and bar data."""
        bars_generated = 0
        
        while self.running and bars_generated < 100:
            # Random price movement
            price_change = random.uniform(-0.5, 0.5)
            self.base_price += price_change
            
            # Generate synthetic trades
            for _ in range(random.randint(5, 15)):
                side = OrderSide.BUY if random.random() > 0.5 else OrderSide.SELL
                event = OrderFlowEvent(
                    timestamp=datetime.now().timestamp(),
                    symbol=self.symbol,
                    price=self.base_price + random.uniform(-0.2, 0.2),
                    size=random.uniform(100, 1000),
                    side=side,
                    is_market_order=random.random() > 0.3,
                )
                await self._emit_event("trade", {"event": event})
            
            # Generate synthetic bar every 10 iterations
            if bars_generated % 10 == 0:
                bar = BarData(
                    timestamp=datetime.now().timestamp(),
                    symbol=self.symbol,
                    open=self.base_price - random.uniform(0.1, 0.3),
                    high=self.base_price + random.uniform(0.1, 0.4),
                    low=self.base_price - random.uniform(0.1, 0.4),
                    close=self.base_price,
                    volume=random.uniform(10000, 50000),
                    bid_volume=random.uniform(5000, 25000),
                    ask_volume=random.uniform(5000, 25000),
                )
                await self._emit_event("bar", {"bar": bar})
            
            bars_generated += 1
            await asyncio.sleep(0.1 / self.speed_factor)
