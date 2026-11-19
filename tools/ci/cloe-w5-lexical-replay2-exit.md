# CLOE-W5-LEXICAL-REPLAY2 Exit

[ADR 0213](../../docs/adr/2026/11/0213-proposed-w5-lexical-replay.md)
selected the [secondary lexical replay](../../docs/specs/cloe-w5-lexical-replay-v1.md)
over the 24 exposed body-v2 cases. Both arms used one owned PostgreSQL lexical
generation and the same policy, scope, ranking function, and timeout. The paired driver
freezes the requests, records each slot before sending it to the service,
and reconciles the generation and file census at exit.

The native terminal disposition was `complete_descriptive`. The journal recorded
48 started slots, 48 observed slots, and
44 service calls. The owned Linux
ARM64 SDK, PostgreSQL container, and Unix socket cleanup were verified.

| Body-positive mean | Legacy | Candidate |
| --- | ---: | ---: |
| nDCG@10 | 0.0000 | 0.6925 |
| MRR@10 | 0.0000 | 0.6208 |
| Recall@10 | 0.0000 | 0.9167 |

The legacy and candidate arms answered 2 and 2 of the two absence controls correctly.

ADR 0206's calibration remains the primary observation. This replay provides
secondary descriptive evidence. Scientific acceptance, held-outs, production
admission, and public CI remain separate.

## Verification

- Unit group passed 30 cases with `--suite unit --no-coverage --jobs 1` and
  these exact files under `src/test/unit/python/tools/`:
  `retrieval_lexical_replay.unit.test.py`,
  `runner_lexical_replay.unit.test.py`,
  and `retrieval_body_study_pg_lifecycle.unit.test.py`.
- Containerized integration passed `--suite int --no-coverage --jobs 1
  --pg-container-port 55433 -- src/test/int/python/tools/retrieval_lexical_replay.int.test.py`.
  The unrelated fixture exercised all six case types, stale and missing
  generation evidence, interruption, timeout, and owned cleanup.
- Scoped typing, Ruff, tracked-file length, inventory closure, and all Linux
  retention profiles passed. The independent corrective source and offline
  preflight review accepted the frozen candidate.
- The evaluated launcher used cached exact artifacts and a fresh offline
  sandbox. The synthetic integration used the established managed test
  runtime in its separate sandbox.
- Complete unit, integration, smoke, staging, system, and combined suites
  were unselected. Hosted publication and triggers were unselected.
