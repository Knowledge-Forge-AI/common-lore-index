# CLOE-CI157REPAIR1 CI158 Candidate Exit

[CI157 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44451624265)
executed at staging `c8bd14e4` over public main `39cb2b34`. Eight jobs
passed. The unit population had one failed assertion, 20,758 passes and
40 skips. A second publication-identity test still treated the newly valid
maximum of 64 as a rejected value. That failure reproduced in isolation.
The test now verifies both sides of the accepted cap relative to its
independently accepted active grant. It passes at the CI157 and CI158
capacities and checks spent identity refusal across both grant and proof.

Same-attempt staging M/A, eight host bundle checks, system export, native
AMD64 and Rust evidence validated against CI157. Integration measured
69,841 of 78,275 lines and 20,295 of 25,320 branches (80.15%). This clears
both hard integration floors and leaves 468 branches to the desired 82%
margin. CI157 did not publish a unit coverage qualification after pytest
failed.

## Campaign binding

The successful system and Rust reports establish prior cumulative application,
Rust qualifier, wheel transfer and component execution totals of 64/36/70/34.
The [CI158 allocation](cloe-ci158-allocation.json) binds those artifacts and
reserves 65/37/72/35 for one attempt under
[ADR 0200](../../docs/adr/2026/11/0200-standing-execution-allocation-and-initial-milestone-ci.md).
The consumed CI157 nonce and reservation are refused by both validators.
The existing D12 build context and SDK recipe verify byte for byte.
The Rust manifest binds the current source and its recorded checkpoint.

The replacement campaign uses a single public staging fast-forward to
synchronize draft PR 27. Hosted capacity remains AVAILABLE. At this
commit, independent review and fresh event binding precede publication.

## Scoped verification

The failing node reproduced under CI157 and passed after the relative
boundary correction, before any campaign metadata changed. The successor
passed 36 cases with `--suite unit --no-coverage --jobs 1` and these owners.

```text
src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py
src/test/unit/python/tools/ci/d12_system_grant.unit.test.py
src/test/unit/python/tools/ci/d12_candidate_context.unit.test.py
src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py
src/test/unit/python/tools/system/d12_system_evidence.unit.test.py
```

Four integration cases passed with `--suite int --no-coverage --jobs 1
--pg-container-port 55433` and this owner.

```text
src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py
```

Complete local suites and local coverage were unselected.
