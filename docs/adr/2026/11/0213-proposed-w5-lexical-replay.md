# ADR 0213 — W5 secondary lexical replay

## Status

The operator selected Route A on 2026-11-18 after the reviewed design in
`CLOE-W5-LEXICAL-REPLAY1`. `CLOE-W5-LEXICAL-REPLAY2` owns the implementation,
scoped proof, and one conditional paired replay.

## Context

[ADR 0206](0206-proposed-w5-captured-text-calibration-protocol.md) preserves
attempt 5 as the first admissible captured-text observation. Its original
PostgreSQL comparator returned zero files in all 36 common-positive slots.
The [CI156 diagnostic](../../../../tools/ci/cloe-ci156-qualification.md)
reproduced the candidate loss on a fresh populated generation. The
[ADR 0212](0212-w5-prospective-lexical-comparator.md) source phase then proved
that the bounded disjunction can recover disjoint synthetic candidates
through the real PostgreSQL service.

The engineering question is whether the same request formulation recovers
relevant files on the already-exposed workload while preserving absence
and privacy behavior. Running the two lexical formulations against one
generation isolates the request change. The result may inform the design of
a future independent usefulness study.

## Proposed selection A

Select the [secondary replay protocol](../../../specs/cloe-w5-lexical-replay-v1.md)
and authorize its lexical-only admission class, scoped implementation and
developer proof. Permit the specified conditional execution after the
source, schedule, runtime and custody checks pass independent review.

The replay accounts for all 24 cases with two arms and one repetition.
Sixteen cases exercise the frozen question formulation. Both arms share a
disposable PostgreSQL generation and the maintained policy, ranking and
grading owners. The report is descriptive secondary evidence. It carries
no quality threshold, new fusion comparison, or claim about independent
generalization.

## Alternative B

Leave evaluated replay pending and advance W7 resource evidence. The
approved synthetic competence proof remains available for a later W5
study design. This choice spends no replay attempt and leaves the W5
usefulness requirement open.

## Scope and rollback

The original protocol, input pin, recorded ranks and conclusions retain
their identities. The class uses the existing product service and cached
core PostgreSQL tooling. Model and vector work, new data classes,
held-outs, dependencies, production admission, product defaults and
publication remain outside this decision. Disable future lexical-only
admissions on rollback. Preserve declarations, failures, spent reservations
and completed observations.
