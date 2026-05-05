# Bookmap BMF Format Support

## Native BMF File Handling

### Format Characteristics
- Binary format with tick-by-tick market data
- Contains:
  - Price levels
  - Order book depth
  - Liquidity information
  - Trade executions
- Typically several GB per session

### Official Conversion Options
1. **Bookmap GUI Export**:
   - File → Export Data → CSV/JSON
   - Recommended for reliable conversion

2. **Bookmap API**:
   - Java SDK available for direct access
   - Requires Bookmap installation

3. **Command Line Tools**:
   - `bookmap-cli` (if installed)
   - Example: `bookmap-cli convert input.bmf output.csv`

### Implementation Strategy
1. **Preferred Path**:
   - Use official Bookmap GUI/CLI to convert to CSV
   - Process CSV through existing pipeline

2. **Direct Parsing Considerations**:
   - Reverse engineering discouraged
   - Format changes between versions
   - No official Python parser

### Replay Safety
- Always:
  - Preserve original timestamps
  - Maintain event ordering
  - Validate monotonic timestamps
  - Disable any condensing/aggregation

### Field Availability
