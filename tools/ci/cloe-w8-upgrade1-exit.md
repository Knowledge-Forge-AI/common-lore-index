# CLOE-W8-UPGRADE1 Installed Upgrade Proof Exit

The exact v0.0.4 release commit and an independent archive path list supply
the predecessor package inputs.
The existing offline wheel builder assembles separate installed environments
for the predecessor and the current source. A changed package member binds
prepared source, wheel and installed bytes. The wheel and installed metadata
are checked. The file digest distinguishes the environments even though their
versions match.

Both Local environments use the same test-authored configuration. The
released environment publishes generation 1 and writes the
pre-upgrade backup. The candidate reads that publication while the original
material is hidden. The stored database and configuration bytes are
unchanged during this readback. An edited source then produces generation 2.
The two installed versions independently restore the original backup into
separate empty homes and return the original navigation answer without source.
The proof covers recovery from a pre-upgrade bundle.

The provisioned Server test publishes generation 1 with the released
environment and reads it from the candidate using the same disposable database
with source hidden. Reapplying the existing migration catalog and refreshing
after an edit advances that database to generation 2. The installed candidate
then locates the new symbol through MCP. Both environments use the SDK's
dynamic Psycopg and libpq.

Both cases use the existing Linux sandbox and maintained public tiny fixture.
The [CI161 qualification](cloe-ci161-qualification.md) records the latest hosted
candidate. This checkpoint is local for the next larger W8 batch. Package
distribution, native platform qualification and public release have their own
evidence boundaries.

## Scoped verification

`tools/run_tests.py --suite unit --no-coverage --jobs 1 --` passed 56
cases with the following selectors. The documented scratch selector pointed
the host unit run at the phase-owned directory within shared scratch after
the default root refused admission. The default thresholds still applied.

```text
src/test/unit/python/repomap_kg/runtime/installed_candidate.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_environment.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_projection.unit.test.py
src/test/unit/python/repomap_kg/runtime/installed_origin.unit.test.py
```

`tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port
55433 -- -rP` passed five cases with the following selectors and clean
terminal resource custody.

```text
src/test/int/python/repomap_kg/runtime/cloe_installed_local_upgrade.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_local.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_migration.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_server.int.test.py
```

The Linux retention checker and tracked Python file-length check passed.
Complete local suites and local coverage were unselected. Git diff and
staged diff checks passed.
