# W5 prospective matched held-out comparison

Status: design selected under
[ADR 0218](../adr/2026/11/0218-w5-prospective-matched-heldout-design.md).
This protocol specifies a future study. Case selection and study execution
are separate authorizations.

## Question and treatments

Does existing public-text-v2 fusion improve file ranking on public captured text held
out from project evaluation and tuning over competent native tools and the fixed
lexical comparator? Compare all three treatments on the same frozen cases.

| Treatment | Publication | Fixed behavior |
|---|---|---|
| Native tools | SQLite | Existing native filesystem treatment with a prospectively authored, bounded search plan. Witness the exact files reached by that plan. |
| Lexical | PostgreSQL | `public-lexical-comparator-v1` followed by the existing PostgreSQL lexical service. Plan questions from the same authored text used by fusion. Use the existing declared shape, style, bounds and refusal rules. |
| Fusion | PostgreSQL | The exact incumbent public-text-v2 fusion treatment. Bind its lexical leg, vector representation and model, candidate windows, weighting and tie handling to source. |

Pin each treatment's mode, scope, tool versions, implementation closure and
request derivation before ranking. The standalone lexical planner has no
effect on fusion's internal lexical leg. Request up to ten external hits
from each incumbent treatment and grade at ten distinct files after
first-hit deduplication. The lexical limit is ten raw nodes or chunks
on its first and only service page. Fusion's ten external hits may come
from wider incumbent component windows and native tools may return file
hits. Preserve those semantics and report raw hits, distinct files and
whether the public service page is bounded. The body-positive structural
floor requires at most one eligible lexical projection entry per file in
each case's scoped universe. Verify this from the full projection before
request execution, independent of terms, scores or golds. The signal
applies to naturally single-chunk file scope. Do not trim or reproject a
file to meet this floor. A shortfall stops the entire comparison. A second
lexical page, post-result admission,
cap change and synthetic padding are outside the treatment.

Freeze the following lexical request shapes. For executable lexical rows
use `fallback=None`, `limit=10`, `offset=0` and the declared exact binding
and path scope. Apply the maintained comparator once.

| Cases | Requested mode | Query style | Comparator shape |
|---|---|---|---|
| Body positives (both wordings), absence and ambiguity | `ranked_discovery` | `ranked_terms` | `question` from the frozen authored clue |
| Metadata anchors | `ranked_discovery` | `ranked_terms` | `identifier_or_path` from the frozen anchor |
| Privacy | Evaluator-admission refusal | Inapplicable | No planner call |
| Unsupported semantic mode (both controls) | `literal_captured_text` | `literal_captured_text` | `literal` preserving the frozen text |

Native and lexical carry out both captured-text literal controls using their
maintained literal behavior. Freeze one expected literal match and one
scope-complete literal absence. Record any native fallback that the
maintained route discloses. Fusion must produce the exact
`unsupported_semantic_query_mode` semantic-mode disposition before model,
query or ranking work. Record whether each arm satisfied its declared
control. A wrong result with intact scope is a negative control result.
Actually performing forbidden work invalidates the study.

## Population and custody

Use one new public captured-text generation and 24 independently authored
tasks. Fix these counts before authoring.

| Stratum | Count | Role |
|---|---:|---|
| Behavior without an exact symbol | 4 | Two literal clues and two paraphrased intents, all body positives. |
| Code plus design or message context | 4 | Two literal clues and two paraphrased intents, all body positives. |
| Relevant tests for a behavior | 4 | Two literal clues and two paraphrased intents, all body positives. |
| Metadata anchors | 4 | Compatibility and grading sanity. |
| Answer absent | 2 | Distinguish scoped complete empty, other returns and unavailable. |
| Ambiguous | 2 | Report known relevant alternatives and partial coverage. |
| Privacy | 2 | Synthetic denied scope over public-safe material, with refusal before access. |
| Unsupported mode | 2 | Declared unsupported mode without forbidden downstream work. |

Freeze one grade-3 file gold per body positive and anchor, two distinct
grade-2 relevant alternatives per ambiguous case, and zero golds for
absence, privacy and the mode controls. Validate expected literal control
results against separate, sealed expectations. Golds use binding identity
and relative path. They must resolve to the predeclared public universe.

Fit the incumbent representation and planner limits before sealing.
Bound public-text-v2 preparation to 80 document records, 24 query records,
500 combined records, 512 tokens per model record, 4,096 bytes per
excerpt and 8 MiB of aggregate model text/transport. These ceilings permit only required records. Truncation and extra model
exchanges invalidate the fixed input.
For each question shape require printable ASCII, at most 256 UTF-8
bytes and at least one distinct eligible content term under the actual
comparator. All executable lexical text obeys its documented byte and
style rules. Check structure without model execution before sealing.
Confirm tokenizer fit upfront during the later authorized study.

