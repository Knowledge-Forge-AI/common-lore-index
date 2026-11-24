# W5 prospective public calibration supplement v1

Status: accepted technical implementation contract under operator-approved
Route A, after two independent technical reviews and parent correction. Phase:
`CLOE-W5-SUPPLEMENT1`; ADR0198. Date: 2026-11-14.
The preauthoring template gate is accepted. The source candidate now contains
the separately pinned public tasks/corpus, preparation and evidence support,
with scoped unit/integration proof and canonical retention enforcement. Final
source review and freeze reproduction remain required before source closeout;
native execution and scientific acceptance remain separate decisions.

## 1. Scope and predecessor preservation

Author a separate public synthetic calibration class, then prepare its source
support and inert future preregistration. Preserve all 54 tracked predecessor
fixture files under `src/test/fixtures/retrieval_evaluation/` at v0.0.4 commit
`39cb2b3452bfd32b05d38f842c0f891614cbf58d`: mixed-v1/context-v1 inputs, catalogs,
golds, splits, historical attempts and identities. The exact byte manifest is
`sha256:a9879e1e27858ffb5efb58238084fd04b43c863e76e7fd3e4ff928e8164c72d7`.
No whole predecessor catalog loader or held-out question/gold decoder is used.
Hash checks may read committed container bytes without decoding those values.

Old `TaskCatalog` remains 36 tasks/24 calibration/12 held-out. Old
`AttemptDeclaration`, `SelectedTaskPins`, `ExperimentPlan`, census and task
validation caps are unchanged. Reuse `EvaluationTask`/`GoldTarget` leaf records,
admission, representations, grading, source custody and query-operation capture;
new supplement records own class/freeze/slot/phase identities. No global
registry mutation, subclass trick, monkey patch or old native-grant reuse.

Task IDs use reserved `sup1-` plus two digits; corpus and supplement IDs have
the same reserved prefix. Check original calibrated IDs and use the existing
byte-level selected-task locator to test proposed IDs against the hash-pinned
original catalog without invoking any task-value decoder. Only task-ID string-token
metadata is decoded (including JSON escapes), for both authoritative mixed-v1
and context-v1 catalogs; do not scan question/gold text for a prefix or claim
hashes alone establish namespace disjointness. No original value is relabelled.

## 2. Preauthoring templates and applicability

Plan exactly 24 new cases, calibration-only, generation 1:

| Intent | Existing EvaluationTask family | Positive cases |
|---|---|---:|
| Remembered names | exact_symbol_and_partial_name | 4 |
| Behavior to code | behavior_without_exact_symbol | 4 |
| Code plus design context | code_plus_design_or_message_context | 4 |
| Relevant tests | relevant_tests_for_behavior | 4 |

Eight other cases use family `absent_ambiguous_changed_deleted_stale`: two
absent (`absent_answer`), two ambiguous (`ranked_positive`, multiple legitimate
gold targets), two policy-denied (`safety_refusal`) and two deliberately
unsupported-mode cases (`ranked_positive`, with declared per-arm capability).
These do not count toward the four-family floor. Every case counts toward the
authorized 32-task ceiling. The first implementation prepares at most 24 tasks
because reused per-exchange owners cap 24; it neither claims to execute 32 nor
changes an old cap. Additional authored capacity is not execution authority.

The 16 positive templates use strict integer generation 1, split calibration,
`ranked_discovery`, metadata stratum and intended surface `stored_metadata`.
This is a metadata file-discovery intent: questions identify relevant names,
behaviors, test targets and design sections from declared public metadata.
Never relabel a body-only/excerpt-dependent question merely to pass a guard.
Actual arm surfaces remain distinct and disclosed:

| Supplement arm | Existing capture arm | Representation/surface |
|---|---|---|
| native_tools_baseline | native_tools_baseline | authorized body view; fixed-string scan with disclosed literal-text fallback |
| current_graph_baseline:postgresql | current_graph_baseline:postgresql | stored metadata/navigation |
| postgres_native_fts:postgresql | postgres_native_fts:postgresql | captured text; ranked_terms |
| exact_vector:canonical-node-v1 | exact_vector | canonical-node-v1 |
| exact_vector:canonical-node-public-text-v2 | exact_vector | admitted captured-span public-text-v2 |
| hybrid_rrf:canonical-node-public-text-v2 | hybrid_rrf | PostgreSQL FTS + public-text-v2 exact vector |

