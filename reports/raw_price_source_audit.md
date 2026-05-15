# Raw Price Source Audit

**Verdict:** PRICE_FIELD_MISPARSED

**File:** es_orderflow_2026-05-12.jsonl
**Last Updated:** 2026-05-12 18:30:12 UTC

## Schema
- **Price field:** `price`
- **Side field:** `side` (values: bid, ask, trade)
- **Size field:** `size`
- **Timestamp:** `ts_recv` (ISO 8601 UTC)
- **Level:** `level` (null = top-of-book)
- **Symbol:** `symbol` (NQM6.CME@RITHMIC)

## Reconstructed Top-of-Book
- **Latest BID:** 28336.0
- **Latest ASK:** 28412.0

## Known Bookmap (Visual Reference)
- **BID range:** ~29760.0
- **ASK range:** ~29765.0

## Comparison Result
**PRICE_FIELD_MISPARSED**