Require at least 22 admitted, rank-eligible public files for each body
positive across at least two selected source bindings. Freeze the common
file universe per task and all 12 distinct body intents. Within the public
population, freeze the sampling frame, selection order and exclusion
reasons in advance. Preserve scope and public provenance for every selected
file. The custodian must attest that the corpus, intents and golds are
independent of the exposed calibrations and original held-outs, including
renamed or near-duplicate files and paraphrased duplicate intents.
Use a sealed comparison or existing digest registers for exact exclusions
without opening earlier held-out values. Require a separate blind
adjudicator to review overlap and golds before the first exposure. Preserve
the manifest, grading policy and provenance with the sealed cohort.

Separate corpus and case authors, gold adjudicators, native-plan authors
and runners. Corpus and case authors can inspect public sources but must
be blind to treatment output. The native-plan author receives only the
frozen clue, declared mode and binding/path scope, and a fixed
source-neutral rubric. The native-plan author has no source, gold or
treatment-result access and cannot inject answer-bearing paths, symbols
or terms. Freeze the results of this rubric in the sealed manifest.
Record independent custody and adjudication. Claim independence from
project evaluation and tuning. Public-source exposure during model
pretraining cannot be determined from this study.

Assign ordinals 1–12 to body positives, 13–16 to anchors, 17–18 to
absence, 19–20 to ambiguity, 21–22 to privacy and 23–24 to semantic
mode controls. Assign within each block by the predeclared answer-blind
selection order. Freeze each ordinal, family, wording, input and gold
digest, tool plan and expected control. Evaluator input omits golds.
Keep the provisional journal under private custody until closure and
integrity checks pass. Release approved golds to a blind grading process
only after ranking is closed. Public outputs contain reviewed aggregates
and safe identities.

The first complete admissible attempt is primary. A technical retry has
no automatic authority. After any score, rank or metric exposure, an
independent rank-blind review must establish an objective contract defect
from sanitized diagnostics before the operator may select a successor.
Any permitted recovery preserves the exact cases, treatment fingerprints
and grading rules and corrects only the diagnosed technical defect. An
inadmissible run additionally requires independently restored integrity,
ownership and cleanup before a successor can be considered. Record all
earlier attempts and exposure states.

## Correspondence and eligibility

Require two separate current factory-issued publication pins. Bind their
complete publication identities, input and acquisition closure, generation,
ordered snapshot vector, admitted and denied bindings, full projection
policy, per-case input digest and public file universe. Require exact
`(binding_id, relative_path, content_sha256)` equality in native
materialization and PostgreSQL captured-text projection. Match the full
policy parameters byte for byte, including file, size, binary, decoding and
capture limits, apart from the declared backend assignment. A report
digest or missing-pin path cannot stand in for either live proof.

Independently record pre/post pins. Use the same PostgreSQL publication
for both PostgreSQL arms. Both fusion components must be ready there.
Leave caller defaults for publication and snapshot unset. Reconcile the
backend-observed publication ID, snapshot ID, accepted run ID and
applicable lexical/vector generation IDs on every completed PostgreSQL
envelope against the frozen pins. Missing required identity proof is
technical incompleteness. A contradictory identity is inadmissible.
Witness matching native materialization and capture pins for native slots.
Record postflight identity, custody and cleanup separately from eligibility.

Before any rank is computed, attest native reachability, exact lexical
plan admission, and fusion structural and tokenizer fit. Freeze all 12
positive intents and every rankable anchor and control. A valid
eligibility shortfall in any required slot stops all ranks and maps to
`inconclusive_floor`. Operational preparation failures are technical
incompleteness. No query reformulation, denominator shrinkage, fallback
substitution, silent truncation or case replacement is allowed.

Evaluator admission refuses the entire selected scope on either privacy
case with `binding_privacy_not_authorized`. Record the admission layer
before any native path lookup or materialization for the task, lexical
planning or database connection, or query tokenization or embedding.
All scopes, omissions and fences stay in force.

Plan 72 slots, one observation per case and arm. Cycle through the six
arm permutations in this order: N/L/F, N/F/L, L/N/F, L/F/N, F/N/L, F/L/N.
Here N is native, L is lexical and F is fusion. Across all 24 cases each
arm appears eight times in each position. Within the 12 consecutive body
ordinals each arm appears four times in each position and the immediate
ordered pairs balance. Report observed positioning by stratum. A planned
refusal occupies its slot without forbidden work. Preserve all slots
including intentionally unstarted ones. No hidden warm-up or automatic
retry is permitted. The later runtime grant binds the exact model census,
offline dependencies, source, publications and ownership proof.

