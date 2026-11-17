# W5 public captured-text foundation v1

Phase: `CLOE-W5-BODY1`. Route A in ADR0205 is operator-approved. The technical
preauthoring design passed independent dynamic_default review, followed by the
parent clarifications recorded below before workload authoring. The separate
24-case catalog and corpus now have an immutable data-only input pin. Source
review passed with no blockers. Final exact-snapshot proof and source-freeze
reproduction are required before publication. Existing operation,
record and token limits apply.

## Purpose and preserved boundaries

Build a separate, opt-in public captured-text calibration source/data foundation.
The source phase makes no retrieval/model call or usefulness observation. It
prepares a concrete prospective protocol without selecting scientific criteria.
Original mixed-v1, context-v1 and supplement-v1 bytes, identities, questions,
golds, held-outs, refusals and failed results remain immutable. No original
held-out question or gold is decoded; predecessor integrity uses bytes/hashes
and task-ID-only token metadata.

No dependency, package metadata, lock, parser, image/provider, runner-wide
policy, extraction/vocabulary, product CLI/MCP or production privilege changes.
The maintained read-only MCP remains source-blind. No actual native study,
model materialization/inference, held-out protocol or scientific acceptance is
included. Complete local suites and local Python coverage remain unselected.
Routine allocation is infinite; every later operation still has finite limits.

## Preauthoring matrix and reachability

Use the operator-selected matrix unchanged: 24 calibration-only generation-1
cases, 12 body positives (four per family below), four exact-symbol metadata
anchors and eight controls (two each absent, ambiguous, privacy, unsupported).
Each body family plans two natural literal-clue and two paraphrased-intent
wordings. Before freeze disclose planned/surviving counts by family and wording,
every attrition reason and any missing stratum. The common body floor is eight
distinct intents, at least two per family, across native, FTS, public-text-v2
exact and public-text-v2 fusion. Anchors never satisfy that body floor.

| Body family | Investigation template | Required observed witness |
|---|---|---|
| behavior_without_exact_symbol | Locate a rule from its body-level branch, transformation or failure behavior | Existing Python function/module span containing the rule; names/paths alone cannot resolve the distinguishing behavior |
| code_plus_design_or_message_context | Locate code implementing a stated rationale, message or exception policy | Existing Python span containing code plus its design comment or message; the comment is captured source text, not a document edge |
| relevant_tests_for_behavior | Locate a test demonstrating an outcome or adverse boundary | Existing static Python test-function/module span containing its assertion and setup; the test-source file is data and is never executed |

The code-plus-context family deliberately uses embedded design comments or
messages within an existing code span. It does not claim section-prose support
from Markdown. Current `doc.section` spans cover headings only; they may support
metadata anchors but never body-positive witnesses. A mandatory question that
actually needs uncaptured section prose is infeasible and stops before authoring.
No docstring/document relabelling or invented code/document canonical edge.

Use independently authored public-safe fixture material with ordinary meaningful
names/paths, credible distractors and small bodies. Do not conceal useful native
metadata, deliberately obfuscate identifiers, impose zero lexical overlap or
handicap FTS. A body-positive rationale must identify the distinguishing source
fact that metadata alone cannot determine. Derive wording and complete golds
from investigation intent and source inspection before any retrieval; do not
derive search patterns/golds from returned ranks. A native query plan may use
natural salient terms from its question, with no gold-path restriction.

Plan at most 24 small corpus members, each at most 4096 UTF-8 bytes. Avoid classes,
nested definitions and extra helpers unless the model-free inventory can still
prove at most 80 eligible node/binding pairs per representation. Code/test
functions provide body witnesses; optional Markdown members provide heading
anchors only. Denied controls use inert synthetic markers in a declared denied
binding, never a real secret. All materialization uses new owned fixture roots.

Native tools keep their competent authorized body scan/read view and disclose
literal fallback. FTS uses the maintained captured-text projection with
`ranked_terms` and equivalent public material and output limit 10. The vector
arm uses the unchanged `canonical-node-public-text-v2` span/model-text encoding;
fusion depends on that exact query and FTS, without substituting HNSW or v1.
Graph and canonical-node-v1 explicitly report body incompatibility and retain
their metadata surfaces. Declare all six arms and score the four metadata
anchors separately across all six; do not pool those scores into the body set.

