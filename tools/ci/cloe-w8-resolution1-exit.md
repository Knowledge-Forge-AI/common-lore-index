# CLOE-W8-RESOLUTION1 Local Dependency and Public Staging Exit

This batch combines [v0.0.4 upgrade compatibility](cloe-w8-upgrade1-exit.md),
[offline source archive equivalence](cloe-w8-sdist1-exit.md), and a fresh pip
resolver proof for the existing Local dependency. The diagnostic rebuilt
both wheels from the same 872 project inputs
in the existing network-disabled Linux arm64 test SDK.

The one-file offline wheel directory held the exact `typing-extensions`
4.16.0 wheel. Its SHA-256 and 45,571 bytes matched the maintained
lock. Each fresh environment initially had no installed distributions.
Pip's installation report identified that wheel as a dependency of the
requested product. Both installs passed `pip check`, isolated origin
checks, and `cloe --version`. Each published a SQLite graph and returned
the reviewed MCP answer with the fixture material hidden. The empty
directory case refused dependency resolution before installing anything.

The [CI162 allocation](cloe-ci162-allocation.json) reserves one PR 27
staging synchronize. The carried CI161 totals are 68 application starts,
40 Rust qualifier entries, 78 wheel transfers and 38 component runs.
The new reservation caps those totals at 69, 41, 80 and 39. The exact
resolver proof is Linux arm64 diagnostic evidence. The combined hosted
candidate will test the maintained integration owners, including the
upgrade and source archive cases, under the existing gate.

## Scoped verification

`tools/run_tests.py --suite unit --no-coverage --jobs 1 --` passed
101 scoped cases:

```text
src/test/unit/python/repomap_kg/runtime/installed_candidate.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_environment.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_projection.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_origin.unit.test.py
src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py
src/test/unit/python/tools/ci/d12_system_grant.unit.test.py
src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py
src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py
src/test/unit/python/tools/system/d12_system_evidence.unit.test.py
```

`tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port
55433 -- -rP` passed 12 cases with clean terminal resource custody:

```text
src/test/int/python/repomap_kg/runtime/arch7f_release_wheel.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_local_upgrade.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_local.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_migration.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_server.int.test.py
src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py
```

Complete local suites and local coverage were unselected. The package-index
publication path and native platform qualification remain distinct roadmap
gates. [CI161](cloe-ci161-qualification.md) qualified the preceding public
staging source. The 82% integration branch target remains open.
