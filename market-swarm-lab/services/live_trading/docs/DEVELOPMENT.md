# Development Guide

## Project Structure

```
market-swarm-lab/services/live_trading/
├── .env.template              # Configuration template
├── requirements.txt           # Python dependencies
├── README.md                  # Main documentation
├── STARTUP_GUIDE.md          # Getting started
│
├── config.py                 # Configuration loader
├── data_types.py             # Core data structures
│
├── feed_adapters.py          # Feed integrations
│   ├── RithmicAdapter
│   ├── BookmapAdapter
│   └── MockFeedAdapter
│
├── regime_detector.py        # Trend/volatility detection
├── absorption_detector.py    # Order absorption detection
├── followthrough_gate.py     # Signal confirmation
├── alert_engine.py           # Main orchestration
│
├── delivery_whatsapp.py      # WhatsApp delivery
├── live_service.py           # Main entry point
│
├── replay_harness.py         # Backtest harness
├── test_mock_feed.py         # Mock feed test
│
├── __init__.py               # Package initialization
│
├── data/
│   ├── sample_bars.csv       # Test bars data
│   └── sample_events.csv     # Test events data
│
├── docs/
│   ├── ARCHITECTURE.md       # System design
│   └── DEVELOPMENT.md        # This file
│
└── logs/                      # Runtime logs (created on first run)
    ├── errors.log
    └── service.log
```

## Development Setup

### Prerequisites

```bash
# Python 3.8+
python3 --version

# pip
pip --version

# Virtual environment
python3 -m venv venv
source venv/bin/activate
```

### Installation

```bash
# Clone repo
cd market-swarm-lab/services/live_trading

# Install dev dependencies
pip install -r requirements.txt

# Optional: Install dev tools
pip install pytest pytest-asyncio black flake8 mypy
```

### Configuration

```bash
# Create .env for development
cp .env.template .env

# Edit for local testing
nano .env
```

**Development .env:**
```bash
SERVICE_MOCK_FEED_MODE=true
SERVICE_LOG_LEVEL=DEBUG
SERVICE_DEBUG_MODE=true
ALERT_WHATSAPP_ENABLED=false
MONITOR_ENABLED=false
```

## Running Tests

### Mock Feed Test

```bash
# Quick test of entire pipeline
python test_mock_feed.py

# With custom duration
python -c "
import asyncio
from test_mock_feed import MockFeedTest
from config import load_config

async def main():
    config = load_config()
    config.service.mock_feed_mode = True
    test = MockFeedTest(config)
    await test.run(symbols=['ES', 'NQ'], duration_seconds=60)

asyncio.run(main())
"
```

### Replay Test

```bash
# Test with sample data
python replay_harness.py data/sample_bars.csv /tmp/test_results.json

# Inspect results
python -c "
import json
with open('/tmp/test_results.json') as f:
    results = json.load(f)
    print(f'Generated {len(results)} alerts')
    for r in results[:3]:
        print(f'  - {r[\"symbol\"]} {r[\"type\"]} @ \${r[\"price\"]:.2f}')
"
```

## Code Style

### Formatting with Black

```bash
# Format all files
black . --line-length 100

# Check formatting
black . --check --line-length 100
```

### Linting with Flake8

```bash
# Check code quality
flake8 . --max-line-length 100 --exclude venv

# Common issues
flake8 config.py  # Check specific file
```

### Type Hints with MyPy

```bash
# Check type hints
mypy . --ignore-missing-imports

# Check specific file
mypy alert_engine.py --ignore-missing-imports
```

## Testing Patterns

### Unit Testing

**Example: Testing Regime Detection**

```python
# tests/test_regime_detector.py
import pytest
from regime_detector import RegimeDetector
from data_types import BarData, RegimeType

def test_uptrend_detection():
    detector = RegimeDetector(ma_short=5, ma_long=20)
    
    # Create 20 bars with uptrend
    bars = []
    for i in range(20):
        bar = BarData(
            timestamp=i * 60,
            symbol="ES",
            open=4500 + i,
            high=4500 + i + 1,
            low=4500 + i - 0.5,
            close=4500 + i + 0.5,
            volume=50000
        )
        bars.append(bar)
    
    # Process bars
    results = []
    for bar in bars:
        regime = detector.update(bar)
        if regime:
            results.append(regime)
    
    # Assert last regime is uptrend
    assert results[-1].regime_type == RegimeType.UPTREND
    assert results[-1].trend_strength > 0.5
```

### Integration Testing

**Example: End-to-End Signal Generation**

