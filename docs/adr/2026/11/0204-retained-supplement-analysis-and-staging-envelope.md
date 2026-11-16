# ADR 0204: Retained supplement analysis and staging envelope

## Status

Accepted for bounded engineering implementation under the operator's
2026-11-16 roadmap-continuation request. Scientific criteria, held-outs,
production admission and full W5 acceptance remain operator decisions.

## Context

CI152 ran the repaired candidate and passed eight jobs. The staging integration
job was cancelled at the repository's 180-minute outer envelope. Its log shows
continuing test progress at 99% shortly before cancellation; there is no complete
same-attempt staging report or coverage result. This is incomplete qualification,
not a passed staging gate and not evidence of a particular test failure.

The selected supplement observation is technically complete: 24 tasks, six arms,
three repetitions, 432 slots and 16 comparable untruncated positives across four
families. Its current public observation exposes completeness and eligibility
to propose criteria, but does not explain comparative retrieval quality and
costs in an operator-reviewable form. The raw, hash-bound report already holds
maintained grading and layered cost observations. Repeating inference is neither
necessary nor selected.

## Decision

Assign `CLOE-W5-SUPPLEMENT-ANALYSIS1`, a new specific subfamily checked against
existing phase and ADR identities. Deliver three connected engineering epics:
strict retained-input and provenance admission; deterministic paired quality,
control and cost analysis; and bounded public-safe decision artifacts. Follow
the [analysis contract](../../../specs/cloe-w5-supplement-analysis-v1.md).

The analyzer consumes already retained results. It does not invoke adapters,
models, databases, Docker, external tools or network. Reuse maintained grading
values and immutable task/source identity. Do not change tasks, golds, scoring,
model parameters, result selection or scientific thresholds. Repetitions share
embeddings and are not independent relevance samples. Display them as stability
observations, then give each distinct task one weight.

Report all six arms, all declared slots and controls, family denominators and
missing/refused/unavailable outcomes. Compare only explicitly compatible metric
names and common comparable tasks; unknown is not zero. Do not choose a winning
attempt, pool failed predecessors or tune from a held-out class. Preserve the
technical attempt history and its failure disposition. A decision artifact can
state that criteria selection is pending; it cannot grant scientific acceptance.

Publish the JSON and Markdown as two logical members of one deterministic,
uncompressed ZIP artifact. One exclusive link publishes the complete archive;
two independent file links cannot promise atomic visibility of a pair. Logical
member hashes and the archive hash are separate. A crash may leave an unpublished
temporary file, but cannot expose a partially published analysis archive. Bind
the analyzer's transitive local imports without executing them, including imports
inside functions, separately from the original evaluator and worker closures.

### Staging timeout

Raise only the public `staging-integration-gate` outer `timeout-minutes` from
180 to 240. Update its strict topology contract and fixture/test owners. Keep
the historical standalone staging lane at 180, all other job envelopes, normal
triggers, the one complete smoke-then-integration command, same-attempt M/A
obligations, coverage floors and every inner operation/cleanup deadline.

GitHub's hosted-job limit is six hours, so a concurrent split is not required
by that platform limit. A later evidence-backed partition would need to retain
the complete population, same-attempt measurement and exact aggregation; this
phase does not introduce one. See [GitHub limits](https://docs.github.com/en/actions/reference/limits).

## Alternatives

- Repeating the completed model study would spend resources without answering
  the missing analysis question and risks selecting a preferred result.
- A prose-only summary lacks executable identity, denominator and privacy joins.
- Automatically choosing usefulness thresholds would cross the operator's
  scientific acceptance boundary.
- Leaving 180 minutes repeats an observed outer-envelope cancellation. Splitting
  jobs now adds aggregation and coverage-provenance changes without a platform
  need. A finite 240-minute envelope preserves existing qualification semantics.

## Verification and rollback

Use independent synthetic input, exact scoped unit and containerized filesystem
integration owners with coverage disabled, complete changed-source static
enforcement, and independent dynamic_default dispatcher review. Freeze source
before one inert analysis of the retained report; bind original report hashes,
analysis source, task identities and output bytes. Native observation remains
qualified only for its original explicit source boundary.

No new dependency, runner cohort, model/study, build, pull or runtime provider
is selected. Complete local suites and coverage remain unselected. Publish the
integrated reviewed milestone once to staging with a fresh finite CI reservation
and actual historical counters. Main, tags and releases are unchanged.

Rollback removes only new analysis-owned files/resources and retains all original
reports and attempts. Reject partial or ambiguous output publication; do not
overwrite existing analysis artifacts or relabel CI152 as passing.