This is a designed class-scoped capability, not existing legacy U1 acceptance.
The current Python extractor uses AST lineno/end_lineno for functions and full
module spans. The unchanged raw excerpt mapper preserves the bytes inside those
observed intervals, including comments and assertions. Source-only probes
demonstrate this on three unrelated snippets, without publication/native claims.
Real SQLite/PostgreSQL accepted publication, span correspondence and projection
readback remain required integration evidence before source acceptance.

### Grading unit and body-dependence evidence

The grading unit is the file, using existing maintained file-level ranking
metrics and file-only gold locators. Body witnesses add observed line/node
identity without changing the gold/grading unit or introducing a new scorer.
Every body positive must need body evidence to distinguish relevant files,
not merely to distinguish two nodes within an already obvious gold file.

Each body-positive intent names at least one distinct same-family non-gold
distractor file whose actual names, paths and headings fit the question equally
well. Keep ordinary meaningful names; no obfuscation or metadata withholding.
Freeze the observed metadata identities and a human-inspected static rationale
explaining the ambiguity and the distinguishing body fact. Witness validation
checks the real metadata/source membership, family/source-role, distinct file,
actual captured body fact and contrasting distractor fact. Equal semantic fit
is an inspected authoring assertion, not an automated linguistic proof; disclose
that limit and preserve the full material for independent review. A case whose
metadata already reveals its graded gold file fails body-dependence review.

Prefer function spans and place required facts after the def line and before
the last executable statement, near the beginning of a short function. Comments
above def, decorators and comments after the final statement are not function
witnesses. Python python_common.end_line reads positive end_lineno and otherwise
falls back to lineno; parent inspection confirms this unchanged implementation.
Never use a module-span fallback to conceal a missing function witness.

For every retained fact record its source line/UTF-8 byte interval and actual
UTF-8 offset/length within the encoded model text, plus model-text identity.
Disclose long prefixes, module-only spans, late facts and other truncation risks
before data freeze. These offsets are not proof that 512 tokenizer tokens retain
the fact; later tokenizer truncation remains a native-confirmation gate, and
lost positives cannot be silently replaced or used to shrink the denominator.

## Separate class, records and two freezes

Reserve namespace `body1-` for the new registered class; functional tests use
`unit-body-` and cannot load as registered. Store new fixture data under
`src/test/fixtures/retrieval_evaluation/body-v2/`. Do not call the legacy
supplement parser with a relabelled metadata copy. Reuse strict JSON primitives,
EvaluationTask/GoldTarget leaves and maintained grading; a new closed parser
owns role, wording stratum, body-witness references and applicability semantics.

Each case stores task, role, independent intent ID, wording stratum and witness
references. A witness identifies binding, source-relative path, source SHA-256,
observed node/evidence identity, line interval, and a required body fact in that
interval. Metadata anchors record their metadata basis. Witness text is part of
public frozen data, not private runtime evidence. Semantic task stratum is body
and intended surface captured_text for body positives; it is not rewritten to
pass a v1 guard. Use requested ranked_discovery with existing allowed leaves.
Controls keep their honest refusal/unavailable/absence semantics and exact gold
sets; unsupported cases are not counted as positive samples.

Strict parsing rejects unknown/missing fields, wrong types (including bool for
integer), duplicate keys/IDs/files/intents, nonfinite values, depth/size overflow,
held-out splits, foreign generation/class/family, unsafe paths and mutable
external references. Preserve both existing task digest schemes. Records expose
defensive copies or immutable leaves; nested caller mutation must not change
validated identities. Maximum 24 tasks, 512-KiB JSON, 32-level depth and existing
transport/excerpt bounds remain. No new representation version.

First freeze data-only input identity: catalog/corpus/policy/bindings/namespace,
predecessor byte manifest and preauthoring gate reference. Revalidate the actual
static capture inventories and all witnesses before accepting the floor.
Record attrition without dropping failed cases or rewriting from rankings. Every
planned/authored case counts toward 24, including controls and failed cases.
After source proof and independent resulting-state review, reproduce this input
pin byte-for-byte and add a separate exact preparation-source closure. Never
make the data pin depend on its own registration constant or later freeze.

### Pre-query input correction

