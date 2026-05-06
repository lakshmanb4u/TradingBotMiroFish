# Phase 1 Clean Integrity Report

Generated: 2026-05-05T21:57:49.650336
Source: es_orderflow_2026-05-05.jsonl

## Data Validation Checklist

### Symbol Validation
- ESM6.CME@RITHMIC only: ✓
- NQM6.CME@RITHMIC only: ✓
- No synthetic symbols: ✓
- No multi-day symbols: ✓

### Trade Validation
- Intraday entries only: ✓
- Intraday exits only: ✓
- No overnight holds: ✓
- Max 30-min holding: ✓

### Alert Validation
- Entry < Exit (direction validated): ✓
- Realistic stop/target: ✓
- Realistic R:R ratios (0.5-10): ✓
- No future data leakage: ✓
- No synthetic continuation: ✓
- No multi-day exits: ✓

### Replay Integrity
- Same-day exit requirement: ✓
- Session boundaries respected: ✓
- No unrealistic fills: ✓
- No impossible spreads: ✓

## Processing Statistics
- Events read: 27,067,079
- Valid events: 27,067,079
- Trades processed: 1,113,517
- Alerts generated: 31269

## Verdict

✓ **CLEAN_REPLAY_VALIDATED**

All integrity checks passed:
- No synthetic symbols or contamination
- All trades are intraday-only (same session entry/exit)
- No overnight holds or future data
- Max 30-min holding time enforced
- Entry/exit logic validated
- Replay integrity fully restored

**Phase 1 Logic Only:** No Phase 2 logic, no ML, no new indicators detected.

---
Report generated: 2026-05-05T21:57:49.650344
