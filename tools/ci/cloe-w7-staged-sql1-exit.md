# CLOE-W7-STAGED-SQL1 Staged PostgreSQL Measurement Exit

The disposable PostgreSQL 16.14 fixtures executed the maintained canonical
edge-evidence INSERT and every staged completeness count. Both used
`EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)` and the plan root's inclusive
counters. An independent connection sampled the cluster WAL insert position
and current database's cumulative temporary bytes immediately around each
statement. These counters have different owners and time intervals. Planning
buffers are reported separately, and AFTER triggers run after the plan has
completed. Root totals cover plan execution. The difference
between root WAL and the shared position delta remains unattributed.
[PostgreSQL 16 plan and trigger accounting](https://www.postgresql.org/docs/16/using-explain.html)
· [PostgreSQL 16 EXPLAIN](https://www.postgresql.org/docs/16/sql-explain.html)
· [PostgreSQL 16 statistics](https://www.postgresql.org/docs/16/monitoring-stats.html)

## Edge-evidence merge

Both generated fixtures used the existing refresh publication role and the
same INSERT template after the actual canonical merge preamble prepared and
analyzed the temporary maps. Each had one more node than the number of edges,
with one evidence observation and node-evidence link per edge. Parallel query
workers were disabled in the diagnostic transaction.

| Fixture | `work_mem` | Staged links | Committed links | Temp blocks read / written | WAL records / bytes | Shared WAL position delta (B) | Database temp delta (B) |
|---|---:|---:|---:|---:|---:|---:|---:|
| In-memory | 4 MB | 100 | 50 | 0 / 0 | 202 / 14,930 | 17,864 | 0 |
| Spill | 64 kB | 8,192 | 4,096 | 513 / 603 | 16,432 / 1,364,312 | 1,602,584 | 0 |

The small fixture had 258 shared buffer hits, five dirtied and three written
shared blocks, and two local hits. The spill fixture had 32,474 shared hits,
87 dirtied and 88 written shared blocks, and 160 local hits. Both recorded
zero shared reads, zero other local block counters and zero WAL full-page images.

In both cases the INSERT created the expected rows when explained, the
savepoint rollback returned the target to zero rows, and the ordinary INSERT
committed the exact same row identities. An independent reader verified that
committed state. The analyzed temporary maps remained in the plan without
repeated scans of the two permanent target tables. Both runs used synthetic
stage `stage-explain`, repository ID 1 and run ID 1. The exact INSERT
SHA-256 was `8375f247473b5ee8f01a455d2a3f56368d14fd70bb718ecb420a408bf4f839a6`.

## Completeness checks

The seven real count templates ran with the same bound parameter
`stage-validation-explain` as ordinary validation, in a read-only
transaction using the fixture's database owner. The observed and ordinary
invocations both passed all counts. Plan differences between roles were
outside this comparison.
Every plan recorded zero WAL, temporary blocks and local blocks. The shared
database temporary-byte delta was zero in all seven windows.

| Family | Expected rows | Shared buffer hits | Shared WAL position delta (B) | Statement template SHA-256 |
|---|---:|---:|---:|---|
| Files | 0 | 2 | 72 | `a2b7b6baa593d0ab69b7bf3329c3fe8f0646e27a97f955a803628b48bb3faf6c` |
| Raw observations | 0 | 2 | 136 | `15efa2456f925def796528ff7465543f3026447f6d5102be84ceda72b4c31d8f` |
| Canonical nodes | 51 | 2 | 0 | `6a80f3f5724940a4bba5951b5d483bab402dd5b5b997a96930dc0fe6d3640eae` |
| Canonical edges | 50 | 4 | 0 | `8c0e005fd3e1d17df8df819c53ab433f2ed9cb2a71f5f929bdd76c2e9d7be21b` |
| Canonical evidence | 50 | 2 | 0 | `da8500f6a5e958f4871d8fcaaf1c079089b17e95a4f7e4f2a3fc1f2c530a8daa` |
| Canonical node evidence | 50 | 2 | 0 | `eb4a269c0c1c78c58bef9dfdf0f599c4099faa218ecb5e0a80488912e31cdc33` |
| Canonical edge evidence | 100 | 4 | 0 | `0f0aa569855785899c137aea595da6dd7a84c6747f081c17b89ce7b4c5bbac6f` |

The spill and read-only cases demonstrate why shared counter movement cannot
be attributed to the statements. COPY prepared the fixture before
measurement. Statement-plan attribution covers the selected INSERT and
count statements. Physical device I/O and operation-wide RSS remain open.
This phase leaves the product's default and version 2 reports intact.

## Verification

`python3 tools/run_tests.py --suite int --no-coverage --jobs 1
--pg-container-port 55433 -- -rP` with these scoped owners passed 15 cases,
including the original rollback and recovery cases. Terminal custody passed.

```text
src/test/int/python/repomap_kg/storage/scale4_canonical_staging_merge.int.test.py
src/test/int/python/repomap_kg/storage/slice19_copy_recovery.int.test.py
```

The initial pytest run passed but its enclosing runner failed the host
cleanup census because the concurrent retention checker still had a running
container. The container identity was verified and Docker removed it on
exit. The accepted integration run and Linux retention enforcement both
passed when run sequentially.
Scoped Ruff, mypy, and tracked file-length checks passed on the three
modified Python files. Unit, complete integration, smoke, staging, system,
compileall, and local coverage were unselected for this diagnostic phase.
Public staging remains on the [CI158 qualification](cloe-ci158-qualification.md).
