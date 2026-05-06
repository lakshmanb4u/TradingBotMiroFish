# Live Orderflow Alert Service - Startup Guide

## Quick Start (5 minutes)

### Step 1: Setup Environment

```bash
cd market-swarm-lab/services/live_trading/

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Service

```bash
# Copy template
cp .env.template .env

# Edit with your settings
nano .env
```

**Minimal configuration** (.env):
```bash
# For mock testing only:
SERVICE_MOCK_FEED_MODE=true
SERVICE_LOG_LEVEL=INFO

# For Bookmap live:
BOOKMAP_API_KEY=your_key
BOOKMAP_HOST=localhost
BOOKMAP_PORT=9000
BOOKMAP_SYMBOLS=ES,NQ

# For WhatsApp alerts:
TWILIO_ACCOUNT_SID=ACxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxx
TWILIO_PHONE_NUMBER=+15551234567
ALERT_RECIPIENT_PHONE=+15559876543
ALERT_WHATSAPP_ENABLED=true
```

### Step 3: Test with Mock Feed

```bash
# Test signal generation without live data
python test_mock_feed.py
```

Expected output:
```
2026-05-05 10:30:45,123 - __main__ - INFO - Starting mock feed test for ['ES', 'NQ'] (30s)
...
============================================================
MOCK FEED TEST RESULTS
============================================================
Total Alerts Generated: 12-18 (typically)
...
```

### Step 4: Start Live Service

```bash
# Make sure .env is configured with live feed details
python live_service.py
```

Monitor the console:
```
2026-05-05 10:30:47,123 - __main__ - INFO - Starting Live Orderflow Alert Service
2026-05-05 10:30:47,456 - __main__ - INFO - Initialized adapters for 4 symbols
2026-05-05 10:30:48,789 - __main__ - INFO - Service started
```

## Detailed Setup

### Prerequisites

- Python 3.8+
- Rithmic account (for live Rithmic data) OR
- Bookmap API key (for live Bookmap data)
- Twilio account (for WhatsApp delivery)
- Redis (optional, for distributed deployment)

### Environment File Structure

The `.env` file controls all service behavior:

```bash
# ============ FEED SOURCE ============
# Choose ONE feed adapter:

# Option A: Bookmap
BOOKMAP_API_KEY=abc123def456
BOOKMAP_HOST=localhost
BOOKMAP_PORT=9000
BOOKMAP_SYMBOLS=ES,NQ,GC,CL

# Option B: Rithmic
RITHMIC_USERNAME=your_username
RITHMIC_PASSWORD=your_password
RITHMIC_ACCOUNT_ID=ACCT123
RITHMIC_ENVIRONMENT=paper

# Option C: Mock (for testing)
SERVICE_MOCK_FEED_MODE=true

# ============ ALERT SENSITIVITY ============
# Lower values = more alerts (may increase false positives)
# Higher values = fewer alerts (may miss trades)

ALERT_MIN_ORDER_SIZE=100              # Min order size to track
ALERT_MIN_ABSORPTION_SIZE=500         # Min absorption volume
ALERT_MIN_FOLLOW_THROUGH=300          # Min confirmation volume
ABSORPTION_VOLUME_MIN_PCT=0.30        # % of bar volume (30%)
ABSORPTION_DELTA_MIN_RATIO=0.5        # Delta/volume ratio (50%)

# ============ REGIME DETECTION ============
# These control trend identification

REGIME_MA_SHORT=5                     # Short moving average bars
REGIME_MA_LONG=20                     # Long moving average bars
REGIME_ATR_PERIOD=14                  # Volatility calculation
REGIME_VOLATILITY_THRESHOLD=0.02      # High volatility threshold

# ============ WHATSAPP DELIVERY ============
# For manual testing:
# 1. Create Twilio account at twilio.com
# 2. Get Account SID and Auth Token from dashboard
# 3. Provision a WhatsApp-enabled phone number
# 4. Your recipient phone must be verified in Twilio

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+15551234567        # Twilio WhatsApp number
ALERT_RECIPIENT_PHONE=+15559876543      # Your phone number
ALERT_WHATSAPP_ENABLED=true

# ============ SERVICE BEHAVIOR ============
SERVICE_LOG_LEVEL=INFO                # DEBUG, INFO, WARNING, ERROR
SERVICE_TICK_INTERVAL_MS=100          # Processing loop interval
SERVICE_MOCK_FEED_MODE=false          # Use synthetic data
SERVICE_DEBUG_MODE=false              # Extra logging
MONITOR_ENABLED=true                  # Prometheus metrics
MONITOR_METRICS_PORT=8888             # Metrics endpoint
```

### Workflow: Development → Testing → Production

#### 1. Development (Local, Mock Feed)

```bash
# .env
SERVICE_MOCK_FEED_MODE=true
SERVICE_LOG_LEVEL=DEBUG
ALERT_WHATSAPP_ENABLED=false  # Don't send actual messages yet

# Run test
python test_mock_feed.py
```

#### 2. Testing (Paper Trading, Live Bookmap)

```bash
# .env
SERVICE_MOCK_FEED_MODE=false
SERVICE_LOG_LEVEL=INFO
BOOKMAP_API_KEY=your_test_key
BOOKMAP_HOST=localhost
BOOKMAP_PORT=9000
ALERT_WHATSAPP_ENABLED=false  # Still log only

