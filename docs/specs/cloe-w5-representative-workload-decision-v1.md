# W5 representative public workload decision

Status: proposed, awaiting operator selection under ADR0205. Phase
`CLOE-W5-DISPOSITION2`; this document is a reviewable choice, not authority.

## Evidence and the actual gap

CI153's nine jobs and same-attempt staging/system/native contracts qualify the
current engineering candidate. They do not accept learned-vector usefulness.
The retained supplement has 24 tasks, 16 comparable positives, six arms, three
repeated queries and 146 counted model records. Repeated queries sharing
embeddings do not add independent relevance samples.

On the 16 positives, native tools, graph, both exact-vector arms and fusion
have file nDCG@10 1.0; native FTS has 0.889143. Controls remain separate:
absent-answer correctness is 18 true/18 false/0 unknown, and privacy refusals
are 36 true/0 false/0 unknown. Capability matches do not establish ranking or
absence correctness. No utility threshold was selected after seeing results.

The decisive source constraint is supplement-v1 section2: its positives are
metadata discovery tasks answerable from names, headings, titles and paths.
Genuine body-dependent questions are excluded. This is a valid measurement of
that class, but a saturated metadata set cannot demonstrate a positive quality
increment for vectors/fusion over those perfect comparators. Cost averages
include all slot states, lack setup/inference coverage and are not a substitute
for a correctness-preserving total-cost study. Current evidence supports
"incremental utility not established," not global inferiority or superiority.

## A: New public captured-text class and source phase (recommended)

Keep useful optional vectors in v0.1.0. Authorize a distinct, opt-in,
public-only captured-text calibration design and source phase with at most 24
new cases. The goal is a representative comparison capable of exposing both
semantic benefit and semantic failure; it does not require a vector win. Use
this concrete prospective matrix; it does not alter supplement-v1:

| Role | Existing family/kind | Planned cases |
|---|---|---:|
| Body positive | behavior_without_exact_symbol | 4 |
| Body positive | code_plus_design_or_message_context | 4 |
| Body positive | relevant_tests_for_behavior | 4 |
| Metadata anchor | exact_symbol_and_partial_name | 4 |
| Control | absent / ambiguous / privacy / unsupported | 2 each (8 total) |

The new operator-selected structural floor is eight distinct common body
positives, at least two per body family. Planned body population is 12;
metadata anchors do not count toward that floor. All 24 cases count toward the
new-class ceiling. Original classes retain their old four-family/all-six-arm
floors. No real private repository is acquired.

### Epic 1: Independent preauthoring contract

Before authoring questions or golds, resolve and independently review:

- A template matrix tied to intended product investigations and actual
  captured public code, test or design text. Inputs are independently authored
  public-safe fixture material; no external repository, license acquisition or
  new runtime dependency is implied. No target repository code executes.
- Reachability through each existing representation and comparator. A metadata
  heading span is not section prose. A filename is not a body-text witness.
  If current capture cannot supply the required span, report that seam and
  obtain its separate disposition before authoring dependent questions.
- All six named arms remain declared with actual surface, fallback and
  capability. Mandatory common body comparators are native tools, PostgreSQL
  FTS, public-text-v2 exact vector and public-text-v2 fusion. Graph and
  canonical-node-v1 retain metadata surfaces: report body-task incompatibility
  explicitly and score all six on the four metadata anchors separately. Do not silently
  drop unavailable arms, claim old six-arm comparability or turn refusal into
  a successful relevance score. Keep the competent native search/read view and
  credible FTS baseline over equivalent public material and output budgets.
- Separation of calibration from every original held-out. Original mixed-v1,
  context-v1 and supplement-v1 are immutable, not pooled or relabelled. A new
  namespace and independent data pin make the distinction explicit. Integrity
  reads do not decode original held-out question/gold values.
- Forecast record/byte/token usage from model-free capture. Preserve the
  existing combined 500-record and 512-token limits, model/revision/profile,
  representation identities and every per-operation time/memory/byte bound.
  Refuse infeasible templates; do not enlarge limits or discard truncated
  positives after scoring to meet a desired denominator.

Fix the wording mix before authoring: each body family plans two natural
literal-clue cases and two natural paraphrased-intent cases, all genuinely
body-dependent. Derive questions from product investigation intent
independently of arm behaviour. Never impose a zero-lexical-overlap eligibility
rule, handicap native/FTS, or select/rewrite cases from ranks. Report
lexical-clue strata without using them to discard failures. If the new set
saturates again, retain that uninformative result; no post-result reauthoring
or hidden replacement is authorized. Any new version needs a new prospective
disposition.

Before data freeze, disclose each body's family-specific planned and surviving
literal-clue/paraphrased counts and every attrition reason. The numeric floor
does not guarantee both wording strata survive; report that limitation rather
than silently dropping missing or failed cases.

Epic 1 proves the designed class-scoped capability through model-free source
and capture evidence, not that legacy supplement-v1 U1 already admits body
queries. Its existing vector/fusion body refusals remain. Epic 2 implements
only the designed isolated class admission after that gate; no queries run. A
global or excluded capture, privilege or policy change is a named stop.

This gate precedes task authoring. It resolves technical feasibility and class
semantics, not scientific usefulness thresholds or final acceptance. The
operator selects the concrete structural matrix/floor above; the parent design
gate verifies it without changing its numbers, dropping a family or expanding
the comparable-arm set. Any new privacy privilege, global admission switch,
extraction/vocabulary change, dependency, image/provider or runner-wide policy
class is outside this option and must return as a concrete separate decision
before adoption.

### Epic 2: Pinned data and isolated admission foundation

