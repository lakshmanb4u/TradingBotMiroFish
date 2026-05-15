# ALL STALE CANDIDATE PATTERNS - COMPREHENSIVE AUDIT
**Executed:** 2026-05-13T04:45:06.991552Z
**Symbol:** NQM6.CME@RITHMIC
**Total Candidates Audited:** 190
**Valid Candidates:** 62
**Quarantined Candidates:** 128

---

## CANDIDATE AGE DISTRIBUTION

### Statistics (All 190 candidates)

| Metric | Value (seconds) |
|--------|-----------------|
| **Minimum** | 0.00 |
| **P50 (Median)** | 0.10 |
| **P90** | 0.40 |
| **P95** | 0.50 |
| **P99** | 0.50 |
| **Maximum** | 0.50 |
| **Mean** | 0.15 |

### Distribution Percentiles
```
Min:       0.00s
P50:       0.10s (median candidate age)
P90:       0.40s (90th percentile)
P95:       0.50s (95th percentile)
P99:       0.50s (99th percentile - extreme stale)
Max:       0.50s (worst case)
```

---

## QUARANTINE REASON BREAKDOWN

### All Quarantine Reasons (128 total)

| Reason | Count | % |
|--------|-------|-----|
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (9.00) | 8 | 6.2% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (307.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (15.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (11.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (10.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (8.00) | 6 | 4.7% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (55.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (313.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (12.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (305.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (6.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (7.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (294.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (297.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (13.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (306.00) | 4 | 3.1% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (52.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (300.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (232.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (312.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (321.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (320.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (20.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (292.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (293.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (355.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (299.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (262.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (44.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (40.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (42.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (318.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (227.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (603.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (302.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (614.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (289.00) | 2 | 1.6% |
| PRICE_DIVERGENCE_EXCEEDS_5_TICKS (62.00) | 2 | 1.6% |

### Quarantine Totals
- **TTL > 30 seconds:** 0 candidates
- **Price divergence > 5 ticks:** 128 candidates
- **Timestamp/price desync:** 0 candidates
- **Known stale patterns:** 0 candidates
- **Other reasons:** 0 candidates

---

## TOP 20 WORST STALE CANDIDATES

### Ranked by Age + Divergence Score

| Rank | UUID | Original TS | Dispatch TS | Age (s) | Orig Price | Live Price | Div (ticks) | Reason |
|------|------|-------------|-------------|---------|------------|------------|-------------|--------|
| 1 | cand_000159 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.4 | 28295.25 | 28448.75 | 614.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (614.00) |
| 2 | cand_000064 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.3 | 28295.25 | 28448.75 | 614.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (614.00) |
| 3 | cand_000157 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.2 | 28295.25 | 28446.00 | 603.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (603.00) |
| 4 | cand_000062 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28295.25 | 28446.00 | 603.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (603.00) |
| 5 | cand_000137 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.2 | 28445.75 | 28357.00 | 355.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (355.00) |
| 6 | cand_000042 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28445.75 | 28357.00 | 355.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (355.00) |
| 7 | cand_000113 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.3 | 28368.50 | 28448.75 | 321.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (321.00) |
| 8 | cand_000018 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28368.50 | 28448.75 | 321.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (321.00) |
| 9 | cand_000117 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.2 | 28293.25 | 28373.25 | 320.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (320.00) |
| 10 | cand_000022 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28293.25 | 28373.25 | 320.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (320.00) |
| 11 | cand_000154 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.4 | 28370.25 | 28449.75 | 318.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (318.00) |
| 12 | cand_000059 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.3 | 28370.25 | 28449.75 | 318.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (318.00) |
| 13 | cand_000129 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.5 | 28370.00 | 28448.50 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 14 | cand_000097 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.2 | 28369.25 | 28447.75 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 15 | cand_000172 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.2 | 28370.25 | 28448.75 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 16 | cand_000034 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28370.00 | 28448.50 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 17 | cand_000077 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28370.25 | 28448.75 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 18 | cand_000002 | 2026-05-06T00:00:00 | 2026-05-06T00:00:00 | 0.1 | 28369.25 | 28447.75 | 314.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (314.00) |
| 19 | cand_000118 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.3 | 28293.25 | 28371.50 | 313.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (313.00) |
| 20 | cand_000108 | 2026-05-12T18:27:12 | 2026-05-12T18:27:12 | 0.3 | 28294.00 | 28372.25 | 313.00 | PRICE_DIVERGENCE_EXCEEDS_5_TICKS (313.00) |


---

## STALE PATTERN ANALYSIS

### Key Findings

1. **Age Range:** 0.00s (best) to 0.50s (worst)
   - Candidates > 30s TTL: 0 detected
   - Median age: 0.10s (healthy)

2. **Price Divergence:** 
   - Candidates > 5 ticks divergence: 128 detected
   - Worst divergence: 614.00 ticks

3. **Timestamp/Price Desync:**
   - Candidates with age > 1s AND divergence > 0.5 ticks: 0 detected

4. **Known Patterns:**
   - Stale 28370.25 pattern: 0 detected
   - (Not an isolated case - indicates systematic issue)

### Integrity Verdict

**Valid Candidates:** 62/190 (32.6%)
**Quarantined:** 128/190 (67.4%)

**Status:** 
- If > 5% quarantine rate: ⚠️ SYSTEMATIC ISSUE DETECTED
- If < 5% quarantine rate: ✓ Healthy baseline

Current rate: 67.4% ⚠️ SYSTEMATIC

---

## RECOMMENDATIONS

1. **Immediate:** Review top 20 worst candidates for patterns
2. **Analysis:** Check if 28370.25 pattern repeats across sessions
3. **Prevention:** Implement age/price guard in live pipeline
4. **Monitoring:** Track quarantine rate in production
5. **Audit:** Expand to 100+ sessions for full assessment

---

**Report Generated:** 2026-05-13T04:45:06.992493Z
