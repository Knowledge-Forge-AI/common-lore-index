# ADR 0215 — Staged resource counter continuity

## Status

Route A selected on 2026-11-18 for
`REPOMAP-CLOE-RESOURCE-GOVERNANCE11`.

## Context

The staged PostgreSQL observer formerly clamped backwards counter pairs
to zero. A synthetic witness fed the existing emitter declining WAL and
database temporary-byte observations. Both were exported as measured zero.
A temporary-byte reset can also be hidden by subsequent growth.

## Decision

Use the existing unavailable state for a missing or decreasing counter.
For database temporary bytes, bind both observations to the same
`pg_stat_database.datid` and `stats_reset` generation. A valid database ID
with a null reset timestamp is a comparable generation when both
observations have that same identity. A missing or malformed identity and
a change of database ID or reset timestamp leave the delta unavailable.
Matching flat counters still report measured zero.

The observer samples the identity in its existing statement and retains it
in a private snapshot. The identity and timestamp do not enter emitted
events. Preserve the existing report version, units, categories, and
availability vocabulary. Product sampling does not reset statistics,
force a flush, or change a role. PostgreSQL statistics remain shared and
can lag. This decision provides continuity checks for the sampled pairs
and makes no physical-I/O, peak-RSS, or whole-operation attribution claim.
[PostgreSQL 16 monitoring statistics](https://www.postgresql.org/docs/16/monitoring-stats.html)

## Evidence and rollback

Require exact scoped proofs of stable zero and nonzero observations,
decreasing pairs, missing identities, database mismatch, and reset despite
equal or higher totals. Verify the observed database ID and changed reset
timestamp in one disposable PostgreSQL fixture. Exercise the existing
staging report and ordinary publication parity. Revert source and tests
together without migrating data or changing report schema.