The intended surface is not a claim that native/FTS/vector matching took place
on metadata. The strict parser validates intended/requested vocabularies;
unknown strings cannot exploit guards that only reject `captured_text`.
Native tools retain their competent authorized body view. No fallback is
reported as a missing arm's execution. HNSW is not in the mandatory six arms.

Count at least eight distinct positive intents, two per named family,
prospectively applicable to all six arms. Planned count is 16. Static
representation/exposure inventories must show every required gold file is
reachable in each arm; gaps remain reported but cannot satisfy this floor.
This checks candidate coverage, not query matches or scores. No output/rank
is used to choose a task, query, gold or family. Reauthoring before the input
pin is allowed only for model-free structural/capacity evidence, with history.

Selected bindings must be declared public, body-authorized and captured under
existing policy. Test paths must be eligible under that same policy or their
family fails. Design context uses standalone Markdown heading-derived
`doc.section` targets. Their observed span is the heading line only: positive
questions must be answerable from heading/title/path metadata, not section
prose. Each template records those distinguishing metadata fields. Stop if
fewer than two per family survive. Do not use spanless `doc.page` nodes or
Python docstrings relabelled as documents. Existing Markdown extraction and
canonicalization already supply those node kinds and spans. Code and design
targets stay separate; no direct code-document canonical edge is invented.

Before authoring, publish the template matrix with source predicates and
assumptions. After authoring/static capture, recheck actual inventories and the
floor without evaluating supplement queries. Native/runtime availability is
still unproved. An unavailable exact arm at preparation voids the common floor;
no substitution or smaller denominator is allowed. If feasibility needs an
excluded privacy, extraction, graph or admission change, stop before authoring.

## 3. Corpus and record/token forecast

Plan at most 24 small public synthetic files: twelve Python source files,
eight non-collected Python test-source files and four Markdown design files. Logical test sources use
`tests/test_*.py` and `def test_` names as static source intent; the canonical
runner collects `*.test.py` under unit/int roots. Corpus sources are data in
a bundle, materialized only into owned fixture roots, never canonical test
roots, and are not imported or executed.
Each code/test target has a captured existing-kind node with an observed span;
each Markdown target uses a heading-derived section. Target code is never
imported or executed. No connector, external corpus, real private data or
realistic secret is an input. Policy-negative content is an inert synthetic
marker under a denied binding, not a credential.

The upper bound is 80 accepted node/source pairs per representation. With all
24 tasks conservatively counted as potential queries, the two representations
need at most 2*(80+24)=208 records, below the existing combined 500-record cap.
Actual semantic eligibility excludes two denied/two unsupported cases, so the
forecast is at most 20 query embeddings per representation (200 combined).
Fusion reuses its public-text exact-query/vector exchange; three retrieval
repetitions reuse prepared embeddings and do not multiply inference records.
Every eventual exchange must prove document_count + query_count == record_count
== embedding_count. No trimming based on adapter outcomes is allowed.

Preserve the 512-token limit, 4096-byte excerpt limit and all existing transport,
request/result/artifact/record caps. Keep fixture spans/queries deliberately
small, but do not pretend a byte bound proves token count. Eventual tokenizer
receipts must disclose actual counts/truncation; a truncated required target
or query cannot count toward the later native-confirmed positive floor. Exceeding
the forecast or any immutable cap is a stop, not an implicit increase.

## 4. Strict catalog and two freezes

`cloe-calibration-supplement-catalog-v1` has closed fields for schema/class,
corpus/phase, rationale, cases/tasks and the arm/exposure/applicability matrix.
Bound UTF-8 JSON, size/depth, duplicate keys and non-finite values. Validate
exact types before `EvaluationTask.from_mapping`: booleans are not integers,
bindings are lists of strings and exposure is an object. Round-trip task
serialization must equal the strictly parsed values. Bound IDs/paths, reject
unknown fields/families/versions, held-out splits, duplicates and generation or
corpus drift. Use only the four positive families plus the named other family.

Record both task digest schemes explicitly: canonical JSON ASCII-escaped
(`retained_task_sha256`) and canonical UTF-8 (`task_sha256`). They may differ
for non-ASCII text; never substitute one for the other. A class hash binds each
digest to its scheme. No new English-only limitation or silent normalization.

First author/directly inspect fixtures and golds and make a data-only input pin:
catalog bytes, exact fixture entries, policy and predecessor manifest. No
source closure is claimed before source implementation exists. Source tests
always use separate synthetic tasks in a distinct test-only namespace, never
the supplement questions/golds, and cannot be loaded as the registered class.

