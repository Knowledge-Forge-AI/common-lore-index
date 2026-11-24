# ADR 0214 — Prepared-stage COPY count and reader custody

## Status

Accepted for bounded local source implementation under
`REPOMAP-CLOE-RESOURCE-GOVERNANCE10` after independent design review.
Resulting-source acceptance follows scoped proof and review.

## Context

ADRs [0190](0190-prepared-row-spool-admission-and-cancellation.md),
[0191](0191-bounded-materialization-and-copy-cancellation.md), and
[0192](0192-cooperative-source-capture-cancellation.md) bound work up to the
prepared-row handoff. The staged ingestion path previously waited until the
database completeness check to detect transferred cardinality drift.

An in-process synthetic COPY sink observed seven family sessions for both a
short and an overlong first family. The overlong family wrote all three rows
against a prepared count of one. An injected output failure left a
deliberately retained spool iterator open after the backing path was removed.
These observations show the work boundary and the distinction between reader
release and path removal.

## Decision

Use the existing `PreparedStageRows.row_counts[family]` contract inside the
ordinary and measured staged COPY paths. Missing, Boolean, non-integer, and
negative counts fail with the existing completeness error before opening that
family. A single lazy replay writes no more than the declared count. It can
acquire one proving excess row and refuses before adapting or writing it.
A short family refuses on exhaustion. Existing PostgreSQL completeness
validation remains authoritative for successful COPY. The new check proves
cardinality and does not verify row content identity.

The staged caller closes the bounded view and explicitly releases the exact
RowSpool iterator it acquired. Caller-owned non-spool iterators retain their
existing lifetime. After successful COPY and reader settlement, remove that
family's spool path. Outer stage teardown owns paths that remain after
refusal. Preserve the active error delivered by the COPY context when
local reader cleanup fails. The Psycopg COPY context retains its existing
error precedence, including errors it raises while ending the COPY command.

A staged count mismatch now fails during loading, before later families,
statistics and the prepared-state commit. The existing transaction owner
rolls back the COPY and marks the already created attempt failed. The prior
accepted publication stays readable. Successful stages follow the existing
prepared, validating, validated and publication transitions. The product
caller census finds the inline staged-ingestion sequence and the retained
population helper. Direct uses of the generic COPY function retain its
existing API and caller-owned transaction contract.

## Evidence and rollback

Require exact scoped unit and disposable PostgreSQL proof. Verify both
declared prefixes and EOF boundaries, retained-reader release while the
original error is still referenced, count-skew rollback before statistics,
prior accepted receipt preservation and successful recovery. Preserve the
existing family order, spool release sequence and checksum assertions in the
lifetime owner. Identify the family by the catalog table passed to the
transport.

Revert the coordinated source change without a migration. The decision
preserves existing SQL, transaction and reconciliation authority, public
report and CLI/MCP surfaces, dependency pins and runner policies. It supplies
no physical-I/O, RSS, latency, native cancellation or hosted qualification
claim.