The first data-only input pin is retained byte-for-byte under body-v1. Its
registered check refused the eighth context-family case because neither source
variant contained an in-span design comment or message. It is unaccepted history,
with no retrieval, tokenizer or model execution. Revision 2 adds explicit design
comments to those two functions, recomputes observed spans and witness/model-text
hashes, and retains all 24 task IDs, questions, golds, intents and task digests.
It introduces no replacement sample or changed scientific result. The current
body-v2 pin also binds all ten first-freeze files as predecessors; the registered
loader rechecks their original pin, every member and exact unchanged task map.
Bindings, policy, namespace and preauthoring gate remain byte-identical. The
source-bound revision record discloses both complete trees. Old pins cannot load
as the current registered class, and original evidence is never overwritten.

### Control strata and prospective matrices

Absent and ambiguous controls use body/captured_text/ranked_discovery. Their
four body arms are prospectively capable; graph and canonical-node-v1 remain
unavailable for metadata_representation_not_comparable (graph uses its existing
captured_text_surface_unsupported_by_metadata_graph reason). Absent has no
golds/absent_answer; ambiguous has all legitimate equally graded file golds and
ranked_positive. Neither contributes to the body-positive floor.

Both privacy controls use body/captured_text with a declared denied binding;
all six arms refuse before capability or capture, including mixed public/denied
selection. One unsupported control uses body/captured_text/literal_captured_text:
native and FTS support it, graph/v1 report body incompatibility, v2/fusion report
unsupported_semantic_query_mode. The other uses metadata/stored_metadata/
current_literal_enumeration: native/graph/FTS are prospectively capable with
their existing explicit fallbacks; v1/v2/fusion remain unavailable for
unsupported_semantic_query_mode. These are expected capabilities, not executions.

## Class-scoped admission and inert preparation

New `retrieval_body_*` owners own this class. Old public_text_applicability,
_query_decision, supplement expected_applicability and registered sup1 loaders
are unchanged. No monkey patch, mutable registry or body-allowed boolean.

A registered loader independently binds the exact approved data-only pin and
all referenced bytes, then rereads members for each downstream use. Generic
functional parsing cannot issue a registered admission object. The new admission
factory requires a registered class, exact case/task digest and membership,
declared arm capability, current factory-issued VerifiedPublicationPin and the
actual BodyProjectionPolicy. Reuse admit_w5_task's policy/binding/publication
checks, then require full-authorized body policy for every selected binding.
Declared privacy denial and unbound selection refuse before publication
readback. Remaining cases revalidate the issued pin through its existing
trusted readback adapter; stale or wrong-publication inputs refuse before
candidate capture. No candidate, query, model or execution callback is accepted.
The new class binds every policy field, including max_projected_files, and
explicitly requires every task binding in selected_binding_ids. The legacy
policy digest and legacy admission behavior remain unchanged.

Mirror the existing issued-pin pattern: exact registered record type, private
factory token, immutable owned canonical bytes and an is_factory_issued check.
Recompute input membership and current binding at every preparation use. A
generic parser result, dataclasses.replace, constructed object or asserted
registered flag is not a registered admission proof. Functional validators may
exercise the algorithm on unrelated data but public registered entrypoints must
refuse those objects.

The reused admit_w5_task path revalidates a factory-issued current publication
pin, graph/backend, calibration split/generation, policy and frozen-policy
digest, optional post-publication identity, full selected-binding IDs and
declared/observed denial/absence classification. _policy_denial checks both
original and resolved graph/binding privacy, unresolved inherit, full-authorized
retention for body tasks and forbidden email/feed bodies. Parent readback of
the complete functions confirms no metadata-only comparability gate there.
The new class still performs its own registered membership and arm checks;
finding any legacy metadata gate at implementation is a named stop, not a
reason to edit or bypass it.

For a body task, only native, FTS, public-text-v2 exact and dependent fusion can
be prepared. Graph/v1 remain unavailable for representation incompatibility.
For anchors, retain the six-arm metadata applicability rules. Privacy denial
takes precedence over capability unavailability. The output is an inert,
source/data-bound preparation decision; it never grants model, native or
production execution. Actual queries cannot be performed by this front end.

