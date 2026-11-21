# CLOE CI163 repair Exit

CI163 executed the hosted workflow and failed in the unit, smoke and D12
lanes. The lexical unit doubles now handle bounded PostgreSQL batch
execution so the existing tests reach publication and the commit fence.
Hosted unit line and branch coverage were 92.7% and 87.1%.

The native and system D12 jobs stopped before an application build.
The official PGDG archive and dated Debian sources retain all 48 exact
frozen package identities. The candidate selects those identities using
the existing trust roots and verifies each archive before offline install.
The Trixie smoke dependency image selects glibc and OpenSSL headers that
match the held runtime. The recipe and acquisition change are recorded in
[ADR 0219](../../docs/adr/2026/11/0219-retain-frozen-trixie-package-acquisition.md).

## Scoped proof

The canonical runner used one worker with Python coverage disabled.
The unit selection passed 277 cases.

- `src/test/unit/python/tools/d12_builder_headers.unit.test.py`
- `src/test/unit/python/tools/d12_inner_preflight.unit.test.py`
- `src/test/unit/python/tools/d12_libpq_runtime_probe.unit.test.py`
- `src/test/unit/python/repomap_kg/runtime/release_dockerfile_boundary.unit.test.py`
- `src/test/unit/python/repomap_kg/runtime/d12_release_manifest.unit.test.py`
- `src/test/unit/python/repomap_test_support/resource_test_images.unit.test.py`
- `src/test/unit/python/tools/ci/d12_system_grant.unit.test.py`
- `src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py`
- `src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py`
- `src/test/unit/python/repomap_kg/retrieval/lexical_storage_and_navigation.unit.test.py`
- `src/test/unit/python/repomap_kg/storage/lexical_lifecycle_boundaries.unit.test.py`
- `src/test/unit/python/repomap_kg/storage/lexical_build_lease_boundaries.unit.test.py`
- `src/test/unit/python/tools/ci/d12_hosted_qualification.unit.test.py`
- `src/test/unit/python/tools/ci/d12_hosted_admission.unit.test.py`
- `src/test/unit/python/tools/ci/d12_hosted_reports.unit.test.py`

The sandboxed integration selection passed 8 cases on the
established pinned ARM diagnostic image with zero builds, pulls and image
or volume residue.

- `src/test/int/python/repomap_kg/runtime/d12_package_preflight.int.test.py`
- `src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py::test_system_hold_proof_capture_and_child_secure_writer`
- `src/test/int/python/repomap_kg/storage/lexical_build_lease_boundaries.int.test.py`

A disposable native ARM APT simulation resolved 47 smoke prerequisites
against trusted indexes without package installation. The native AMD64
package proof used signed index identities. Hosted CI owns AMD64
execution and full-population coverage. Complete local unit, integration,
smoke, staging, system and combined suites were unselected.

## Hosted qualification

The [CI164 allocation](cloe-ci164-allocation.json) binds one automatic
attempt to the existing public staging pull request. CI163 consumed
one Rust qualifier entry before prerequisite refusal. It consumed zero
application builds, wheel transfers and component runtime executions.
The one-use CI163 grant is retired.
