# CLOE-STAGING-SPLIT-W7-REPORT1 Product and Public Staging Exit

[CI159 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44540555733)
passed eight jobs. The monolithic staging integration job was canceled at
its 240-minute job envelope. The exact native/system and Rust artifacts
prove 66 cumulative application starts, 38 Rust qualifier entries, 74
wheel transfers and 36 component executions. Staging qualification remains
incomplete for that attempt.

[ADR0216](../../docs/adr/2026/11/0216-hosted-staging-integration-parts.md)
retains a single smoke admission followed by three parallel measured jobs
and one concurrent abrupt job. The stable required check verifies
same-attempt membership, cleanup, full-population and measurement closure,
then applies the original Python and Go coverage floors. The configuration
keeps the previous 240-minute envelope on each integration part. Replaying
the split assignment against CI158 body durations placed 46.6, 54.1 and
41.6 minutes on the three measured parts. Hosted elapsed time also includes
setup and cleanup, so the upcoming attempt is the timing proof.

[ADR0217](../../docs/adr/2026/11/0217-server-staged-resource-report-v3.md)
ships optional Server report schema 3. Direct PostgreSQL refresh can record
staged-writer shared WAL insert-position movement and current-database
temporary-byte movement. Disposable PostgreSQL exercises ordinary samples,
real counter reset, query failure, canonical offline readback and persisted
publication. Samples may be unavailable, and a flat observation reports
zero. Attribution remains shared and bounded to the writer interval.

The [CI160 allocation](cloe-ci160-allocation.json) reserves 67 application
starts, 39 Rust qualifier entries, 76 wheel transfers and 37 component
executions as cumulative maxima for one synchronize on draft PR 27.
The new grant and proof refuse CI159's consumed nonce and reservation.
The application context hash changes with the product source. Build recipe,
Dockerfile, context membership and SDK recipe retain their existing pins.

## Scoped verification

The following unit owners passed 346 cases and skipped 35 preexisting
public-projection exclusions under `--suite unit --no-coverage --jobs 1`.

```text
src/test/unit/python/tools/ci/hosted_staging.unit.test.py
src/test/unit/python/tools/ci/pipe0_topology_contract.unit.test.py
src/test/unit/python/tools/ci/pipe0_topology_gates.unit.test.py
src/test/unit/python/tools/ci/qualification_command_contracts.unit.test.py
src/test/unit/python/tools/ci/workflow_model.unit.test.py
src/test/unit/python/tools/staging_report_contract.unit.test.py
src/test/unit/python/tools/staging_report_presentation.unit.test.py
src/test/unit/python/tools/runner_failure_recovery.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_report_v2_codec.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_report_codec.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_report_cli.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_view_contracts.unit.test.py
```

The disposable integration run passed 45 cases under
`--suite int --no-coverage --jobs 1 --pg-container-port 55433` after
correcting an overlapping reason-table name that had affected earlier
report versions. Terminal resource custody passed.

```text
src/test/int/python/repomap_kg/ops/resource_report_v2_server_cli.int.test.py
src/test/int/python/repomap_kg/ops/resource_report_offline_cli.int.test.py
src/test/int/python/repomap_kg/ops/resource_report_local_cli.int.test.py
```

The final campaign binding passed 36 scoped unit cases and 4 scoped
containerized integration cases under those same flags.

```text
src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py
src/test/unit/python/tools/ci/d12_system_grant.unit.test.py
src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py
src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py
src/test/unit/python/tools/system/d12_system_evidence.unit.test.py
src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py
```

The complete local suites and local coverage were unselected. The
first split campaign measures the combined hosted coverage and timing.
The final frozen source passed the union of those exact selectors
with 382 unit passes, 35 preexisting skips and 49 integration passes.