After source and scoped proof pass independent review, publish the final
`cloe-calibration-supplement-freeze-v1`: reproduce the input pin byte-for-byte
and add the reviewed preparation-source closure. Source changes cannot alter
the input pin; mismatch is a stop. Source membership is the exact sorted list
produced by the maintained builders plus an explicit supplement owner list,
supplied as data by the caller. Tools owners do not import test support.
The caller, in the existing test-support/orchestration layer, reruns the
maintained builders and supplies their fresh member set at every check. Tools
owners do not import those test-support builders; they compare the supplied
set and reread members
under existing evaluator-closure custody. The base builder's source globs are
recorded as a fixed result, not used to discover fixture inputs. Confirmed
`candidate_source_files` excludes fixture data; catalogs/input pins/freeze
outputs are separate input hashes, avoiding self-reference. Builder glob families include retrieval/*.py,
retrieval_evaluation*.py, retrieval_semantic_*.py and w5_experiment*.py; new
supplement code/support members are explicit. The final commit/
tree is a later distinct preregistration binding, not an earlier execution.

Fixture entries use exact relative paths, bounded size and regular-file
identity. Refuse absolute/traversing paths, symlink components, nonregular
files, drift and partial reads. Exclusive complete-file publication refuses
reuse/overwrite. A freeze never authorizes any supplement evaluation query.

## 5. Source seams and operation evidence

New `retrieval_supplement_*` owners cover contracts/catalog, input pin/freeze,
applicability/reachability, preparation, operation scope, report and inert
preregistration. A prepare/check-only front end has no execute/model/study
command. No product CLI/MCP, schema, graph vocabulary, image, package metadata,
dependency or test-runner policy change is included.

The supplement declaration owns its six-arm x task x three-repetition slot
set (24*6*3=432 for this plan). Do not feed qualified arm strings into the old
`ExperimentPlan`. Any later vector sub-plan uses bare existing arm names,
at most 24 tasks and three repetitions. The two exact representations have
distinct attempts/journals even if output hashes coincide. New references bind
capture record hash AND representation/profile/journal/attempt identity; never
insert phase/class fields into the closed old operation record. Missing exact
input makes fusion unavailable; no HNSW/metadata substitution.

Existing generation timing is not per-operation phase evidence. The existing
query timing owner overwrites `query_ns` with an elapsed sample, so blindly
subtracting snapshots would be invalid. Add a small sequential caller-owned
phase scope with a monotonic clock and explicit identity; attach existing
query captures or independently observed field samples where available.
Adapters/kernel formats stay unchanged. Invisible/nested/shared work remains
coarse, shared or unattributed. Never split whole-container/model-worker time
into per-arm estimates or sum overlapping timing layers.

Phase records identify class/freeze/source, generation/task/arm or shared owner,
phase, attempt, units, state and observation completeness. Separate setup,
input preparation, inference, import, index, query, footprint/storage,
cleanup/teardown and failures. Preserve unknown/null for unavailable cost,
actual query/row/statement and byte-layer scope, shared owner identity, and
non-additivity. An original body exception is retained even if capture fails;
neither failure is silently discarded. No new concurrency or background worker.

Reports preserve the exact declared ordered slot set and all completed/failed/
unavailable/refused/not-run states. Refuse missing, duplicate, foreign, changed
or stale cells. Separate grading and safety-refusal correctness; use maintained
ranking/file grading rather than a new scorer. No pooling with predecessors,
no denominator shrink and no independent-sample claim from repetitions.
ANN recall versus exact remains separate if later separately declared. Synthetic
delivery is functional-only; row counts or asserted receipt flags cannot issue
origin/scientific/execution/production/held-out acceptance.

## 6. Concrete engineering/proof sequence

1. Parent accepts independently reviewed ADR/design, templates, capacity and
   namespace checks before actual task/gold/corpus authoring.
2. Author/directly inspect new class; generate the data-only input pin.
3. Implement source, using separate synthetic test inputs throughout.
4. Run exact scoped `tools/run_tests.py --suite unit|int --no-coverage` owners,
   affected Ruff/mypy/compile checks, clean ownership admission and diff checks.
5. Independently review resulting source; publish the final source-bound freeze
   only after it reproduces the earlier data-only pin and predecessor bytes.
6. Prepare inert preregistration/resource/cleanup forecast for a fresh operator
   study/allocation decision. Do not execute or publish under this phase.

Planned exact unit owners under `src/test/unit/python/tools/` are
`retrieval_supplement_catalog.unit.test.py`, `retrieval_supplement_freeze.unit.test.py`,
`retrieval_supplement_applicability.unit.test.py`,
`retrieval_supplement_preparation.unit.test.py`,
`retrieval_supplement_operation.unit.test.py`, `retrieval_supplement_report.unit.test.py`
and `retrieval_supplement_declaration.unit.test.py`.
They cover parsing/coercion/digests, capacity, mode/policy/gold reachability,
source/task drift, symlinks/partial/exclusive files, actual phase scope and
exception behavior, complete slots and shared/unknown/non-additive cost.

Planned exact integration owners under `src/test/int/python/tools/` are
`retrieval_supplement_files.int.test.py` (real filesystem custody),
`retrieval_supplement_publication.int.test.py` (real SQLite/PostgreSQL accepted
node and span readback, including standalone Markdown doc.section and test-path
admission), and `retrieval_supplement_evidence.int.test.py` (real owned work and
evidence lifecycle). They run in the existing core sandbox, with distinct
synthetic tasks and explicitly synthetic model delivery, no supplement query
or model. Stubbed `w5_operation_orchestration` is not real-boundary evidence.

Use only existing verified runtime prerequisites. No SDK/application build,
artifact acquisition, optional-profile materialization, complete local suite,
local coverage or held-out query. Missing required integration is a named gap,
not grounds to bypass isolation. Desired unit87/integration82 cushions and hard
85/85,80/80 remain, without debt/threshold/suppression/denominator relaxation.

## 7. Stops, rollback and later decisions

Stop if the common floor, target reachability or test-path/Markdown eligibility
requires an excluded policy/vocabulary/extraction change; preserve diagnostics.
No retry, new runtime, model worker or old allocation is silently enabled.
Undo only unused supplement-owned source/artifacts before adoption; originals
and released v0.0.4 remain intact. Existing five studies/two workers and hosted
allocations stay spent. A later exact native declaration, scientific criteria,
matching held-out class and overlap disposition remain operator decisions.
Prior tiny-workload JSONB retains relational joins. Full W5, production vectors
and v0.1.0 remain open. No design-only or source-only public campaign is granted.


## 8. Accepted case and inventory details

The positive common-field template applies only to the 16 positive cases.
All selected fixture bindings are metadata/body public except two designated
denied code-source files under a separate denied binding. Neither denied file
is a positive gold or an eligible model document. Privacy case P1 selects only
that binding; P2 selects it plus public bindings. Both use declared binding
privacy denial (metadata and body), mode ranked_discovery and intended stored
metadata. Every arm must go through the maintained W5 admission gate before
its primitive callback. It returns REFUSED/binding_privacy_not_authorized for
all six arms, before callbacks. Do not call the unfenced native primitive and
mistake its empty AVAILABLE result for a correct refusal. Expected states are
still recorded per arm; body-only denial is not the chosen privacy case.

Unsupported semantic case U1 requests literal_captured_text with body stratum:
native and FTS may complete on authorized bodies under their existing style;
PG graph is unavailable for body retrieval; both vector arms and fusion are
unavailable with metadata_representation_not_comparable. U2 requests
current_literal_enumeration with metadata intent: graph/native/FTS can complete,
with native/FTS literal fallback disclosed; vectors are unavailable with
unsupported_semantic_query_mode, and fusion follows public-text-v2. These are
per-arm capability expectations, not a claim every arm is unavailable or every
completed answer is correct. Absent and ambiguous cases use existing grading;
no capability failure is upgraded to a retrieval success.

Inventory recheck occurs before the data-only input pin using existing static
extraction/canonicalization and policy predicates, without evaluating queries.
Count every candidate node/source pair, including file/module/function/test,
doc.page/doc.section and any auxiliary nodes. No ADR/skill-shaped paths or
frontmatter are in this corpus. If the 80-pair cap or reachability fails,
revise only on static evidence before the pin and retain that history; after
the pin a gap is a stop or an explicitly versioned repin, never a rank-driven
repair. Real accepted-store reachability remains a later native preflight.

Gold EvaluationLocator pairs binding_id with the binding-relative metadata
source_relative_path. Canonical graph observation paths are alias-prefixed,
but multi_source_routing explicitly preserves the original unprefixed path in
metadata. Do not double-prefix golds or infer a source locator from graph text.

The existing combined worker cap is enforced by the partition-sum validator
in retrieval_public_text_preflight.py (sum of v1/ptx documents/queries <=500),
and response accounting binds input, record and embedding counts. A source
unit owner must show the new prepared request contains each eligible query
once per representation and every repetition references that same prepared
exchange: replication into three new inference requests is forbidden. The
forecast is prospective; synthetic proof is not native execution.

The native surface is STORED_METADATA only when every selected materialized
path is a metadata path; otherwise it is captured text with the maintained
fixed-string fallback. Declare and verify the actual branch, rather than
treating the task's intended surface as an observation.

Phase scopes preserve the original body exception and separately record any
capture failure. Do not nest a scope with measure_query_work on the same ledger.
Zero-valued generation defaults or non-owner cleanup shares are not observed
per-arm costs; keep them shared or unknown. No instrumentation edits to old
adapters/kernel formats are authorized here.

The canonical phase ID is CLOE-W5-SUPPLEMENT1. Earlier private design receipts
used REPOMAP-CLOE-W5-SUPPLEMENT1 before acceptance; those exact receipts remain
immutable as scoped design-preparation evidence. No prior public phase ID is
rewritten. The identity policy requires uniqueness, not a legacy prefix.

## 9. Implementation review clarifications

The source-kind label for the declared folder bindings is `folder`. An explicit
data-only pin revision corrects the parent's earlier `repository` label before
source adoption. The superseded pin and its exact inputs remain phase evidence;
the catalog, queries, golds, corpus, binding IDs, privacy and retention did not
change. The current input pin is
`sha256:c401ff63624c3ed3c0a3b28987cc3b99cacd82907f8a576d51fe56278db98b62`.
Pin validation preserves those exact bytes, including formatting. Final freeze
construction and verification require the externally supplied expected pin
digest; registered inputs additionally require the reviewed registered digest
and original 54-file predecessor manifest. A self-consistent replacement pin
does not satisfy that boundary.

The no-builder-import rule applies to new tools directly importing test-support
builders or orchestration. Reusing the maintained FTS fallback selector retains
its existing transitive test-support import, which stays in the reviewed source
closure. That reuse does not invoke predecessor catalog loaders or fixture
materializers. The direct-import guard makes no claim to prove the absence of
all transitive imports. Caller-side source builders still run freshly at every
freeze/check boundary; no duplicated FTS resolver or old adapter edit is needed.

The shared BGE/ONNX artifact profile identity is distinct from each arm's
embedding-profile identity. The v1 `EmbeddingProfile` hashes its own fields;
public-text-v2 `StudyEmbeddingProfile` also prefixes its schema. Declarations
therefore bind one artifact identity and both separate embedding identities.
Vector/fusion journal bindings use the appropriate embedding identity, and
capture checks compare the observed profile to that per-arm binding. Baselines
have no embedding profile. Pure construction of these profile descriptors is
source-derived metadata, not retained native execution or a model-worker run.

The immutable predecessor manifest stores bare lowercase SHA-256 hex digests.
The final-freeze boundary explicitly normalizes that scheme for guarded file
reads; it also accepts the prefixed scheme used by functional manifests. No
manifest bytes or input pin are rewritten. Both schemes receive source drift
checks, including a bare-hex real checkpoint integration case.

Reports retain supplied actual modes, surfaces and fallback disclosures, and
compare surface/fallback expectations as well as execution/reason. A mismatch
stays visible without removing its slot. Completed native delivery requires
the supplied scoped-path surface sequence, bound into the result artifact hash;
the branch is metadata only when that nonempty sequence is entirely metadata.
The prospective corpus forecast exposes bodies, while runtime delivery checks
the actual branch separately. Caller-supplied scope remains unqualified evidence,
not proof of native ownership. Required native origin remains a later gate.

The maintained file grader accepts an optional exact representation version,
defaulting to its unchanged canonical-node-v1 behavior. Supplement vector/fusion
calls pass their declared representation, preserving v2 identity without a new
scorer or changing an old result schema. `FileFusionResult` itself has no journal
or embedding-profile fields: its exact bytes are bound by the qualified capture
reference, which separately binds the declared journal and observed profile.
This is a structural join, not native component-origin authentication; eventual
native admission must verify the underlying arm/journal/worker correspondence.
Phase costs with task/arm/repetition owners must belong to declared slots;
explicitly shared costs retain their distinct shared-owner semantics.
Result artifact binding uses the maintained public-text serializer with explicit
per-arm representations, rather than the generic result's legacy single v1
label. A foreign representation's observed arm cannot be substituted into the
declared slot even if its other result fields are self-consistent.
