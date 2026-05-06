# Architecture Overview

## System Design

The Live Orderflow Alert Service is built with a **modular, asynchronous pipeline** architecture that prioritizes reliability, latency, and extensibility.

### High-Level Flow

```
FEED SOURCE
    ↓
[Rithmic/Bookmap/Mock]
    ↓
EVENT BUFFER
    ↓
REGIME DETECTOR ←─────┐
    ↓                 │
ABSORPTION DETECTOR   │
    ↓                 │
FOLLOW-THROUGH GATE   │
    ↓                 │
ALERT ENGINE ←────────┘
    ↓
DELIVERY LAYER
    ↓
[WhatsApp/Email/Logging]
```

## Core Components

### 1. Feed Adapters (`feed_adapters.py`)

**Purpose**: Normalize data from multiple sources

**Classes**:
- `FeedAdapter` (abstract base)
- `RithmicAdapter` - Rithmic native feed
- `BookmapAdapter` - Bookmap REST/WebSocket API
- `MockFeedAdapter` - Synthetic data generation

**Responsibilities**:
- Connect/disconnect from feeds
- Parse incoming data
- Emit normalized events to callbacks
- Handle reconnection logic

**Key Pattern**: Observer pattern with async callbacks

```python
adapter = BookmapAdapter("ES", host="localhost", port=9000)
adapter.register_callback(alert_engine.on_feed_event)
await adapter.connect()
```

### 2. Event Buffer

**Purpose**: Aggregate orderflow events between bars

Events are collected in dictionaries by symbol:
```python
event_buffers: Dict[str, List[OrderFlowEvent]]
```

**Lifecycle**:
1. Events arrive from feed adapter
2. Buffered in `event_buffers[symbol]`
3. At bar close, events are processed
4. Buffer cleared after processing

### 3. Regime Detector (`regime_detector.py`)

**Purpose**: Identify market regime (trend, range, volatility state)

**Algorithm**:
1. Maintain rolling window of prices (20 bars)
2. Calculate short/long moving averages (5/20 bars)
3. Calculate ATR (Average True Range) for volatility
4. Classify regime based on MA relationship and volatility

**Output**: `RegimeState` with:
- `regime_type` - UPTREND, DOWNTREND, RANGE, BREAKOUT, BREAKDOWN
- `trend_strength` - 0.0 to 1.0
- `volatility` - Historical volatility
- `support_price`, `resistance_price` - Key levels

**Regime Logic**:
```
IF volatility > threshold:
    IF slope > 0 AND price > resistance:
        BREAKOUT
    ELSE IF slope < 0 AND price < support:
        BREAKDOWN
    ELSE:
        RANGE
ELSE:
    IF short_ma > long_ma AND slope > 0:
        UPTREND
    ELSE IF short_ma < long_ma AND slope < 0:
        DOWNTREND
    ELSE:
        RANGE
```

### 4. Absorption Detector (`absorption_detector.py`)

**Purpose**: Identify when large orders are absorbed without price movement

**Algorithm**:
1. Aggregate trades into price level buckets (±0.25 tick)
2. For each price level:
   - Calculate total volume absorbed
   - Calculate opposite-side volume (absorption confirmation)
   - Calculate delta (buy - sell)
3. Check if absorption ratio exceeds threshold

**Metrics**:
- `absorption_ratio = absorbed_volume / bar_volume`
- `delta_ratio = |delta| / absorbed_volume`
- `confidence = absorption_ratio × (delta_ratio / min_ratio)`

**Output**: `AbsorptionSignal` with:
- `side` - BUY or SELL
- `absorbed_volume` - Total volume
- `ratio` - % of bar volume
- `confidence` - 0.0 to 1.0

### 5. Follow-Through Gate (`followthrough_gate.py`)

**Purpose**: Confirm absorption signals with continuation

**Logic**:
1. Track pending absorption signals
2. New signals are checked against pending
3. For same symbol/side/price level and within time window:
   - Add as confirmation
   - Accumulate volume ratios
4. When confirmation count + volume ratio threshold met:
   - Signal is "confirmed"
   - Confidence boosted

**Confirmation Criteria**:
- Same symbol and side
- Within 5-second time window
- Price within ±2% of initial
- At least 2 bars of absorption
- Average volume ratio > 40%

**Confidence Calculation**:
```
confidence = initial_confidence
           + (0.1 × num_confirmations)
           + (0.2 × (avg_ratio - min_ratio))
```

### 6. Alert Engine (`alert_engine.py`)

