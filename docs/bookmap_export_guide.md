# Bookmap Data Export Guide

## Exporting ES Order Flow Data

1. **Export Location**:
   - In Bookmap, go to: `File → Export Data`
   - Recommended path: `state/orderflow/raw/` (create if missing)
   - Name format: `ES_YYYYMMDD_HHMMSS.csv`

2. **Recommended Settings**:
   - Format: CSV (preferred) or JSON
   - Timezone: UTC (critical for replay safety)
   - Fields to include:
     - Required: Timestamp (nanosecond precision), Price, Best Bid/Ask, Bid/Ask Depth, Traded Volume, Liquidity Above/Below
     - Optional: Buy/Sell Volume, Delta, CVD, Imbalance Score
   - Time range: Full trading session (09:30-16:00 ET)

3. **UTC Handling**:
   - Verify Bookmap is configured to UTC in Settings → General
   - All timestamps will be normalized to UTC during ingestion

4. **Replay Safety**:
   - Export full sessions (no partial ranges)
   - Disable "condensed timestamps" option
   - Never edit exported files manually

5. **Verification**:
   After export, immediately validate with:
