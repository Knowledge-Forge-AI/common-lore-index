# CLOE-W7-ATTRIBUTION1 Public Staging Candidate Exit

This candidate combines the [publication custody fix](cloe-resource-governance13-exit.md)
and the [staged SQL measurements](cloe-w7-staged-sql1-exit.md). The former
preserves the committed PostgreSQL outcome when client RSS sampling fails.
The latter verifies rollback and ordinary commit for the real edge-evidence
INSERT and a seven-query read-only completeness comparison. The spill
fixture wrote 603 temporary blocks while the sampled database temporary-byte
delta was zero. Statement-root and shared-interval measurements serve
different purposes. Neither supports physical device I/O or whole-operation
RSS claims.

[CI158](cloe-ci158-qualification.md) qualified the preceding public staging
commit with 87.06% unit and 80.15% integration branch coverage. This candidate
has its own pending hosted qualification. The 82% integration branch margin
remains a target.

The [CI159 allocation](cloe-ci159-allocation.json) uses ADR 0200 to bind one
automatic synchronize event on draft PR 27 after one fast-forward of public
staging. The prior successful attempt spent 65 application starts, 37 Rust
qualifier entries, 72 wheel transfers and 35 component executions. The
successor reservations are 66, 38, 74 and 36. Both grant and admission-proof
validation refuse the spent CI158 identity. The context rebind covers the
changed application source. The context file count, build recipe, Dockerfile
and SDK recipe are byte-identical. The Rust manifest derives its source
checkpoint from this commit and retains the existing parser and wheel pins.

## Scoped verification

The exact unit owners passed 80 cases with
`tools/run_tests.py --suite unit --no-coverage --jobs 1 --`.

```text
src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py
src/test/unit/python/tools/ci/d12_system_grant.unit.test.py
src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py
src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py
src/test/unit/python/tools/system/d12_system_evidence.unit.test.py
src/test/unit/python/repomap_kg/storage/arch2c_staging_observability.unit.test.py
src/test/unit/python/repomap_kg/ops/resource_view_projections.unit.test.py
```

The exact integration owners passed 31 cases with
`tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port 55433 --`
in the disposable container harness. The terminal check reported zero owned
ephemeral image or volume residue.

```text
src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py
src/test/int/python/repomap_kg/storage/arch2c_staging_observability.int.test.py
src/test/int/python/repomap_kg/ops/resource_view_fault_custody.int.test.py
src/test/int/python/repomap_kg/storage/scale4_canonical_staging_merge.int.test.py
src/test/int/python/repomap_kg/storage/slice19_copy_recovery.int.test.py
```

The complete local suites, compileall, staging, system and local coverage
were unselected. The published release on main remains independent of this
staging candidate. Public versions 1 and 2 retain their report contracts.
