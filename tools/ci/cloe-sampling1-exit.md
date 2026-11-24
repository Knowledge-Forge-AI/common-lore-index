# CLOE-SAMPLING1 Optional Server Sampling Exit

An exception from the optional staged resource sampler could prevent the
Server refresh before work. After PostgreSQL committed, the same class of
exception could relabel a completed publication as `commit_unknown`.
The CLI regressions reproduced those outcomes for the initial sample,
the final sample and an exception after emitting a partial counter.

The staged writer now contains ordinary sampling exceptions at the
optional measurement boundary. It marks the measurement lane failed,
keeps the real commit outcome and lets lifecycle events continue.
The v3 report uses the existing `observer_failed` reason on its two
shared-counter rows, including when one sample was emitted before the
failure. Process-control exceptions follow their existing handling.

## Scoped verification

- `tools/run_tests.py --suite unit --no-coverage --jobs 1 -- src/test/unit/python/repomap_kg/storage/arch2c_staging_observability.unit.test.py`
  passed 39 cases.
- `tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port 55433 --`
  passed 16 cases in the two owners below. All three new CLI cases confirmed
  the accepted run from a separate PostgreSQL read and checked redaction.

```text
src/test/int/python/repomap_kg/ops/resource_report_v2_server_cli.int.test.py
src/test/int/python/repomap_kg/ops/resource_view_server.int.test.py
```

The container runner reported clean terminal custody. Complete suites,
smoke, staging, system and coverage were unselected. This checkpoint adds
no remote write or workflow trigger.
