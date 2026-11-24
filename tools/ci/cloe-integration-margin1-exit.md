# CLOE-INTEGRATION-MARGIN1 Integration Refusal and Recovery Exit

This test-only checkpoint uses the retained CI162 measured branch map as
the baseline. CI162 measured 20,328 of 25,362 integration branches
(80.15%). The 82% target was 469 branches away at that denominator.

The new cases exercise three existing command paths.

- Configuration migration refuses portable operation aliases, mixed
  configuration families, an external hard link, excess file counts,
  an oversized file and excess combined bytes. Dry-run and confirmed
  calls leave the home and the separate file byte-for-byte intact.
- Server v3 reports preserve committed publication when either sampled
  counter moves backward. The affected WAL or temporary-byte row is
  unavailable while its independent counterpart remains measurable.
- SQLite upgrade re-verifies the backup before writing. Corruption after
  backup creation leaves the exact v1 graph available for a verified
  retry. A fault after COMMIT reconciles from the actual v2 state and a
  retry reports already current without creating a second backup.

## Scoped verification

`tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port
55433 --` passed all 34 cases in these owners:

```text
src/test/int/python/repomap_kg/cli/cloe_config_migration.int.test.py
src/test/int/python/repomap_kg/ops/resource_report_v2_server_cli.int.test.py
src/test/int/python/repomap_kg/cli/sqlite_local_migration.int.test.py
```

The terminal Docker boundary passed. The initial 30-case command saw
a test pass but a harness failure because source verification and the
Docker boundary detected concurrent phase work. The final run fixed
the source identity and ran without another phase container.

Unit, complete integration, smoke, staging, system and compileall were
unselected. The measured CI162 totals select scenarios only. The local
proof made no coverage measurement and spent no public CI.
