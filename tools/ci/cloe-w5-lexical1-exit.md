# CLOE-W5-LEXICAL1 Prospective Lexical Comparator Exit

[ADR 0212](../../docs/adr/2026/11/0212-w5-prospective-lexical-comparator.md)
and the [competence contract](../../docs/specs/cloe-w5-lexical-comparator-v1.md)
define the separately named `public-lexical-comparator-v1` source phase.
The request planner and synthetic test owners are ready for the next
integrated engineering milestone.

## Observed behavior

The question route selected deterministic terms and sent a bounded
disjunction through the maintained PostgreSQL lexical service. The two
disjoint synthetic witnesses matched their respective terms. The frozen
ordinary query returned an empty available result against the same publication.
The other witnesses retained identifier alias and verbatim literal matching,
available zero candidates, ambiguity with stable locators, the ten-hit page
bound, binding fences, projection absence and graph privacy refusal before
connection. The fixtures exercised newly authored developer content.

The [original W5 calibration](cloe-w5-bodystudy1-exit.md) retains its evidence
and interpretation. An evaluated comparison requires a separately reviewed
and selected protocol. Scientific acceptance and production vector admission
remain open under ADR 0206.

## Verification

- Unit selector: `src/test/unit/python/tools/retrieval_lexical_comparator.unit.test.py`. The canonical `--suite unit --no-coverage --jobs 1`
  invocation passed all 17 exact cases. The default host invocation stopped
  before pytest at the scratch byte watermark. The passing invocation used
  the operator-approved one-command limits of 252 GiB and 5,300,000 inodes.
- Integration selector: `src/test/int/python/repomap_kg/retrieval/lexical_comparator_competence.int.test.py`. The canonical
  `--suite int --no-coverage --jobs 1 --pg-container-port 55433` invocation
  passed its one exact disposable PostgreSQL case. The runner materialized
  one managed test-runtime cache image and reported zero owned image or
  volume residue. Candidate application build count was zero.
- The changed-file Ruff and length checks, scoped compilation and retained
  static enforcement passed. The initial changed-file mypy output repeated
  the 143 existing dependency findings byte-for-byte at record level.
  The first inventory run found a cohort ordering error. The next aggregate
  stopped its product checks over the local typing environment. Exact
  versions on macOS then surfaced one Linux-specific Rust readback typing
  finding. The cached Linux SDK matched the product baseline. Adding the
  existing pinned Docker and psutil stubs as read-only inputs completed the
  development type closure. The final offline aggregate passed with zero
  cohort regressions.
- Independent source review accepted all nine frozen grants in
  `dynamic_default`. The dispatcher final stage blocked on its intentionally
  narrower grant set. Parent capture recovery verified the independent
  review and full cleanup. Git whitespace and changed-document links passed.

Complete local unit and integration populations, local coverage, smoke,
staging, system and combined runtime suites were unselected. No evaluated
cohort ran. Hosted CI remains AVAILABLE. This checkpoint is local and
qualifies only the stated scope. CI156 still qualifies its exact public
staging commit.