**Purpose**: Orchestrate detection pipeline and generate alerts

**Pipeline** (per bar):
1. Update regime
2. Analyze bar for absorption
3. For each absorption:
   - Check follow-through gate
   - Generate alert if confirmed OR high confidence
4. Emit alerts to callbacks
5. Update statistics
6. Cleanup expired signals

**Alert Types**:
- `ABSORPTION` - Single bar absorption
- `FOLLOW_THROUGH` - Multi-bar confirmation
- `REGIME_CHANGE` - Market regime change
- `ACCUMULATION` - Repeated absorption zones
- `DISTRIBUTION` - Selling pressure zones
- `BREAKOUT_CONFIRMED` - Breakout with volume
- `BREAKDOWN_CONFIRMED` - Breakdown with volume

**Alert Severity**:
- Adjusted based on:
  - Confidence score
  - Volatility environment
  - Position sizing relative to ATR
  - Regime strength

### 7. Delivery Layer

#### WhatsApp (`delivery_whatsapp.py`)

**Features**:
- Twilio integration
- Async message queue
- Rate limiting (0.5s between messages)
- Fallback to logging if Twilio unavailable

**Message Format**:
```
🚨 ABSORPTION
Symbol: ES
Side: BUY
Price: $4520.50
Volume: 5,000
Severity: MEDIUM
Regime: UPTREND
⏰ 10:30:45
```

## Data Types (`data_types.py`)

### Core Types

```python
# Event
OrderFlowEvent(timestamp, symbol, price, size, side, order_id, is_market_order)

# Bar
BarData(timestamp, symbol, open, high, low, close, volume, 
        bid_volume, ask_volume, price_levels, large_orders)

# Signal
AbsorptionSignal(bar, side, absorbed_volume, absorption_orders, ratio, confidence)
FollowThroughConfirmation(initial, confirmations, total_volume_ratio, 
                          confirmation_count, confidence)

# State
RegimeState(regime_type, trend_strength, volatility, support, resistance)

# Alert
OrderFlowAlert(alert_id, alert_type, severity, symbol, side, price, volume,
               regime, absorption_signal, followthrough, message, timestamp,
               whatsapp_sent, email_sent)
```

## Async Architecture

The service uses Python's `asyncio` for:

1. **Non-blocking Feed Operations**
   - Feed adapter callbacks are async
   - Multiple feeds process concurrently

2. **Alert Delivery**
   - WhatsApp sends in background task
   - Doesn't block signal generation

3. **Periodic Cleanup**
   - Expired signals removed periodically
   - Old events pruned

```python
# Main loop
async def start(self):
    # Connect feeds
    for adapter in adapters:
        await adapter.connect()
    
    # Start processing
    asyncio.create_task(self._process_loop())
```

## Configuration Hierarchy

```
.env (file)
    ↓
load_config()
    ↓
Config dataclass
    ├─ RithmicConfig
    ├─ BookmapConfig
    ├─ AlertConfig
    ├─ RegimeConfig
    ├─ AbsorptionConfig
    ├─ FollowThroughConfig
    ├─ ServiceConfig
    └─ MonitorConfig
    ↓
passed to components
    ├─ RegimeDetector
    ├─ AbsorptionDetector
    ├─ FollowThroughGate
    ├─ AlertEngine
    └─ WhatsAppDelivery
```

## Signal Processing Example

### Real-World Flow

1. **T=0ms**: Bookmap sends depth update
   - Adapter emits 50 trades
   - Stored in `event_buffers[ES]`

2. **T=100ms**: Bookmap sends bar close for 1-min bar
   - Events retrieved from buffer: 50 trades
   - `alert_engine.process_events(50 trades)` - registered with absorption detector
   - `alert_engine.process_bar(bar)` - main processing

3. **T=100ms - Regime Detection**
   - New close at 4523.00 added to price buffer
   - Short MA = avg(closes[-5:]) = 4522.50
   - Long MA = avg(closes[-20:]) = 4521.00
   - Short > Long + uptrend = UPTREND

4. **T=100ms - Absorption Detection**
   - Group 50 trades into price levels
   - Find price level with 1000 shares of BUY absorption
   - Opposite side: 200 shares of SELL (only 20%)
   - Absorption ratio: 1000 / 15000 (bar volume) = 6.7%
   - Delta ratio: 800 / 1000 = 80%
   - Confidence: 6.7% × (80% / 50%) = 10.7%
   - **ABSORPTION SIGNAL GENERATED**

