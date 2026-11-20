# CLOE-CI160-UNIT1 Public Staging Repair Exit

[CI160 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44615515818)
recorded three failing unit assertions and 13 successful jobs. Unit coverage
cleared its hard floors at 92.8% line and 87.0% branch. The four integration
parts ran concurrently. The longest took 96 minutes and 3 seconds. The
canonical aggregate passed with 89.22% Python line, 80.15% Python branch,
and 87.90% Go statement coverage, with same-attempt cleanup and coverage
closure. Python integration branch coverage remains below the 82% target.

The corrected CLI owner verifies that refresh parses schema 3 and refuses
schema 4, while other report commands refuse schema 3. Focused dispatch
refusals already cover ineligible refresh paths. The historical monolithic
fixture still checks rejection of the missing native runner. The active
workflow test checks the three exact jobs that call the runner directly
and the single native image pin. The split gate and the Server report
implementation retain the previously measured source.

The [CI161 allocation](cloe-ci161-allocation.json) carries forward the
67 application starts, 39 Rust entries, 76 wheel transfers and 37 component
executions verified through CI160. It reserves a single draft PR 27
synchronize with cumulative bounds of 68, 40, 78 and 38. The admission
validator refuses the retired CI160 nonce and reservation. The application
context and workflow bytes match CI160. The Rust manifest binds the refreshed
grant validation sources at the successor checkpoint.

## Scoped proof

The canonical unit runner used `--suite unit --no-coverage --jobs 1`
and returned 312 passed with 39 existing projection skips for:

```text
src/test/unit/python/repomap_kg/ops/sealing_budget_cli.unit.test.py
src/test/unit/python/tools/ci/ci_topology.unit.test.py
src/test/unit/python/tools/ci/d12_native_python_job.unit.test.py
src/test/unit/python/tools/ci/hosted_staging.unit.test.py
src/test/unit/python/tools/ci/pipe0_topology_contract.unit.test.py
src/test/unit/python/tools/ci/pipe0_topology_gates.unit.test.py
src/test/unit/python/tools/ci/qualification_command_contracts.unit.test.py
src/test/unit/python/tools/ci/workflow_model.unit.test.py
src/test/unit/python/tools/ci/public_export_policy.unit.test.py
src/test/unit/python/tools/ci/d12_system_grant.unit.test.py
src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py
src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py
src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py
src/test/unit/python/tools/system/d12_system_evidence.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_report_v2_codec.unit.test.py
```

The canonical isolated integration runner used
`--suite int --no-coverage --jobs 1 --pg-container-port 55433`
with `src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py`.
All four cases passed, with clean terminal resource custody.
Full local suites and local coverage were unselected. CI161 supplies the
full-population result for this candidate.