# In another terminal, tail logs
tail -f logs/errors.log

# Start service
python live_service.py
```

#### 3. Production (Live Trading)

```bash
# .env - AFTER successful testing
SERVICE_MOCK_FEED_MODE=false
SERVICE_LOG_LEVEL=INFO
BOOKMAP_API_KEY=your_production_key
RITHMIC_ENVIRONMENT=live
ALERT_WHATSAPP_ENABLED=true

# Monitor continuously
watch -n 1 'python -c "
import json
with open(\"/tmp/mock_feed_test_results.json\") as f:
    data = json.load(f)
    print(f\"Last 5 minutes: {len(data)} alerts\")
"'

# Start with supervision
python live_service.py 2>&1 | tee logs/service.log
```

## Common Workflows

### Test Without Real Data

```bash
# Fastest way to validate installation
python test_mock_feed.py

# Expected output: 12-18 alerts generated in 20 seconds
# If successful, all components are working
```

### Backtest Against Historical Data

```bash
# Create sample bars CSV (or use existing data)
python replay_harness.py data/historical_bars.csv results/backtest_report.json

# View results
cat results/backtest_report.json | python -m json.tool
```

### Monitor Live Feed

```bash
# Terminal 1: Start service
python live_service.py

# Terminal 2: Watch alerts (requires Linux/Mac)
python -c "
import time
import json
last_count = 0
while True:
    try:
        # In production, read from metrics endpoint
        print(f'Monitoring... (check logs in terminal 1)')
        time.sleep(5)
    except KeyboardInterrupt:
        break
"
```

## Troubleshooting

### Issue: ImportError: No module named 'dotenv'

**Solution:**
```bash
pip install -r requirements.txt
```

### Issue: No alerts generated with mock feed

**Solution 1:** Check configuration
```bash
grep -E "ABSORPTION|REGIME|FOLLOWTHROUGH" .env
# If empty, copy from .env.template
cp .env.template .env
```

**Solution 2:** Check logs
```bash
python test_mock_feed.py 2>&1 | head -50
```

**Solution 3:** Lower sensitivity
```bash
# Edit .env
ABSORPTION_VOLUME_MIN_PCT=0.15  # Was 0.30
ABSORPTION_DELTA_MIN_RATIO=0.3  # Was 0.5
python test_mock_feed.py
```

### Issue: WhatsApp alerts not sending

**Checklist:**
- [ ] Twilio account has WhatsApp sandbox enabled
- [ ] Credentials are correct in .env
- [ ] Recipient phone is verified in Twilio sandbox
- [ ] Phone numbers include country codes (+1, etc)
- [ ] Testing with mock data first (SERVICE_MOCK_FEED_MODE=true)

**Debug:**
```python
from config import load_config

config = load_config()
print(f"TWILIO SID: {config.whatsapp.account_sid[:5]}...")
print(f"From: {config.whatsapp.phone_number}")
print(f"To: {config.whatsapp.recipient_phone}")
```

### Issue: High CPU usage

**Solution:** Increase tick interval
```bash
# .env
SERVICE_TICK_INTERVAL_MS=200  # Was 100
```

### Issue: Lost connection to feed

**Solution:** Check connectivity
```bash
# Test Bookmap connection
python -c "
import socket
s = socket.socket()
result = s.connect_ex(('localhost', 9000))
print('Bookmap reachable' if result == 0 else 'Connection failed')
"

# Restart service
pkill -f live_service.py
python live_service.py
```

## Performance Expectations

### Mock Feed Test
- **Duration**: 20 seconds
- **Alerts**: 12-18
- **CPU**: <10%
- **Memory**: <50MB

### Live Bookmap (4 symbols)
- **Latency**: <500ms alert-to-WhatsApp
- **CPU**: 5-15%
- **Memory**: 100-200MB
- **Throughput**: 100-500 trades/second

## Next Steps

1. **Backtest** with historical data using replay_harness.py
2. **Paper trade** with PAPER environment variable set
3. **Monitor** in production with log aggregation
4. **Alert tuning** based on real market conditions

## Production Checklist

- [ ] .env configured with production credentials
- [ ] Tested with mock feed successfully
- [ ] Backtested with historical data
- [ ] WhatsApp alerts working in test mode
- [ ] Error log monitoring set up
- [ ] Process supervision configured (systemd/supervisor)
- [ ] Disk space for logs checked
- [ ] Backup of .env file secured

## Getting Help

### Enable Debug Logging
```bash
SERVICE_LOG_LEVEL=DEBUG
python live_service.py 2>&1 | tee full_debug.log
```

### Check System Requirements
```bash
python3 --version          # Need 3.8+
pip --version
python -c "import sys; print(sys.path)"
```

### Validate Installation
```bash
python -c "
from config import load_config
from feed_adapters import MockFeedAdapter
from alert_engine import AlertEngine
print('✓ All imports successful')
"
```

## Support Resources

- **Configuration**: See `.env.template` for all available options
- **Data Types**: `data_types.py` - All alert and data structures
- **Architecture**: `README.md` - System design and pipeline
- **Examples**: `test_mock_feed.py` - Working example
- **Backtest**: `replay_harness.py` - Historical testing

---

Ready to start? Run:
```bash
python test_mock_feed.py
```

Should see alerts within 20 seconds ✓
