# REPOMAP-CLOE-RESOURCE-GOVERNANCE11 Counter Continuity Exit

[ADR 0215](../../docs/adr/2026/11/0215-staged-resource-counter-continuity.md)
defines the corrected staged observation. A missing or decreasing WAL
position or temporary-byte count yields the existing unavailable state.
Temporary bytes also require the same database ID and statistics reset
generation at both samples. Valid flat pairs still emit measured zero.

The observer reads the database ID and reset timestamp in its existing
statement. The identity stays in the private snapshot. Report schema,
measurement categories, units and memory sampling stay compatible.

## Evidence

Before the correction, the observer reported measured zero for synthetic
WAL positions 300 to 290 and temporary totals 200 to 100. Unit cases cover
flat, increasing, decreasing and missing samples, reset timestamps with
flat or higher totals, database mismatch, and unusable identities.

The scoped unit run passed 43 cases with
`--suite unit --no-coverage --jobs 1`.

```text
src/test/unit/python/repomap_kg/storage/arch2c_staging_observability.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_view_projections.unit.test.py
```

Disposable PostgreSQL integration passed nine cases with
`--suite int --no-coverage --jobs 1 --pg-container-port 55433`.

```text
src/test/int/python/repomap_kg/storage/arch2c_staging_observability.int.test.py
src/test/int/python/repomap_kg/ops/resource_view_server.int.test.py
```

A test invoked `pg_stat_reset()` inside its disposable fixture and observed
the same database ID with a changed reset timestamp. Its temporary-byte
observation was unavailable and had no numeric value. The scoped tests
also exercised report projection and ordinary publication parity.
Scoped typing, Ruff, full Linux retention enforcement and independent
source review passed. The fixed integration harness left zero owned
ephemeral image or volume residue.

## Campaign state

Hosted capacity remains AVAILABLE. This local checkpoint used zero
hosted attempts. CI156 qualifies its exact published staging commit.
Complete local unit, integration, smoke, staging, system and combined
suites and local coverage were unselected. The PostgreSQL counters are
shared samples that can lag, so physical I/O and operation attribution
remain outside the evidence. Full W7 acceptance remains open.
