# CLOE-CI164-LOCALREAD1 Exit

[CI164 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44948857438)
failed one release-recipe unit test. The other 13 jobs passed, including the
native AMD, system and split staging integration gates. The unit assertion now
checks that strict checksum verification precedes archive configuration and
package inventory. The release recipe and package pins are unchanged.

The Local readback batch reduces work that scales with returned and probe
rows. Node and file search render only the requested page. Context expansion
combines both bounded incident-edge routes in SQLite's byte order and
normalizes only the selected edges. Node locations select the accepted run or
the latest available run with a scalar aggregate and read current and
historical evidence one window at a time. The existing offsets, truncation,
role filters, snapshot identities and read-only MCP results are preserved.

## Scoped verification

Canonical unit selectors (`tools/run_tests.py --suite unit --no-coverage --`):

- `src/test/unit/python/repomap_kg/runtime/arch7f_release_resources.unit.test.py`
- `src/test/unit/python/repomap_kg/storage/context_expansion_queries.unit.test.py`
- `src/test/unit/python/repomap_kg/storage/sqlite_local_queries.unit.test.py`
- `src/test/unit/python/repomap_kg/mcp_server/node_locations.unit.test.py`
- `src/test/unit/python/tools/ci/d12_system_grant.unit.test.py`
- `src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py`

Canonical integration selectors (`tools/run_tests.py --suite int --sandbox
--jobs 1 --no-coverage --` with the existing exact dependency-cache override):

- `src/test/int/python/repomap_kg/storage/sqlite_local_parity.int.test.py::test_same_validated_input_reads_identically_from_sqlite_and_postgresql`
- `src/test/int/python/repomap_kg/retrieval/context_sqlite_publication.int.test.py`
- `src/test/int/python/repomap_kg/storage/node_locations.int.test.py`
- `src/test/int/python/repomap_kg/storage/node_location_boundaries.int.test.py`
- `src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py`

The proof ran 115 unit and 22 containerized integration cases.
Retained Python quality, file-length and whitespace checks are required before publication. Complete
local unit, integration, smoke, staging and system suites and local Python
coverage were unselected.

## Hosted campaign

The [allocation](cloe-ci165-allocation.json) reserves one automatic campaign on the opening
of draft PR #28. Public main advanced with the project site, and the README
change is included in the projected merge's build-context digest. The topology
checker admits the exact reviewed Pages deployment. Changes to its triggers,
steps or permissions require an updated review and pin. Actual cumulative application starts,
Rust qualifier entries, pinned wheel transfers and native component executions
after CI164 were 70/43/82/40. The new ceilings are 71/44/84/41. The workflow,
pinned SDK and release recipe retain their previous identities. Hosted CI
measures the successor's complete gates and coverage.
