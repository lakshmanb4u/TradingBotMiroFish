# Live Orderflow Alert Service

A production-ready Python service for real-time orderflow analysis, regime detection, absorption pattern identification, and WhatsApp alert delivery.

## Features

- **Live Feed Adapters**: Rithmic and Bookmap API integration
- **Regime Detection**: Identifies market trends, ranges, breakouts using moving averages and volatility
- **Absorption Detection**: Identifies large order absorption patterns
- **Follow-Through Gate**: Confirms signals with multi-bar continuation analysis
- **WhatsApp Alerts**: Real-time alerts via Twilio WhatsApp API
- **Mock Feed Mode**: Synthetic data generation for testing
- **Replay Harness**: Backtest against historical data
- **Comprehensive Logging**: Structured logging for monitoring

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Live Orderflow Service                    │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   Rithmic    │  │   Bookmap    │  │  Mock Feed   │       │
│  │   Adapter    │  │   Adapter    │  │   (Testing)  │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                  │                │
│         └─────────────────┴──────────────────┘                │
│                           │                                   │
│                    ┌──────▼──────┐                            │
│                    │ Event Buffer │                           │
│                    └──────┬───────┘                            │
│                           │                                   │
│         ┌─────────────────┴─────────────────┐                │
│         │                                   │                │
│   ┌─────▼──────────┐           ┌───────────▼────────┐        │
│   │ Regime Detector│           │ Absorption Detector│        │
│   │ - MA trend     │           │ - Volume analysis  │        │
│   │ - Volatility   │           │ - Order clustering │        │
│   │ - Support/Res  │           │ - Delta absorption │        │
│   └─────┬──────────┘           └────────┬───────────┘        │
│         │                               │                    │
│         └───────────────┬───────────────┘                     │
│                         │                                    │
│                  ┌──────▼─────────┐                          │
│                  │ Follow-Through  │                         │
│                  │      Gate       │                         │
│                  │ - Multi-bar     │                         │
│                  │ - Volume ratio  │                         │
│                  └────────┬────────┘                          │
│                           │                                   │
│                  ┌────────▼────────┐                          │
│                  │  Alert Engine   │                          │
│                  │ - Alert Gen     │                          │
│                  │ - Severity      │                          │
│                  │ - Statistics    │                          │
│                  └────────┬────────┘                          │
│                           │                                   │
│              ┌────────────┴────────────┐                     │
│              │                         │                     │
│       ┌──────▼─────────┐      ┌───────▼──────┐              │
│       │  WhatsApp      │      │   Logging    │              │
│       │  Delivery      │      │   (Metrics)  │              │
│       │  (Twilio)      │      │              │              │
│       └────────────────┘      └──────────────┘              │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Python 3.8+
- pip
- Redis (optional, for distributed deployment)

### Setup

1. Clone or navigate to the service directory:
```bash
cd market-swarm-lab/services/live_trading/
```

2. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.template .env
# Edit .env with your configuration
```

## Configuration

### Key Environment Variables

**Feed Configuration:**
```bash
# Rithmic
RITHMIC_USERNAME=your_username
RITHMIC_PASSWORD=your_password
RITHMIC_ACCOUNT_ID=your_account_id
RITHMIC_ENVIRONMENT=paper  # or 'live'