Prepare documents with the unchanged public-text descriptor builder, retained
span correspondence, projection membership, metadata/body privacy and excerpt
encoder. A new class-specific query-input record owns body admission; do not
overwrite the old QueryInputDecision or insert body queries into its v1 bundle.
The new retrieval_body_records.build_body_query_records builder owns the
separate class-scoped v2 query records. Query text is the exact frozen task
question, UTF-8, SemanticMode.QUERY; bind its SHA-256 and the unchanged
cloe-w5-public-generation-input-v1 query-text encoding (no document prefix,
question rewrite or fabricated legacy QueryInputDecision). Bind class, task,
admission/publication and public-text representation identities. Unit owner
retrieval_body_preparation.unit.test.py and real publication integration owner
retrieval_body_publication.int.test.py prove byte identity and cross-class/drift
refusal without running a model. The native/FTS task plans use existing typed
leaves and strict actual policy.
Record v1 documents/metadata anchors separately from v2 body queries. Keep
public-text query/fusion dependency identity exact, not a model-result stand-in.

Use private internal common validators with unrelated functional inputs to test
algorithmic checks. Tests cannot turn a generic functional catalog into the
registered class: public registered entrypoints still reject it. Unit and real
publication tests use unrelated `unit-body-` questions/golds; the actual new
study questions/golds are inspected/frozen as data rather than used as test
expectations or executed retrieval. The registered loader's hash/membership
boundary is separately checked with mutated, foreign and undeclared inputs.

The source-only inventory canonicalizes each binding independently. Maintained
publication qualifies spanless file-node canonical keys with the binding alias.
The real publication proof checks the exact bijection against declared public
corpus membership, without adding an observed file span or changing any function
key, witness, graded locator or frozen input byte. All other canonical pairs
must match directly. Neither namespace is silently substituted as body evidence.

## Capacity, reporting and next protocol

Count the v1 and v2 eligible pair sets independently from actual maintained
representation/public-text inventories. Include every emitted kind (modules,
test-profile nodes, doc.page and distractors) in its applicable document census;
record explicit exclusions rather than counting only gold functions. Do not
assert equal v1/v2 membership or turn an excluded spanless doc.page into body
support. Refuse more than 80 pairs in either representation.

At most 80 node/binding pairs per representation and 24 possible queries imply
at most 2*(80+24)=208 records, below the existing combined cap 500. Actual v1
queries exclude body tasks; v2 eligibility excludes privacy/unsupported cases.
Count every partition explicitly; document_count + query_count == record_count
== embedding_count remains the future execution invariant. Repetitions reuse
query embeddings and do not add independent relevance samples.

The inert estimator counts actual captured input records/bytes and refuses any
cap violation before a future execution. UTF-8 byte size is not a tokenizer
count. No inference/tokenizer is loaded now. Actual 512-token counts and
truncation require future tokenizer receipts; truncated required targets or
queries cannot count toward the native-confirmed floor. Unknown setup/inference
costs stay unknown, with no division of worker time across arms.

Reports bind exact class/data/source/publication/task/arm/state, all declared
cases and planned slots (24*6*3=432), capability and refusal reason, body and
anchor strata, wording attrition, witness reachability and missing evidence.
No synthetic success or row count certifies execution, usefulness or origin.
The prepared protocol lists prospective scientific questions and unresolved
criterion fields; it chooses no threshold from the saturated old result.
Actual model study, matching held-out work and final acceptance require their
separate authority, potentially combined in one concrete future packet.

## Implementation sequence and proof owners

1. Independently review this complete design, the approved decision, source
   seams and model-free probe before questions/golds/corpus authoring. Correct
   any technical finding within Route A; an essential excluded seam stops.
2. Author and directly inspect the 24-case matrix and separate corpus. Capture
   model-free witness inventories, disclose attrition and make the data-only pin.
3. Implement the new class parser, pin/custody, witness/reachability, isolated
   admission, inert records/report and check-only front end. Use unrelated
   functional tests, preserve original bytes/refusals and negative cases.
4. Run exact scoped unit and real containerized integration owners with
   --no-coverage, changed/affected static and retention enforcement, diff checks,
   independent resulting-state review, freeze reproduction and exact commit.
5. Accumulate a meaningful source milestone. Prepare a fresh publication grant
   only if that campaign is decision-relevant and within the accepted Route A
   boundary. Never publish a docs-only checkpoint or reuse CI153's spent nonce.