## Source adapter

The matched adapter reads a committed evaluator USTAR archive and
reconstructs the fixed cases, native plans, standalone lexical requests and
72-slot schedule. The committed evaluator archive independently binds its
projected source inventory and the historical source-frame identifier. Its
only observation inputs are current publication and capture readbacks and
measured fit for the complete rankable census. Each arm receives binding IDs
after evaluator admission. Privacy and semantic-mode refusals enter the
journal before any treatment callback can receive a plan.

The adapter reuses the maintained native and lexical services. Fusion uses
the incumbent direct-text lexical leg and the existing file RRF owner with a
matched exact public-text-v2 vector reader. The runner supplying that reader
must bind the observed component generations and retained inference artifacts
before work. Caller publication and snapshot defaults stay unset in lexical
calls. A current pair is required before ranking and before closing the
journal. The append-only journal records admission before delegating a rank,
preserves pending work after loss of authority and binds the complete event
chain into the final 72-slot ranking receipt.

The custodian opens the separate grading archive only for that receipt, after
complete eligible output and verified postflight. Synthetic proof exercises
the source adapter with separate SQLite and PostgreSQL fixture publications,
real native and lexical literal controls, and an inert vector input.
The runtime envelope must identify and admit the approved offline worker,
artifact closure, concrete publication lifecycle, and one-use command before
evaluation of the sealed corpus.

## Grading and decision rule

Grade unique files by binding identity and relative path with the existing
file-level grading owner. Retain original ranks and compute the established
six-decimal per-task nDCG@10, MRR@10 and recall@10 on the deduplicated ranks.
Use exact means of those recorded decimal values. Weight each body intent
equally. Report exact arm means and 12 paired differences per baseline, plus each family
and wording stratum. Do not pool anchors or controls with body positives.

A complete admissible matched signal requires all of the following.

- Fusion's mean nDCG@10 gain is at least 0.05 over each baseline.
- Its mean MRR@10 and recall@10 are at least as high as both baselines.
- No family's mean nDCG@10 falls more than 0.05 below either baseline.
- Every planned privacy and mode control satisfies its per-arm expectation.

The 0.05 absolute threshold carries forward the practical calibration
screen. This fixed cohort supports a descriptive held-out comparison. It
cannot estimate a population-wide effect or prove production latency.
Report absence and ambiguity on their separate contracts. An incorrect
absence is an explicit W5 acceptance gap even when the comparative signal
is present. Ranked vectors may return candidates in the absence of a
relevant answer. Record what happened without treating arbitrary neighbors
as an answer or an unavailable response as a complete empty response.

Apply outcome precedence in this order.

1. `inadmissible` for broken integrity or custody, privacy disclosure
   or forbidden work, including ranked execution before a ready preflight.
2. `incomplete_technical` for required preflight, evidence, runtime or
   cleanup failures, or missing required observations once the floor passes.
3. `inconclusive_floor` for an observed eligibility shortfall and a
   complete 72-slot ledger. Required ranked slots remain unstarted.
   A completed expected refusal requires its explicit disposition.
4. `matched_signal_present` when the complete observations meet the
   criterion, otherwise `signal_not_demonstrated`.

Outcomes do not grant retry, acceptance, production admission or release.

## Cost accounting

Use one pinned native host and the same lifecycle boundary for every arm.
Record hardware, runtime, cache and concurrency state. Report these
preparation cost scopes separately: native-only SQLite publication and
filesystem materialization, shared PostgreSQL publication and FTS
projection/index, and fusion-only public-text-v2 projection, document
embedding and vector preparation. Original public acquisition is common.
Count preparation once per exact generation and representation.

Report model startup and pre-ranking query tokenization/encoding as
separate costs with query count, scope and reuse. Report per-query
encoding only when actually measurable. Report slot-local SQL RRF time
separately from precomputed vector work and label native and lexical slot
elapsed time with their actual comparable boundaries. Include observed
position by stratum and shared-cache conditions. Shared counters or
overlapping intervals are not additive. Peak RSS has its measured scope.
Mark unavailable and inapplicable measurements with reasons. A zero is a
factual observation only. Report the denominator and scope for all
reported costs, with elapsed-time comparisons descriptive.

One execution per arm and case supports balanced position and comparable
accounting. A repeated-stability or performance study would require a
separate protocol. The inert source implements census, scheduling and
decision arithmetic over synthetic metadata and already graded results.
It does not open sealed inputs, issue publication authority, or execute
retrieval.
