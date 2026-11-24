# CLOE W5 dry run and W7 Local evidence window Exit

Local navigation and compact context now sort and cap the direct-evidence
window using relational identifiers before fetching wide fields. Both read
owners keep their selected-run and direct-role rules, stable ordering,
alternative locations, and one-row overflow signal. The sprint uses the
existing Local schema and dependencies.

A synthetic SQLite probe used 4,096 reverse-ordered evidence rows and
requested 17. It returned identical rows before and after the change.
Metadata reads fell from 4,096 to 17,
and snapshot-vector reads fell from 4,096 to 17.
These are projection counts for the fixture.
Another 960 comparisons across 48 deterministic SQLite fixtures returned
identical rows across run selections, predicates, and window sizes.

The synthetic W5 qualification is recorded in
[the controller dry-run note](cloe-w5-matched-dry1-exit.md). It is independent
of held-out study execution and acceptance.

## Scoped verification

The canonical runner ran with one worker and `--no-coverage`. The unit
selection passed 134 cases.

- `src/test/unit/python/tools/retrieval_w5_matched_dry.unit.test.py`
- `src/test/unit/python/tools/retrieval_w5_matched_adapter.unit.test.py`
- `src/test/unit/python/repomap_kg/storage/context_expansion_queries.unit.test.py`
- `src/test/unit/python/repomap_kg/mcp_server/navigation_search.unit.test.py`
- `src/test/unit/python/tools/ci/d12_system_grant.unit.test.py`
- `src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py`

The sandboxed integration selection passed 16 cases with the
existing pinned ARM diagnostic. The runner reported zero builds, pulls,
and terminal image and volume residue.

- `src/test/int/python/tools/retrieval_w5_matched_dry.int.test.py`
- `src/test/int/python/repomap_kg/storage/navigation_search.int.test.py`
- `src/test/int/python/repomap_kg/retrieval/context_cli.int.test.py::test_cli_context_select_and_pack_over_sqlite_and_postgres_homes`
- `src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py::test_system_hold_proof_capture_and_child_secure_writer`

Complete local unit, integration, smoke, staging, system, and combined
runs were unselected. Hosted qualification owns the full coverage gates.

## Publication

[CI163 allocation](cloe-ci163-allocation.json) binds one automatic
qualification on the existing staging pull request to CI162 accounting,
the current application context, and the rotated one-use grant. The
hosted workflow retains its existing jobs and thresholds.