Planned unit selectors under src/test/unit/python/tools/:
retrieval_body_catalog.unit.test.py, retrieval_body_pin.unit.test.py,
retrieval_body_witnesses.unit.test.py, retrieval_body_admission.unit.test.py,
retrieval_body_preparation.unit.test.py, retrieval_body_report.unit.test.py.
Exact affected legacy unit selectors in the same tools directory are
retrieval_evaluation_admission.unit.test.py,
retrieval_public_text_admission.unit.test.py,
retrieval_semantic_generator_inputs.unit.test.py and
retrieval_supplement_applicability.unit.test.py. Preserve their actual existing
body-refusal and policy/publication assertions without updating expected values.
Planned integration selectors under src/test/int/python/tools/:
retrieval_body_capture.int.test.py, retrieval_body_publication.int.test.py,
retrieval_body_files.int.test.py. Also retain the existing exact
src/test/int/python/tools/retrieval_supplement_publication.int.test.py selector.
They prove real accepted SQLite and PostgreSQL
source/span/projection correspondence, cross-class denial and drift/cleanup;
they do not use a fake pin to claim publication or native/model acceptance.
Use tools/run_tests.py --suite unit|int --no-coverage -- <exact selectors>.
No complete suite, smoke, staging, system, combined run or local Python coverage.

Add meaningful portable publication/binding refusal tests where changed
boundaries expose missing branches; preserve all denominator, threshold and
debt ceilings. Hosted CI alone measures the desired 87/82 margins. Integration
still needs 477 branches at CI153's denominator; this is an observed gap, not a
promise that tools-only tests raise product coverage.

## Failure and rollback

Fail closed on missing body witnesses/capability, infeasible capture, pin/source
drift, bad path/JSON, denied privacy, insufficient floors, truncation or cleanup
failure. No hidden replacement or post-result reauthoring. A renewed saturated
study remains uninformative rather than permission to redesign after results.
Rollback removes only unused new-class source/data and retains every historical
input/result/attempt. Qualified CI153 and v0.0.4 remain distinct immutable facts.


## Source review dispositions and prospective execution stops

The independent resulting-state source review passed. Its advisory limits are
preserved here; a source-phase PASS does not authorize an execution packet.

- Reports and record ledgers are per backend and per issued publication. Native
  requires SQLite, while FTS and graph require PostgreSQL. A single report
  cannot establish the common four-arm body floor. Any future execution packet
  must define and prove an explicit cross-report join over identical corpus,
  input pin, generation, task digests, grading unit and representation identities,
  with exactly one declared backend/publication per arm and no unavailable-arm
  substitution. The join must retain every refused/unavailable slot. It requires
  real admissions for both publications and the eight-intent/two-per-family
  floor after tokenizer confirmation. This join is unimplemented and unaccepted;
  no present report claims the floor or cross-backend execution.
- Final source proof reruns the four exact integration file owners and the
  registered check-only front end; exact selectors and snapshot hashes are
  retained with their receipts. Failed earlier attempts remain evidence.
- The source-freeze closure must contain the actual projection_policy.py bytes.
  This binds the default max_projected_files=4096 omitted by the legacy policy
  serialization. Runtime admission independently checks complete policy identity.
- The predecessor manifest's commit identifies the older committed predecessor
  set. Its additional ten body-v1 members were working-tree bytes at that commit,
  preserved under their individual hashes and first pin, rather than files
  claimed to exist in that commit. The revision builder also checked unchanged
  intents and the localized witness correction. Those last two comparisons are
  builder-only assertions; the registered loader guarantees the original pin,
  every original member byte, unchanged task digests and immutable members.
- Wording labels are pre-query authoring strata, not a promise of zero lexical
  overlap. body1-11's gold comment literally contains "failed handoff returns
  ownership"; body1-08's revision-added distractor contains "partial plan".
  Preserve and disclose these overlaps in future lexical-stratum analysis;
  neither labels nor samples may be relabelled after results.
- The denied binding's static inventory is authored and shape-checked. It is
  never recomputed by extracting denied source. It is not observed public capture
  or native evidence and cannot satisfy a positive floor.

The decision spec is included in the explicit source closure. Duplicate unrelated
functional fixture helpers and repeated bounded revalidation remain maintenance
advisories; they do not justify changing class, privacy or custody semantics.
