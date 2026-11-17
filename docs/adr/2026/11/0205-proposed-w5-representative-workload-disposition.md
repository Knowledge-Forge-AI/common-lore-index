# ADR 0205: W5 representative workload disposition

## Status

Route A accepted by the operator on 2026-11-16 after the reviewed
`CLOE-W5-DISPOSITION2` decision. Implementation phase: `CLOE-W5-BODY1`.
The bounded public captured-text design/source class is authorized; the
v0.1.0 usefulness requirement is retained. Alternatives B and C were not
selected. CI153 qualification is a separate completed fact. Actual studies,
scientific criteria, held-outs and production remain separately gated.

The [technical foundation contract](../../../specs/cloe-w5-captured-text-foundation-v1.md)
must pass independent preauthoring review before new questions, golds or corpus
are authored. Adoption of Route A does not assert that this technical gate,
source implementation or a usefulness observation has passed.

## Context

CI153 qualifies staging `9bb22fd` and its retained-analysis implementation. The
completed supplement has 16 comparable positives across four families, but
native tools, current graph, both exact-vector arms and fusion all score 1.0 on
the file-level headline metrics. FTS has nDCG@10 0.889143. The control report
has 18 correct and 18 incorrect absent-answer observations and 36 correct
privacy refusals. These are small descriptive observations, not evidence of
incremental learned usefulness, generalization or a production profile.

The accepted supplement contract deliberately makes every positive a metadata
file-discovery intent. It says a question must be answerable from declared
names, headings, titles or paths, and forbids relabelling a body-dependent
question to pass admission. Thus adding similar metadata tasks or repeating
inference cannot directly answer the missing captured-text usefulness question.
This does not prove that a different workload would favor vectors.

ADR0200 removes cumulative numerical allocation ceilings while explicitly
reserving new data classes, scientific criteria, held-outs and production
admission. ADR0198's metadata class and fixed data pin remain intact. The
operator previously chose to keep learned usefulness in v0.1.0 and broaden
calibration; the completed observation supplies new evidence for the next
decision, not permission to quietly defer that requirement.

## Recommended selection: A

Keep the v0.1.0 usefulness requirement and authorize a distinct public
captured-text calibration design and source phase as specified in [the decision
contract](../../../specs/cloe-w5-representative-workload-decision-v1.md). Bound
the proposed class to 24 new calibration cases: 12 body positives across three
existing families, four remembered-name metadata anchors and eight controls.
Keep every predecessor input, question, gold, held-out, result and failed
attempt immutable.

The proposed operator-selected structural floor is eight common body positives,
at least two per body family, across native tools, FTS, public-text-v2 exact
vector and fusion. Graph/canonical-node-v1 stay metadata comparators, with the
four anchors scored separately. Original all-six-arm/four-family floors remain
unchanged and cannot be satisfied retroactively by this class.

The first gate is an independently reviewed preauthoring design. It must prove
actual public captured-text reachability, truthful per-arm capability and a
credible native/lexical comparator before new task authoring. It must apply
those compatible-arm/common-positive rules explicitly. Source support then uses
a new pinned opt-in admission class with exact unit/containerized integration
owners; it cannot weaken legacy refusals or expose body text through the MCP.

This selection authorizes bounded design, new public fixture/data-class source
and model-independent proof only. Actual model/study execution, held-outs,
scientific thresholds/acceptance and production admission are not selected.
Their prospective protocol and exact-source execution packet follow source and
data freeze. No new dependency, provider, parser, image or runner-wide policy
class is included; any essential excluded seam returns to the operator.

## Alternative selection: B

Explicitly defer learned-vector usefulness and full fusion acceptance beyond
v0.1.0. Keep existing experiments documented as experimental with utility
unestablished, existing defaults and production refusal intact. Amend the
authoritative W5/release requirements only after the operator chooses this
route, then advance the genuinely open W6–W8 gates. This changes product scope;
it is not an acceptance claim for current vector quality or production use.

The exact requirement amendment targets are named in the decision contract. All
routes preserve the separate JSONB obligation and its retained tiny-fixture
relational disposition; no production index or larger-scale acceptance follows.

## Alternative selection: C

Keep W5's requirement and unresolved result, authorize no new evaluation class,
and prioritize another W6–W8 milestone within its existing authority. This
changes work order, not release acceptance. W6–W8 also remain advanceable under
A: usefulness deferral is not a prerequisite to their progress.

## Status quo

Retain the current metadata fixture and repeat no inference. This preserves
valid evidence but leaves the required usefulness decision unanswered. A
technically green core pipeline cannot close that scientific/product gate.
Repeating unchanged models is not selected as a remedy for the ceiling effect.

## Consequences, gates and rollback

Route A buys a source/data foundation, not a usefulness observation. Further
work includes prospective criteria and native-execution authority, at least one
new calibration study, a separately approved matching held-out evaluation and
final scientific acceptance. Reserved choices may share a later concrete
packet; routine numerical allocation stays infinite. Each physical operation
still needs exact source/runtime proof and finite operational limits. No
hardware-time estimate or vector win is promised. Capture feasibility may stop
A before task authoring. A renewed saturated ceiling is an uninformative
result, not permission for post-result reauthoring.

B shortens v0.1.0 scope; C retains W5 while changing priority. All choices
retain source compatibility, privacy, history, debt and current operation
limits.

The operator owns this choice. Independent review assesses technical readiness
of the packet; it cannot accept the data class, alter release requirements or
select scientific criteria. The written selection accepts Route A only; retain B and C as rejected alternatives.
Rollback removes only unused new-class preparation and retains all results. No
docs-only public CI, main promotion, tag or release is selected here.
