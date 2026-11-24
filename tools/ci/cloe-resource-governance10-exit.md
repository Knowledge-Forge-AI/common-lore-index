# REPOMAP-CLOE-RESOURCE-GOVERNANCE10 Prepared Stage COPY Exit

[ADR 0214](../../docs/adr/2026/11/0214-prepared-stage-copy-custody.md)
binds staged COPY to the count already established during preparation.
Both measured and ordinary ingestion enforce one family's row count before
opening a later family or running statistics. If the stream contains an
excess row, COPY receives only the declared prefix. A short stream fails
at EOF. The transaction owner rolls back and marks the loading attempt
failed. The prior accepted publication remains available.

The staged caller explicitly closes the bounded view and its owned spool
reader before removing the backing path. A COPY error remains primary if
local reader settlement also fails. Generic COPY and transaction ownership
stay with their existing callers. Successful ingestion still requires
database-side completeness validation.

## Evidence

Using an in-process COPY sink, an overlong family declared one row and
previously wrote three, then proceeded through all seven families. It now
writes one and refuses inside the first COPY session. A short family also
refuses in the first session. A deliberately retained reader previously
stayed open after the spool path disappeared. On the corrected path the
reader closed while the original error remained referenced. These are
logical transfer and custody witnesses.

The scoped unit run passed 42 cases with `--suite unit --no-coverage --jobs 1`.

```text
src/test/unit/python/repomap_kg/storage/scale2_staging_copy.unit.test.py
src/test/unit/python/repomap_kg/storage/arch6e_spool_transfer_lifetime.unit.test.py
src/test/unit/python/repomap_kg/storage/staged_refresh_custody.unit.test.py
```

Disposable PostgreSQL integration passed eight cases with
`--suite int --no-coverage --jobs 1 --pg-container-port 55433`.

```text
src/test/int/python/repomap_kg/storage/slice19_copy_recovery.int.test.py
src/test/int/python/repomap_kg/storage/staged_maintenance_recovery.int.test.py
```

The short and overlong spools reached real COPY. Both rolled back before
statistics or prepared-stage commit, preserved the exact accepted receipt
and allowed a fresh attempt. Scoped typing and full retention enforcement
passed. Independent design and resulting-source reviews accepted this
scope. The fixed containerized test harness reported zero owned residue.
The mismatch tests used ordinary COPY. The measured path uses the same
count guard and reader custody ahead of its existing timing scopes.

## Campaign state

The operator's AVAILABLE setting remains in effect. This is a local
checkpoint. No hosted run or automatic lane was triggered. CI156
qualifies its own exact public staging head. Complete local unit,
integration, smoke, staging, system and combined suites were unselected.
The logical COPY and rollback proof leaves physical PostgreSQL I/O,
hard RSS limits and whole-operation acceptance open.