```python
# tests/test_signal_generation.py
import asyncio
import pytest
from alert_engine import AlertEngine
from feed_adapters import MockFeedAdapter
from config import load_config

@pytest.mark.asyncio
async def test_absorption_alert_generation():
    config = load_config()
    config.service.mock_feed_mode = True
    
    engine = AlertEngine(config)
    alerts = []
    
    def capture_alert(alert):
        alerts.append(alert)
    
    engine.register_alert_callback(capture_alert)
    
    # Generate synthetic absorption
    from data_types import OrderFlowEvent, OrderSide, BarData
    
    events = [
        OrderFlowEvent(1.0, "ES", 4520.0, 500, OrderSide.BUY),
        OrderFlowEvent(1.1, "ES", 4520.0, 200, OrderSide.SELL),
        OrderFlowEvent(1.2, "ES", 4520.1, 300, OrderSide.BUY),
    ]
    
    bar = BarData(
        timestamp=60,
        symbol="ES",
        open=4520.0,
        high=4521.0,
        low=4519.0,
        close=4520.5,
        volume=10000,
        bid_volume=6000,
        ask_volume=4000
    )
    
    await engine.process_events(events, "ES")
    await engine.process_bar(bar)
    
    assert len(alerts) > 0
```

## Adding New Features

### 1. Add New Alert Type

**Step 1**: Extend `AlertType` in `data_types.py`:
```python
class AlertType(Enum):
    # ... existing
    MY_NEW_ALERT = "MY_NEW_ALERT"
```

**Step 2**: Add detection logic in `alert_engine.py`:
```python
async def detect_my_new_pattern(self, bar: BarData):
    # Detection logic
    if condition:
        alert = OrderFlowAlert(
            alert_type=AlertType.MY_NEW_ALERT,
            ...
        )
        await self._emit_alert(alert)
```

**Step 3**: Call from `process_bar()`:
```python
async def process_bar(self, bar: BarData):
    # ... existing detection
    await self.detect_my_new_pattern(bar)
```

### 2. Add Custom Feed Adapter

**Step 1**: Create adapter class in `feed_adapters.py`:
```python
class MyFeedAdapter(FeedAdapter):
    async def connect(self):
        # Connection logic
        await self._emit_event("connected", {...})
    
    async def disconnect(self):
        # Disconnection logic
        await self._emit_event("disconnected", {...})
    
    async def subscribe(self, symbols):
        # Subscribe logic
        await self._emit_event("subscribed", {...})
```

**Step 2**: Use in `live_service.py`:
```python
from feed_adapters import MyFeedAdapter

adapter = MyFeedAdapter(symbol)
adapter.register_callback(self._on_feed_event)
await adapter.connect()
```

### 3. Add Custom Delivery Channel

**Step 1**: Create delivery class:
```python
# delivery_slack.py
class SlackDelivery:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    async def send_alert(self, alert: OrderFlowAlert):
        message = self._format_alert(alert)
        await post_to_slack(self.webhook_url, message)
    
    def _format_alert(self, alert):
        return f"🚨 {alert.symbol} {alert.alert_type.value}"
```

**Step 2**: Register callback in `live_service.py`:
```python
slack = SlackDelivery(webhook_url)
self.alert_engine.register_alert_callback(
    lambda alert: slack.send_alert(alert)
)
```

## Debugging

### Enable Debug Logging

```bash
# .env
SERVICE_LOG_LEVEL=DEBUG
SERVICE_DEBUG_MODE=true
```

```bash
python live_service.py 2>&1 | tee debug.log
```

### Add Print Debugging

```python
# In alert_engine.py
async def process_bar(self, bar: BarData):
    print(f"[DEBUG] Processing {bar.symbol} at {bar.timestamp}")
    print(f"  Close: {bar.close:.2f}, Volume: {bar.volume:,.0f}")
    # ... rest of processing
    print(f"  Alerts generated: {len(alerts)}")
```

### Use Python Debugger

```python
import pdb

async def process_bar(self, bar: BarData):
    pdb.set_trace()  # Breakpoint
    # ... rest of code
```

Run with:
```bash
python -u live_service.py
# (then interact with debugger)
```

### Profile Performance

```python
# profile_service.py
import cProfile
import pstats
from io import StringIO
import asyncio
from live_service import LiveOrderflowService
from config import load_config

async def main():
    config = load_config()
    config.service.mock_feed_mode = True
    
    service = LiveOrderflowService(config)
    
    profiler = cProfile.Profile()
    profiler.enable()
    
    # Run for 10 seconds
    await service.initialize()
    await service.start()
    await asyncio.sleep(10)
    await service.stop()
    
    profiler.disable()
    
    # Print results
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(20)  # Top 20
    print(s.getvalue())

asyncio.run(main())
```