# Bookmap
BOOKMAP_API_KEY=your_api_key
BOOKMAP_HOST=localhost
BOOKMAP_PORT=9000
BOOKMAP_SYMBOLS=ES,NQ,GC,CL
```

**Alert Configuration:**
```bash
ALERT_MIN_ORDER_SIZE=100
ALERT_MIN_ABSORPTION_SIZE=500
ALERT_MIN_FOLLOW_THROUGH=300
ALERT_REGIME_THRESHOLD_USD=10000
```

**WhatsApp Delivery:**
```bash
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1234567890
ALERT_RECIPIENT_PHONE=+1987654321
ALERT_WHATSAPP_ENABLED=true
```

**Regime Detection:**
```bash
REGIME_MA_SHORT=5      # 5-bar short MA
REGIME_MA_LONG=20      # 20-bar long MA
REGIME_ATR_PERIOD=14   # ATR calculation period
REGIME_VOLATILITY_THRESHOLD=0.02  # 2% threshold
```

**Absorption Detection:**
```bash
ABSORPTION_TIME_WINDOW_MS=2000      # 2-second window
ABSORPTION_DELTA_MIN_RATIO=0.5      # 50% min delta ratio
ABSORPTION_VOLUME_MIN_PCT=0.3       # 30% of bar volume
```

**Follow-Through Confirmation:**
```bash
FOLLOWTHROUGH_TIME_WINDOW_MS=5000   # 5-second window
FOLLOWTHROUGH_MIN_CONFIRMATION_COUNT=2  # 2 confirmations
FOLLOWTHROUGH_MIN_VOLUME_RATIO=0.4  # 40% volume ratio
```

## Running the Service

### Live Mode

Start the service with live data:
```bash
python live_service.py
```

Monitor output:
```
2026-05-05 10:30:45,123 - __main__ - INFO - Starting Live Orderflow Alert Service
2026-05-05 10:30:46,456 - __main__ - INFO - Initialized adapters for 4 symbols
2026-05-05 10:30:47,789 - __main__ - INFO - Service started
```

### Mock Feed Mode (Testing)

Generate synthetic data and test the pipeline:
```bash
python test_mock_feed.py
```

Results:
```
============================================================
MOCK FEED TEST RESULTS
============================================================
Total Alerts Generated: 15

Alerts by Type:
  ABSORPTION: 8
  FOLLOW_THROUGH: 4
  REGIME_CHANGE: 3

Alerts by Severity:
  LOW: 2
  MEDIUM: 8
  HIGH: 5
  CRITICAL: 0

Sample Alerts:
  ES ABSORPTION @ $4532.50 (Vol: 5000.0, Conf: 75.0%)
  NQ FOLLOW_THROUGH @ $14200.25 (Vol: 3200.0, Conf: 82.0%)
  ...
============================================================
```

### Replay Mode (Backtesting)

Test against historical data:
```bash
# With bar data
python replay_harness.py data/historical_bars.csv results/replay_results.json

# With event data
python replay_harness.py data/order_events.csv results/replay_results.json
```

Replay output:
```
============================================================
REPLAY SUMMARY
============================================================
Total Alerts: 47
WhatsApp Sent: 0
Email Sent: 0

By Type:
  ABSORPTION: 28
  FOLLOW_THROUGH: 12
  REGIME_CHANGE: 7

By Severity:
  LOW: 5
  MEDIUM: 22
  HIGH: 16
  CRITICAL: 4

By Symbol:
  ES: 25
  NQ: 16
  GC: 6
============================================================
```

## Data Formats

### Event CSV Format

For replay with order events:
```csv
timestamp,symbol,price,size,side,order_id,is_market
1703000000.123,ES,4520.50,100,BUY,ORD001,true
1703000000.234,ES,4520.75,250,SELL,ORD002,true
1703000000.345,ES,4520.25,150,BUY,ORD003,false
```

### Bar CSV Format

For replay with OHLCV bars:
```csv
timestamp,symbol,open,high,low,close,volume,bid_volume,ask_volume
1703000060,ES,4520.00,4522.50,4519.75,4521.25,50000,28000,22000
1703000120,ES,4521.25,4523.00,4520.50,4522.00,45000,20000,25000
1703000180,ES,4522.00,4523.50,4521.00,4523.00,55000,35000,20000
```

## Alert Types

### ABSORPTION
Large orders absorbed without immediate price movement, indicating institutional interest.
```
🚨 ABSORPTION
Symbol: ES
Side: BUY
Price: $4520.50
Volume: 5,000
Severity: MEDIUM
Regime: UPTREND
Volatility: 0.75%
Note: Volume: 5000.0 | Ratio: 14.3% | Confidence: 75.0% | Regime: UPTREND | Trend Strength: 65.0%
⏰ 10:30:45
```

### FOLLOW_THROUGH
Absorption confirmed by continued volume on the same side in subsequent bars.
```
🚨 FOLLOW_THROUGH
Symbol: NQ
Side: SELL
Price: $14200.00
Volume: 3,200
Severity: HIGH
Regime: DOWNTREND
Volatility: 1.20%
⏰ 10:31:12
```

### REGIME_CHANGE
Market transitioned to a new regime (uptrend, downtrend, breakout, etc.).
```
🚨 REGIME_CHANGE
Symbol: GC
Side: BUY
Price: $2050.25
Volume: 2,000
Severity: MEDIUM
Regime: BREAKOUT
⏰ 10:32:00
```

## Monitoring & Metrics

Service exposes metrics on configured port (default 8888):
```bash
curl http://localhost:8888/metrics
```

Key metrics:
- `alerts_total` - Total alerts generated
- `alerts_by_type` - Breakdown by type
- `alerts_by_severity` - Breakdown by severity
- `whatsapp_sent` - WhatsApp deliveries
- `whatsapp_failed` - Failed WhatsApp attempts
- `absorption_detected` - Absorption signals found
- `followthrough_confirmed` - Confirmations passed gate
- `processing_latency_ms` - Alert generation latency

## Integration Examples

### With Existing Trading System

```python
from live_service import LiveOrderflowService
from config import load_config