5. **T=100ms - Follow-Through Gate**
   - New signal submitted to gate
   - No prior signals yet - added to pending
   - Gate does NOT confirm yet (need 2 bars)

6. **T=160ms**: Next bar close
   - Similar absorption detected on same side/price
   - New signal submitted
   - Gate finds pending signal from previous bar
   - Same side? YES
   - Within 5s window? YES (0.06s elapsed)
   - Price within ±2%? YES
   - Adding as confirmation
   - Now has 2 bars = confirmation_count >= min (2)
   - Volume ratio average = (6.7% + 5.2%) / 2 = 5.95% (fails > 40% threshold)

7. **T=160ms - Alert Decision**
   - Confirmation not passed gate (low volume ratio)
   - BUT initial signal confidence > 60%
   - AND regime is non-volatile
   - **GENERATE ALERT**

8. **T=160ms - Alert Delivery**
   - Alert queued for WhatsApp
   - Async task sends via Twilio
   - Alert marked `whatsapp_sent = true`
   - Statistics updated

## Performance Characteristics

### Latency

| Operation | Latency |
|-----------|---------|
| Feed → Buffer | <1ms |
| Regime detection | <5ms |
| Absorption detection | 10-50ms (depends on event count) |
| Follow-through gate | <5ms |
| Alert generation | <2ms |
| WhatsApp delivery | 500-2000ms (network) |
| **Total (end-to-end)** | **~600ms** |

### Memory

| Component | Memory |
|-----------|--------|
| Price history (1000 bars) | ~100KB |
| Event buffers (10K events) | ~5MB |
| Pending signals (100) | ~500KB |
| Alert statistics | ~50KB |
| **Total** | **~6MB** |

### Throughput

- **Events/second**: 500-2000 (depends on symbol)
- **Bars/second**: 10-100 (depends on timeframe)
- **Alerts/second**: 0-10 (highly variable)

## Error Handling

### Graceful Degradation

1. **Feed disconnection**
   - Service logs error
   - Attempts reconnect
   - Continues with cached regime

2. **Twilio failure**
   - Logs failed delivery
   - Alert still generated (logged)
   - Retries on next loop

3. **Configuration error**
   - Validation on load
   - Clear error messages
   - Service exits cleanly

### Cleanup & Maintenance

```python
# Every 60 seconds
- followthrough_gate.cleanup_expired()  # Remove 5s+ old signals
- absorption_detector.clear_old_events(max_age=300s)  # 5-minute window
- Log statistics
```

## Testing Strategy

### Unit Testing
- Regime detection with fixed bars
- Absorption calculations with known trades
- Signal gating with predetermined inputs

### Integration Testing
- Mock feed → alert generation
- Backtest with historical data
- Performance profiling

### Production Testing
- Paper trading mode
- Small position sizes
- Live monitoring

## Extension Points

1. **Custom Feed Adapters**
```python
class MyFeedAdapter(FeedAdapter):
    async def connect(self): ...
    async def subscribe(self, symbols): ...
```

2. **Custom Alert Callbacks**
```python
alert_engine.register_alert_callback(
    lambda alert: execute_trade(alert)
)
```

3. **Custom Delivery Channels**
```python
class SlackDelivery:
    async def send_alert(self, alert): ...
```

4. **Custom Detection Logic**
```python
class CustomAbsorptionDetector(AbsorptionDetector):
    def _detect_side_absorption(self, ...):
        # Custom algorithm
```

## Monitoring & Observability

### Metrics Exported

```python
alert_engine.stats:
- total_alerts
- alerts_by_type[AlertType]
- alerts_by_severity[Severity]
- alerts_by_symbol[str]
- whatsapp_sent
- email_sent
- last_alert_timestamp

whatsapp_delivery.get_stats():
- sent (count)
- failed (count)
- queued (count)
- from_phone
- to_phone
```

### Logging

```
2026-05-05 10:30:47,789 - alert_engine - INFO - Generated ABSORPTION alert for ES
2026-05-05 10:30:47,890 - delivery_whatsapp - INFO - WhatsApp sent: SM123...
```

## Deployment Considerations

### Scalability

- Single instance: 4-8 symbols
- Distributed: Use Redis for shared state
- Clustering: Multiple services with load balancer

### High Availability

- Process supervision (systemd, supervisor)
- Log aggregation (ELK, Splunk)
- Error alerting

### Security

- .env for secrets (never commit)
- Twilio API keys rotated regularly
- Feed credentials separate per environment
