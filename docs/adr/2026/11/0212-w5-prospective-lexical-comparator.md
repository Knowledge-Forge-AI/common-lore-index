# ADR 0212: W5 prospective lexical comparator

## Status

Route A selected by the operator for design, source and scoped proof on
2026-11-18. Execution on any evaluated cohort requires a separate protocol
decision.

## Context

The [first admissible W5 calibration](../../../../tools/ci/cloe-w5-bodystudy1-exit.md)
remains primary under [ADR 0206](0206-proposed-w5-captured-text-calibration-protocol.md).
The [disposable lexical diagnostic](../../../../tools/ci/cloe-ci156-qualification.md)
found that the frozen questions admitted no candidates with the expected
index populated. This motivates an engineering check of a separate query
formulation. It supplies no basis for changing a recorded rank or selecting
terms, questions, golds or thresholds from that cohort.

## Decision

Define `public-lexical-comparator-v1` as an opt-in request planner for
the existing fenced PostgreSQL lexical service. Freeze the
[query and competence contract](../../../specs/cloe-w5-lexical-comparator-v1.md)
before source proof. Natural-language questions use deterministic selection
of up to four ordered English content terms and a controlled disjunction.
Explicit identifier and literal inputs preserve their existing query styles
and text. Binding and path scope, authorization, ranking, fences and
capability semantics remain owned by the maintained service.

Use unrelated synthetic developer witnesses to prove lexical competence.
The planner is source tooling with no default CLI, MCP, model or study
integration. The specification describes the provenance and controls needed
to take a future comparator protocol to operator review.

## Alternatives and rollback

Keeping only the historical all-term baseline leaves the identified
competence question unresolved. Corpus-aware term selection and semantic
rewriting would couple the comparator to data or a model and require a new
authorization. The selected adapter can be removed without data migration.
Its scoped proofs establish request and query behavior only. All frozen
predecessor artifacts and results retain their identities. Held-outs,
scientific acceptance, dependency changes, production admission and
publication remain separate.
