# ADR 0198: Prospective public calibration supplement

## Status

Accepted technical implementation contract following the operator's Route A
selection on 2026-11-14, two independent design reviews and parent correction.
The separate public calibration task class and scoped source work are authorized.
The prospective template gate is accepted before task authoring/implementation. Native execution, scientific criteria, held-outs and
publication are not authorized by this ADR.

Phase: `CLOE-W5-SUPPLEMENT1`. The specific W5-SUPPLEMENT family has no
existing primary owner or alias at this checkpoint.

## Context

Common Lore Index v0.0.4 qualified the existing source contracts. CAL5's public
calibration retained 864 cells but only four unique completed, file-graded
tasks common to its graph, lexical/native and learned arms. Their repetitions
are not independent relevance samples. The public-text vector representation
already incorporates admitted captured body excerpts; query applicability
retains the metadata-stratum/ranked-discovery predicate. Extending the frozen
original tasks or treating their unavailable cells as successes is not a remedy.

The operator selected a separate public synthetic calibration supplement,
bounded to 32 total tasks and a prospective floor of eight common positive
tasks, two per approved family, across six named comparison arms. This keeps
the learned-usefulness objective open for a later evidence decision. It does
not select the alternative deferral of that objective beyond v0.1.0.

## Decision

Use the separately versioned contract in
[the supplement specification](../../../specs/cloe-w5-calibration-supplement-v1.md)
after completed independent technical review. Reuse `EvaluationTask`, accepted public
admission, representation/input, grading and observed-operation owners. Add
only supplement-specific catalog, freeze, prospective applicability,
preparation, operation accounting and report/preregistration seams. Do not
relax the original 36-task catalog, original selected-task pins or native grants.

The planned supplement has 24 calibration-only tasks: four positive tasks in
each of four families, plus eight absent, ambiguous, policy-denied or unsupported-mode cases. Every
case counts toward the operator's 32-task ceiling. Before authoring any task,
the reviewed template matrix must support the eight-positive floor without
changing protected admission/privacy policy. No relevance score is forecast.

The new corpus, tasks and golds receive a data-only input pin before source
implementation. The reviewed source closure is added to a final freeze only
after implementation and resulting-source review, reproducing the input pin
exactly. Both precede any later adapter evaluation. This phase may prepare and validate them but cannot execute supplement
evaluation queries. Source tests use separate synthetic tasks. A later native
study requires its own exact-source/artifact/resource declaration and operator
allocation; no old study or model-worker slot reactivates.

## Preserved boundaries

- Original mixed-v1/context-v1 material, all questions/golds/splits, CAL5 and
  earlier attempts stay byte-identical and are never pooled with the supplement.
- Existing held-out questions/golds remain opaque. Hashing their committed
  container files for preservation does not authorize their evaluation.
- The six comparisons are native-tools search, PostgreSQL current graph,
  PostgreSQL native FTS, canonical-node-v1 exact vector, public-text-v2 exact
  vector and public-text fusion. ANN/HNSW is separately disclosed, never a
  substitute for an unavailable exact arm or part of the eight-positive floor.
- Model/revision/profile, dependency and runtime choices stay unchanged. The
  combined 500-record, 512-token and all request/result/artifact/byte caps hold.
- No product CLI/MCP, canonical graph vocabulary, production schema, reader
  privilege, live home/service, parser, image or test-runner policy changes.
- No complete local suites, local coverage, product model process, study,
  acquisition, application/SDK build or public campaign follows from this ADR.

## Accounting and claims

Operation evidence identifies generation, task/arm/phase, attempt and observed
layer. Reuse existing capture owners. Separate setup, query, inference/import/
index, storage/footprint, cleanup and failures. Preserve unknown, shared and
unattributed work; do not divide whole-container totals into invented arm costs.
Prospective report validation preserves every declared slot and separates
execution, grading, capability and cost completeness. It never accepts a
scientific criterion, superiority claim, production profile or release.

The earlier JSONB disposition continues to retain relational joins at its
observed tiny-fixture scale. Neither this supplement nor its cost observations
adopt a production JSONB index or schema change.

## Verification and rollback

Exact scoped unit owners cover strict data parsing, source/task drift, freeze,
applicability, operation provenance and complete slot accounting. Exact core
containerized integration owners exercise real file custody and publication
boundaries with separate synthetic tasks and existing adapters. Learned model
delivery in functional tests is synthetic and labelled as such. No test runs
the actual supplement, any held-out query or a model worker.

Before native execution, freeze the final candidate closure and return the
exact proposed allocation and cleanup proof to the operator. Missing required
runtime/artifact evidence remains a stop. No design-only public CI is requested.
Rollback removes only unused supplement-owned source/artifacts before adoption;
the original catalog, profiles, evidence and public v0.0.4 remain intact.
