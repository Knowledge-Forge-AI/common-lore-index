# CLOE-CI151REPAIR1 Exit

CI151 remains EXECUTED_FAIL. Seven jobs passed, including staging integration,
assembled-product system, native AMD/Rust, source/export and security checks.
The unit population had five behavior failures (20,017 passed, 40 skipped);
pre-review found one newly added scanner suppression. Coverage passed its hard
floors: unit lines 72,606/78,241 and branches 22,029/25,308 (87.04%); integration
lines 69,780/78,241 and branches 20,272/25,308 (80.1012%). The desired integration
82% margin remains 481 branches short at that denominator, not a local coverage
measurement or a reason to hide the functional failures.

## Repairs

The copied real-pytest cleanup fixture now forwards the existing tools root
alongside source/support roots, matching the maintained conftest dependency
closure. Its four child outcomes still verify final teardown/cleanup retention.
Assertions check the child result and pointer explicitly before reading it,
so future collection failures preserve their diagnostic output.

The D12 unit's synthetic full integration-root population now includes the
registered study owner, verifies its visible deselection independently and
still refuses a missing D12 owner. Its admitted D12-only case selects the exact
D12 file, which does not falsely claim to cover the unrelated study owner.
No collection, marker, admission or runtime guard changed.

The deliberately wrong string-as-Path refusal case uses an explicit static
cast while preserving the same runtime string and refusal. The new type-ignore
was removed; scanner baselines, scanner rules and every debt ceiling stay
unchanged. The scanner reports zero added/broadened suppressions. The same
three-file group reproduced all five CI unit failures (49 passed) before the
repair and passed all 54 after it. Those failed records remain immutable.

## Verification boundary

Final canonical scoped unit command is `tools/run_tests.py --suite unit --jobs 1
--no-coverage -- -v` with the exact11 owners below; final containerized admission,
changed-source static enforcement and independent dynamic_default APGR review
are required before publication. Their exact results belong in the commit
message and exact-commit report.

- `src/test/unit/python/repomap_test_support/test_scratch_cleanup_lifecycle.unit.test.py`
- `src/test/unit/python/tools/ci/d12_publication_identity.unit.test.py`
- `src/test/unit/python/tools/ci/d12_system_grant.unit.test.py`
- `src/test/unit/python/tools/ci/d12_system_proof_host.unit.test.py`
- `src/test/unit/python/tools/ci/qualification_rust.unit.test.py`
- `src/test/unit/python/tools/ci/qualification_rust_closure.unit.test.py`
- `src/test/unit/python/tools/ci/qualification_rust_commit_binding.unit.test.py`
- `src/test/unit/python/tools/ci/qualification_rust_workflow.unit.test.py`
- `src/test/unit/python/tools/retrieval_supplement_inner_reads.unit.test.py`
- `src/test/unit/python/tools/retrieval_supplement_study_runner.unit.test.py`
- `src/test/unit/python/tools/test_runner_safety3_build_policy.unit.test.py`

The integration selector is
`src/test/int/python/repomap_kg/runtime/d12_system_admission.int.test.py`, using
the canonical isolated core harness with `--suite int --jobs 1 --no-coverage
--pg-container-port 55433`. No complete local unit, integration, smoke, staging,
system or combined suite, local coverage, new model or study was selected.
The completed prior W5 observation retains its original source/evidence hashes;
these test/CI-metadata repairs do not qualify a new scientific result.

## Hosted boundary

Hosted state AVAILABLE remains operator-set and unrescinded. The current
remediation request and ADR0200 authorize scoped repair/proof, independent
review and one newly bound staging push to existing draft PR27. Actual hosted
application/qualifier/wheel/component counters through CI151 are 58/30/60/29;
CI152 attempt1 reserves 59/31/62/30, not observed execution. The spent CI151
nonce/reservation are refused alongside all prior identities. Exact commit,
manifest, author/export, refs and next workflow ordinal must pass before the
normal synchronize event; no manual duplicate dispatch is selected.

CI151 is not relabeled as a candidate pass. The new exact-source hosted
campaign remains NOT_REQUESTED_CONSERVATION until publication. Complete hosted
qualification and the desired coverage margin remain pending for the repaired
candidate. Main/tag/release, dependency, runner-policy, task/gold and scientific
acceptance boundaries stay unchanged.