Run:
```bash
python profile_service.py > profile_results.txt
```

## Performance Optimization

### Reduce Memory Usage

```python
# Before: Keep all events
self.event_buffers: Dict[str, Deque] = {}  # Unbounded

# After: Limit size
self.event_buffers: Dict[str, Deque] = {
    symbol: deque(maxlen=10000)  # Max 10K events
    for symbol in symbols
}
```

### Reduce CPU Usage

```python
# Increase tick interval
SERVICE_TICK_INTERVAL_MS=200  # Was 100

# Reduce history depth
SERVICE_HISTORY_DEPTH=500  # Was 1000

# Simplify absorption detection
ABSORPTION_VOLUME_MIN_PCT=0.5  # Was 0.3 (fewer alerts)
```

### Improve Latency

```python
# Async event processing
async def process_events(self, events, symbol):
    # Process in background
    asyncio.create_task(self._process_events_async(events, symbol))

# Batch WhatsApp sends
whatsapp_queue = []
while len(whatsapp_queue) < 5 or timeout_exceeded:
    whatsapp_queue.append(alert)
await whatsapp.send_batch(whatsapp_queue)
```

## Common Issues

### Issue: Events not buffering

**Debug**:
```python
# In live_service.py
async def _on_feed_event(self, event_type, data):
    print(f"[DEBUG] Event: {event_type}")
    if event_type == "trade":
        print(f"  Buffered {len(self.event_buffers[symbol])} events")
```

### Issue: High latency

**Solutions**:
1. Profile with `cProfile`
2. Reduce complexity of detection algorithms
3. Increase `TICK_INTERVAL_MS`
4. Use smaller time windows

### Issue: Memory leak

**Debug**:
```python
import tracemalloc
tracemalloc.start()

# Run service...

current, peak = tracemalloc.get_traced_memory()
print(f"Current: {current / 1e6:.1f}MB, Peak: {peak / 1e6:.1f}MB")
```

## Contributing

### Making Changes

1. Create feature branch:
```bash
git checkout -b feature/my-feature
```

2. Make changes with tests:
```bash
# Edit files
nano config.py

# Write tests
nano tests/test_config.py

# Run tests
pytest tests/test_config.py -v
```

3. Check code quality:
```bash
black . --line-length 100
flake8 . --max-line-length 100
mypy . --ignore-missing-imports
```

4. Submit PR with:
- Description of changes
- Test results
- Performance impact
- Configuration changes (if any)

### Commit Message Format

```
[COMPONENT] Short description

Longer explanation of what and why.

- Bullet point 1
- Bullet point 2

Fixes #123
```

Example:
```
[alert_engine] Add ACCUMULATION alert type

Detect repeated absorption at same price level across
multiple bars. Useful for identifying institutional interest.

- New AbsorptionDetector.detect_accumulation_zones()
- New AlertType.ACCUMULATION
- Alert severity boosted for high-conviction signals

Fixes #45
```

## Documentation

### Adding New Module Documentation

```python
"""
Module description - what does this module do?

This module handles X, Y, and Z operations. It integrates with [other modules]
and provides [core functionality].

Key Classes:
    MyClass - Main functionality
    HelperClass - Supporting functionality

Example:
    >>> from my_module import MyClass
    >>> obj = MyClass(config)
    >>> await obj.do_something()

See also:
    - other_module.py - Related functionality
    - docs/ARCHITECTURE.md - Design overview
"""
```

### Adding Type Hints

```python
from typing import Optional, List, Dict

async def process_events(
    self,
    events: List[OrderFlowEvent],
    symbol: str
) -> None:
    """
    Process incoming orderflow events.
    
    Args:
        events: List of order flow events to process
        symbol: Trading symbol (e.g., 'ES', 'NQ')
    
    Returns:
        None
    
    Raises:
        ValueError: If symbol is not subscribed
        asyncio.TimeoutError: If processing times out
    """
    # Implementation
    pass
```

## Resources

- **Python Docs**: https://docs.python.org/3/
- **asyncio Guide**: https://docs.python.org/3/library/asyncio.html
- **Type Hints**: https://docs.python.org/3/library/typing.html
- **pytest**: https://docs.pytest.org/
- **Twilio Docs**: https://www.twilio.com/docs/whatsapp
