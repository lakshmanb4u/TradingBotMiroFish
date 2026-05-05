# Bookmap BMF Conversion Guide

## Official Conversion Path

### Requirements
- Bookmap 7.4+ installed
- GUI access or CLI tools available

### GUI Conversion Method
1. Open Bookmap
2. File → Load Recording → Select .bmf file
3. File → Export Data:
   - Format: CSV
   - Fields: Timestamp (ns), Price, Bid/Ask, Depth, Liquidity, Volume
   - Timezone: UTC
   - Output: `state/orderflow/raw/YYYYMMDD_ES.csv`

### CLI Conversion (If Available)
1. Install Bookmap CLI tools
2. Run:
