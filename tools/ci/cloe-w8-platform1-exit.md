# CLOE-W8-PLATFORM1 Local Platform Preparation Exit

[CI162 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44674001015)
passed all 14 jobs for staging `d5ab23730ae306a1c259c45af3f59b18bf492725`.
The retained staging aggregate matched the artifact digest and bound merge
`2f3ec764f4d97a3f8a4b68db593f8e8f7ddaf1ba` with source digest
`e2b0cc27cdefbec8b22a6562c12337f58c505bef78cf6117a935f3d78bc2bfb8`.
It accepted 2,651 measured tests, 12 measured skips and five abrupt cases.
Measured Python integration covered 69,952 of 78,399 lines and 20,328 of
25,362 branches. Both hard floors passed. The 82% target needs 469 more
covered branches at that denominator. CI162's successor checkpoint is local.

The offline operator kit adapts the corrected historical LOCAL11 runner
to the current CLOE entry point. It binds the `d5ab2373` package-input census
(872 files) and the exact dependency wheel from the lock. The network-disabled
Linux arm64 control used a fresh venv and only the kit. It published a SQLite
graph, backed it up, restored it with an equal database hash, and obtained
matching MCP results with the source hidden. The PostgreSQL check returned
the missing-driver refusal. The kit removed its owned workspace. The native
Apple Silicon entry is separate and requires attended execution and review.

The installed-candidate test helper now keys archived wheels by checkout,
commit, output prefix and archive path list. The regression reproduced
reuse across different requests at the same commit and verifies both changed
prefix and changed inputs alongside a repeat of the exact request.

## Scoped proof

`tools/run_tests.py --suite unit --no-coverage --jobs 1 --` passed 24 cases in
`src/test/unit/python/repomap_kg/runtime/installed_candidate.unit.test.py`.

`tools/run_tests.py --suite int --no-coverage --jobs 1 --pg-container-port 55433 --`
passed four cases and reported zero terminal owned residue:

```text
src/test/int/python/repomap_kg/runtime/cloe_installed_local_upgrade.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_migration.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_server.int.test.py
```

Complete local unit, integration, smoke, staging, and system suites and local
coverage were unselected. This checkpoint adds no publication or workflow
trigger. Package-index delivery and native release qualification remain open.
