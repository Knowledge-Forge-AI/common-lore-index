# REPOMAP-CLOE-RESOURCE-GOVERNANCE13 Staging Observer Custody Exit

## Resource observation at publication

The existing temporary-byte reset test now follows the real staged writer.
An independent connection resets statistics inside the writer's existing
pre-publication hook in a disposable PostgreSQL fixture. The writer observes
the same database identifier and a different reset timestamp in its two
samples. Its temporary-byte event projects to the existing unavailable
state with reason `not_observed`. A WAL observation remains available.
The persisted run and stage carry the returned receipt.

An OS failure from the client RSS probe could escape after PostgreSQL had
committed. The disposable Server fixture reproduced a noncommitted caller
outcome while its database readback showed a complete accepted run.
The optional sampler now converts only `OSError` from `getrusage` to
an unavailable client-memory event. The independently sampled WAL and
PostgreSQL memory values remain usable. The Server caller returns the
committed outcome, and its run ID agrees with the durable accepted state.
The synthetic error detail stays outside all serialized measurements.

The internal staging measurements still require an explicit caller.
Version 1 and version 2 operator reports retain their existing selections.
Physical I/O and operation-wide attribution remain open.

## Verification

The selected RSS unit and disposable integration regression each failed
before the fix. The red integration asserted a complete stored publication
and reached the caller-outcome assertion. The final scoped unit run
passed 44 cases with `--suite unit --no-coverage --jobs 1`.

```text
src/test/unit/python/repomap_kg/storage/arch2c_staging_observability.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_view_projections.unit.test.py
```

The final disposable integration run passed 12 cases with
`--suite int --no-coverage --jobs 1 --pg-container-port 55433`.

```text
src/test/int/python/repomap_kg/storage/arch2c_staging_observability.int.test.py
src/test/int/python/repomap_kg/ops/resource_view_fault_custody.int.test.py
```

Scoped Ruff, mypy, and full Linux retention enforcement passed. The runner
reported zero owned ephemeral image or volume residue. The complete local
suites and local coverage were unselected.

## Campaign state

Hosted capacity remains AVAILABLE. Public staging is qualified at
`c323998e` by [CI158](cloe-ci158-qualification.md).
This local correction has scoped evidence and used zero hosted attempts.