After accepted preauthoring design, create the separate public fixture and
freeze its data-only pin before running any retrieval. Use existing typed
task/gold leaves and maintained capture/provenance/grading owners where valid;
do not mutate their old registered catalog semantics or disguise a new task as
metadata-only. Store the actual body/reference/authorization identity needed by
the new class in a closed, versioned record.

Admission must prove the exact registered class pin, public fixture ownership,
captured source/span identity, publication binding and declared per-arm
capability. A caller-supplied body-allowed boolean or ambient path is not
authority. Private, unbound, cross-publication and stale identities still
refuse. All original class refusals retain negative regression owners. The
read-only MCP remains source-blind; no product CLI/MCP or production reader
privilege is broadened by this offline evaluation class.

### Epic 3: Inert preparation and adverse-case proof

Prepare records, applicability, a bounded preregistration skeleton and report
support without invoking a model, adapter evaluation or held-out query.
Per-task/arm/state/phase identity and original failed attempts remain visible.
Unknown physical costs stay unknown; whole-worker time is not divided among
arms. Require exact scoped unit owners and real containerized integration
owners for capture/publication custody, cross-class denial, drift, missing
capability, truncation, incomplete records and owned failure cleanup.

Independent source review and complete static/debt enforcement precede source
acceptance. Accumulate these connected epics into a meaningful milestone;
standing numerical allocation does not require CI for each intermediate slice.
No complete local suite or Python coverage is selected. Add meaningful
portable/publication boundary tests while progressing; hosted CI measures
whether the desired 87/82 margins are met.

### Exit and follow-on authority

The authorized exit is a frozen source/data foundation and reviewed proposal
for prospective scientific criteria and exact native execution. Criteria must
be fixed before tuning/held-out comparison and cannot be chosen to make the
already observed supplement pass. Model execution and held-out evaluation are
not authorized by this selection. No old study or campaign reservation is
reused. A later physical execution needs its own exact preregistration and
explicit class/source/runtime authority within current operation limits.

This source phase produces no usefulness observation. The remaining chain is:

1. Freeze source/data and prepare prospective usefulness criteria, native
   execution protocol and finite operational declaration. Routine numerical
   allocation is already infinite; no new cumulative count ceiling is requested.
2. Obtain the reserved protocol/criteria authority, then conduct at least one
   new calibration study with the existing pinned model/runtime class.
3. Design, freeze and obtain approval for a matching body-class held-out
   evaluation; original held-out questions/golds stay opaque.
4. Evaluate only under that authority and return all positive, negative,
   inconclusive and failed results for final scientific acceptance.

Reserved choices may be combined in a concrete later packet; the sequence does
not prescribe separate approval turns for routine mechanics. At least a source
milestone, new calibration and matching held-out work remain; no runtime/cost
saving or successful vector outcome is promised.

Scientific/held-out/full-W5/production acceptance remains an operator decision.
The source work may establish that the proposed class cannot fit current
admission or representation constraints; such a result is a named blocker, not
permission to weaken those boundaries. Rollback removes only unused new-class
material and keeps every original source/data/result pin intact.

## B: Explicit v0.1.0 requirement amendment

Defer learned-vector usefulness and full fusion acceptance beyond v0.1.0.
Record current optional experiments as utility-unestablished, with existing
defaults and production refusal preserved. Amend the relevant W5/release
requirements only after this written choice, retain all negative/mixed
evidence, and advance W6 Local Rust/parser adoption, W7 whole-operation
resources and W8 installed release/platform/package gates within their own
authorities. No parser/provider, scientific, native-platform, package-manager
or production admission is granted automatically by choosing this route.

B's forward amendment targets are ADR0205's accepted disposition (if selected),
`docs/specs/cloe-roadmap-transition-v1.md` W5/current-release acceptance, and
`docs/specs/roadmap.md` product steps 6 and 8. Historical ADR0077 and earlier
results are retained as history with a forward amendment reference; no original
operator-authored proposal or historical acceptance is rewritten.

The named-workload JSONB obligation is separate from learned usefulness. All
routes retain the observed tiny-fixture decision to keep relational joins;
neither adopts a production JSONB index or accepts an unmeasured larger scale.
Release adequacy of that bounded disposition remains explicit, not silently
removed with the vector requirement.

## C: Keep W5 pending and change roadmap priority

Retain the usefulness requirement and all current holds, approve no new data
class, and prioritize another W6–W8 milestone within its existing authority.
This is also possible under A; B is not required to advance other workstreams.
New Local parser/dependency, physical-governance policy or release/platform
classes still require their own concrete authority. C does not close W5 or
forecast a particular next implementation before its source-backed scoping.

## Why operator selection is needed

ADR0200 reserves new data classes and scientific/held-out/production decisions.
The accepted supplement contract excludes the body-dependent class proposed by
A. B changes the mandatory v0.1.0 product requirement previously retained by
the operator. Neither is a routine numeric allocation or a green-CI inference.
Current "proceed" authority covers this concrete design and evidence work; it
does not select a new data class or amend the product requirement. C offers a
work-order alternative without either of those scope changes.

## Evidence sources

- [CI153 qualification](../../tools/ci/cloe-ci153-qualification.md).
- [Frozen supplement contract](cloe-w5-calibration-supplement-v1.md), section2.
- [Retained analysis](../../tools/ci/cloe-w5-supplement-analysis1-report.md)
  and its [hash/format binding](../../tools/ci/cloe-w5-supplement-analysis1-observation.json).
- [Standing allocation](../adr/2026/11/0200-standing-execution-allocation-and-initial-milestone-ci.md).
- [Accepted analysis boundary](../adr/2026/11/0204-retained-supplement-analysis-and-staging-envelope.md).
- [Original delivery contract](cloe-roadmap-transition-v1.md): preserve the
  required usefulness objective until an explicit requirement amendment.
