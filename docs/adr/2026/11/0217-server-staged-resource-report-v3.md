# ADR 0217 — Report sampled Server staging counters in schema 3

## Status

Accepted for implementation. The operator selected the opt-in W7 Server
resource report alongside the CI159 workflow split.

## Decision

`cloe ops refresh-graph --resource-report FILE --resource-report-schema 3`
selects a direct Server PostgreSQL report with the v1 fields and two
additional ordered metrics. The metrics use a bytes unit and the
`shared_sample_delta` basis.

| Metric | Interval |
| --- | --- |
| `staging_wal_position_delta` | Cluster WAL insert-position change across the staged writer |
| `staging_database_temporary_bytes_delta` | Current database cumulative temporary-byte change across the staged writer, when its reset identity is continuous |

The observation is per call. A comparable flat counter is measured zero.
Missing or discontinuous pairs report `not_observed`; repeated events
report `ambiguous_repeat`. Raw snapshots, SQL, reset identities and paths
stay private. The report retains the existing canonical file bound,
new-only delivery and offline check/show commands. It conveys shared
counter samples. Statement attribution, physical device I/O and
operation-wide RSS use separate evidence.

The CLI admits schema 3 only for direct Server refresh. A SQLite Local
home, coordinator use, a v2 sealing-budget option or an explicit staging
event transport refuses before subject work. Existing callers using schemas
1 and 2 retain their report contracts. Ordinary refresh creates no new
sampler. Delivery failure leaves the completed subject result intact.

## Acceptance

Exercise the actual disposable PostgreSQL CLI boundary and persisted
publication result, including a counter reset and a sampled failure.
Verify closed detached decoding, scope refusals and duplicate-event
projection with focused tests. The product adds no runtime dependency.
