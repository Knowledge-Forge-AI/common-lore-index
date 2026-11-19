# REPOMAP-CLOE-RESOURCE-GOVERNANCE12 Connected W7 Evidence Exit

## PostgreSQL statement and sampled counters

Two scoped executions used a disposable PostgreSQL 16.14 fixture with
16,384 generated rows. An instrumented insert attempted another 16,384
rows inside a rolled-back transaction. The subsequent row count was
16,384. A serial sort used a transaction-local `work_mem` of 64 KiB.
The probe sampled the existing staging observer across the write, across
the sort and again immediately after the worker connection closed.
Both sort deltas start at the sample just after the rolled-back insert.

| Observation | Single diagnostic | Five-owner integration run |
| --- | ---: | ---: |
| Insert plan WAL records | 16,384 | 16,384 |
| Insert plan WAL bytes | 1,507,328 | 1,507,328 |
| Sampled WAL position delta across the rolled-back insert, bytes | 1,577,512 | 1,577,512 |
| Sort plan temporary read blocks | 154 | 154 |
| Sort plan temporary written blocks | 172 | 172 |
| Sampled temporary-byte delta immediately after the sort | 0 | 0 |
| Sampled temporary-byte delta through connection close | 0 | 647,168 |

The statement counters describe the instrumented execution. The shared
sample describes the view and WAL position at two read times. The
observed zero temporary-byte delta occurred with temporary work reported
by the plan. Even the sample after connection close varied across the two
runs. The tests allow that timing variation. These are measurements of
this generated workload. Physical I/O, hard RSS and whole-operation
resource attribution remain open.
[PostgreSQL 16 EXPLAIN](https://www.postgresql.org/docs/16/sql-explain.html) ·
[PostgreSQL 16 monitoring statistics](https://www.postgresql.org/docs/16/monitoring-stats.html)

## Connected fault custody

- An ordinary clock exception at Server observer completion degrades the
  report while the real PostgreSQL publication succeeds. The accepted run
  matches the returned committed run. Existing process-control exceptions
  still propagate and retain the committed record.
- A source reader now exercises a partial real transfer before a later
  injected read failure. The original error remains the cause, the exact
  descriptor is closed, the temporary object is removed and the incumbent
  is still readable.
- A verifying reader encounters an injected descriptor stat failure at EOF
  after reading the accepted bytes. It preserves the failure cause and
  closes the real descriptor. The same reference is independently readable.
- Metered collision publication exercises read plus close failure and
  close-only failure on real descriptors. Both comparison readers close,
  the temporary object is removed and the accepted object and reference
  survive. A fresh collision succeeds once the injected fault is removed.

The [CI156 result](cloe-ci156-qualification.md) recorded 20,281 of 25,308
integration branches and a 472-branch gap to 82%. Its authenticated
missing-branch inventory guided the observer, source-reader, verifying-stream
and metered-store selection. The scoped runs used no coverage. Hosted
measurement is needed for any successor margin.

## Verification

The initial diagnostic passed one case using
`--suite int --no-coverage --jobs 1 --pg-container-port 55433` and
`src/test/int/python/repomap_kg/storage/arch2c_staging_observability.int.test.py::test_staging_samples_and_statement_counters_on_disposable_workload`
with `-s` to retain the numeric observations.

Each five-owner run passed 34 integration cases with
`--suite int --no-coverage --jobs 1 --pg-container-port 55433` and `-s`.

```text
src/test/int/python/repomap_kg/storage/arch2c_staging_observability.int.test.py
src/test/int/python/repomap_kg/ops/resource_view_fault_custody.int.test.py
src/test/int/python/repomap_kg/artifacts/metered_store_custody.int.test.py
src/test/int/python/repomap_kg/artifacts/navigation33_store_stream_and_sealing.int.test.py
src/test/int/python/repomap_kg/artifacts/sealing_refusal_contracts.int.test.py
```

The existing adjacent unit contracts passed 95 cases with
`--suite unit --no-coverage --jobs 1` and the approved command-local
W7 capacity.

```text
src/test/unit/python/repomap_kg/storage/arch2c_staging_observability.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_view_observer.unit.test.py
src/test/unit/python/repomap_kg/artifacts/metered_store_custody.unit.test.py
src/test/unit/python/repomap_kg/artifacts/store_verify_custody.unit.test.py
```

Independent review accepted the first test set and the evidence. The
review noted that the collision fault was keyed to reader open order.
The test now locates the readers by file role and proves one close for
each after an actual temporary-file read. The parent verified that
correction and repeated the entire scoped integration selection. The
table uses the observations from the initial diagnostic and first
five-owner run.

Scoped Ruff and mypy, tracked file length, and full Linux retention
enforcement passed. The containerized runner reported zero run-owned
ephemeral image or volume residue. The source change is limited to
integration tests and this evidence record.

Hosted capacity remains AVAILABLE. Public staging still points to
`2572d48` and qualifies through CI156. This local checkpoint triggered
zero hosted attempts. Complete local unit, integration, smoke, staging,
system and combined suites and local coverage were unselected.