config = load_config()
service = LiveOrderflowService(config)

# Register custom callback
async def my_alert_handler(alert):
    if alert.severity.value >= 3:  # HIGH or CRITICAL
        # Execute trade logic
        await place_trade(alert.symbol, alert.side, alert.volume)

service.alert_engine.register_alert_callback(my_alert_handler)

# Start service
await service.initialize()
await service.start()
```

### With External APIs

```python
# Add custom delivery channel
class SlackDelivery:
    async def send_alert(self, alert):
        webhook_url = "https://hooks.slack.com/..."
        await post_to_slack(webhook_url, alert.format_for_slack())

slack_delivery = SlackDelivery()
service.alert_engine.register_alert_callback(
    lambda alert: slack_delivery.send_alert(alert)
)
```

## Troubleshooting

### No Alerts Generated

1. Check regime detector is initialized:
```python
regime = service.alert_engine.get_regime("ES")
print(f"Current regime: {regime}")
```

2. Verify absorption sensitivity:
```bash
# Lower thresholds in .env
ABSORPTION_VOLUME_MIN_PCT=0.15
ABSORPTION_DELTA_MIN_RATIO=0.3
```

3. Check feed connectivity:
```bash
SERVICE_DEBUG_MODE=true
SERVICE_LOG_LEVEL=DEBUG
```

### Feed Connection Issues

1. Verify host/port configuration
2. Check firewall rules
3. Test connectivity:
```bash
telnet localhost 9000  # For Bookmap
```

### WhatsApp Not Sending

1. Verify Twilio credentials in `.env`
2. Check phone numbers (include country codes)
3. Test with mock mode:
```bash
SERVICE_MOCK_FEED_MODE=true
```

## Performance Tuning

### High Latency

```bash
# Reduce tick interval
SERVICE_TICK_INTERVAL_MS=50

# Reduce history depth
SERVICE_HISTORY_DEPTH=500
```

### High CPU Usage

```bash
# Increase tick interval
SERVICE_TICK_INTERVAL_MS=200

# Use smaller absorption time window
ABSORPTION_TIME_WINDOW_MS=1000
```

## Production Deployment

1. **Environment Setup**
   - Use `.env` for secrets (never commit)
   - Deploy with systemd or Docker
   - Use process manager (supervisor, systemd)

2. **Monitoring**
   - Enable Prometheus metrics export
   - Set up alert dashboards
   - Monitor error log rate

3. **High Availability**
   - Run multiple instances
   - Use Redis for state sharing
   - Implement failover logic

4. **Testing**
   - Always backtest with replay harness first
   - Use paper trading mode
   - Start with small position sizes

## Testing

### Unit Tests
```bash
python -m pytest tests/
```

### Integration Tests
```bash
# Mock feed test
python test_mock_feed.py

# Replay test
python replay_harness.py data/test_bars.csv /tmp/test_results.json
```

## Files

- `config.py` - Configuration management
- `data_types.py` - Core data structures
- `feed_adapters.py` - Rithmic/Bookmap adapters
- `regime_detector.py` - Market regime detection
- `absorption_detector.py` - Order absorption detection
- `followthrough_gate.py` - Confirmation gate logic
- `alert_engine.py` - Main alert orchestration
- `delivery_whatsapp.py` - WhatsApp delivery
- `live_service.py` - Main service
- `replay_harness.py` - Historical backtesting
- `test_mock_feed.py` - Mock feed testing
- `.env.template` - Configuration template
- `requirements.txt` - Python dependencies

## License

Internal use only - proprietary market research system.

## Support

For issues or questions, check logs at `./logs/errors.log` and enable debug mode in `.env`.
