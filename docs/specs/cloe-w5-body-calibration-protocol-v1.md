# Proposed W5 captured-text calibration protocol

Status: prospective decision under [ADR0206](../adr/2026/11/0206-proposed-w5-captured-text-calibration-protocol.md).
No criteria or body-study execution is selected by this document. It completes
the protocol proposal required by accepted [ADR0205](../adr/2026/11/0205-proposed-w5-representative-workload-disposition.md).

## Problem and preserved evidence

[CI155](../../tools/ci/cloe-ci155-qualification.md) qualifies the captured-text
source/data foundation. The earlier supplement saturated on metadata positives;
its observations cannot establish incremental body-retrieval usefulness.
There are no body-study rankings from which this protocol or its thresholds
were selected. No usefulness win is predicted.

Use the frozen body-v2 input pin
`sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b`.
Keep the 325-member original source freeze at
`tools/ci/cloe-w5-body1-source-freeze-v1.json` independently verifiable. It
qualifies the foundation only; derive an additional complete study closure
after implementation. Retain the original unaccepted body-v1 pin and all 73
predecessor files, questions, golds, prior journals and failures. Do not decode
predecessor held-out values or reuse their authority.

The 24 fixed cases contain 12 body positives across three families, four
metadata anchors and eight controls: two each absent, ambiguous, privacy and
unsupported. One generation, six arms and three retrieval repetitions create
432 selected slots. Every refusal, unavailable row, failure and unstarted slot
remains represented. Repetitions reuse embeddings and are not independent
relevance samples. All authoring and input revisions stay closed.

## Publications and comparison unit

The technical backend assignment is native-tools on the SQLite publication;
graph, PostgreSQL native FTS, canonical-node-v1 exact vector, public-text-v2
exact vector and public-text-v2 fusion on the PostgreSQL publication. The
native observation surface remains `native_filesystem`; its logical target
backend is SQLite. Backend assignment is a source design choice, not a new
scientific selection or product default.

Preserve each publication's complete 144-row preparation report separately.
An explicit cross-publication join selects one declared backend per arm into
144 rows and 432 slots, rather than executing both 432-slot matrices. The
other publication's rows remain preparation evidence. Do not replace an
unavailable selected row with an available counterpart or merge digests into
one fictitious publication. Formatter mappings retain `origin_proven=false`;
they cannot become controller admission or execution proof.

The join must bind current factory-issued input and publication objects;
class/corpus/input pin, graph, generation, ordered snapshot vector, admitted
and denied bindings, each task/case digest, full projection-policy bytes
including `max_projected_files`, representation, source closure and backend
declaration. Preserve both distinct publication identities and independently
issued pre/post capture pins. Check exact observation envelopes and native
materialization/PostgreSQL projection witnesses. Reuse existing correspondence
checks without confusing their legacy policy digest with a full policy hash.
Explicitly require both SQLite and PostgreSQL pins; the legacy correspondence
helper's missing-pin early return is insufficient. Fusion's FTS and v2 dependency
rows must both be ready on the same selected PostgreSQL publication. SQLite
vector/fusion preparation cannot establish that capability. Reject stale,
fabricated, mixed-class, cross-generation and cross-policy joins before retrieval
or model work. A raw report or query envelope is insufficient.

Grade unique file identities with the existing file-level relevance metrics.
Deduplicate multiple chunks through the maintained grading owner using
(binding_id, source-relative path), never path alone. Prove that same-path files
in two bindings remain distinct in unit and real publication integration tests;
retain raw ranks/chunks separately. Do not grade one arm by node and another
by file. Report the full 24-case matrix, each family and literal/paraphrased
stratum, all four anchors and all eight controls separately. No anchor/control
score is pooled into the body-positive headline.

## Structural and tokenizer gates

Before any ranked retrieval, derive the common eligible body-positive set
across native tools, FTS, v2 exact vector and fusion using actual admission,
captured-text reachability and tokenizer receipts. Retain the accepted floor
of eight distinct intents and at least two in each of three families. Report
all 12 planned positives, every excluded reason and wording distribution.
No rank, gold success or latency may determine inclusion. Freeze this set
before query execution. Tokenizer receipts may arrive with embeddings, but
no similarity computation, candidate scoring or rank may be computed until
the common set is frozen. A later error remains missing/incomplete, never an
exclusion that shrinks its denominator. Failure of the floor is inconclusive.

The source census forecasts 66 v1 document records plus four anchor queries,
and 44 v2 document records plus 20 eligible queries: 134 records across two
workers. This is a prospective source count, not actual model admission,
tokenizer fitness or execution evidence. Fusion reuses the v2 query exchange.
Graph/v1 remain incompatible with body intents. Privacy cases must refuse
before model input; unsupported semantic modes cannot acquire embeddings.
Retain the existing 80-document/24-query per-representation, 500-combined-record,
512-token, 4096-byte excerpt and 8 MiB transport/model-text limits. No truncation,
hidden warm-up, replacement task or extra query/model exchange is permitted.
Tokenizer observations are charged to the same two workers, not a third
unrecorded calibration pass. A changed census requires source correction and
fresh review, without altering the fixed task/gold class or these bounds.

## Proposed criterion A: calibrated fusion signal

The operator must select this criterion before any body-model/query study.
The primary candidate is the current public-text-v2 fusion policy, fixed by
exact source before execution. No rank-driven weight, query-plan or prompt
tuning occurs. The two primary contrasts are fusion versus native tools and
fusion versus PostgreSQL FTS, using the frozen common-positive set above.
Both comparators use their competent task-authored plans and actual fallback
semantics; do not cripple a baseline to manufacture an improvement.

For each task/arm, average the three repetition metrics first, then weight
each distinct eligible task equally. Report each repetition and disagreement.
A descriptive `calibration_signal_present` requires all of:

- Mean file nDCG@10 improvement of at least 0.05 over each comparator.
- Mean file MRR@10 and recall@10 no lower than either comparator.
- No family's mean nDCG@10 regression greater than 0.05 against either
  comparator, using that family's predeclared common tasks.
- Complete observations for the frozen common-positive matrix, valid ownership
  and source joins, no privacy disclosure and correct declared semantic-mode
  refusal. A missing required observation cannot be scored as a successful zero.

The 0.05 absolute effect is a proposed practical calibration screen, not a
statistical significance threshold or a derived production requirement. As an
illustration with 12 single-gold tasks, one rank-2 to rank-1 change contributes
about 0.0308 to mean nDCG; two contribute about 0.0615. All 12 frozen body positives and all four metadata
anchors have one
grade-3 gold file each. Both ambiguous controls have two grade-2 golds each;
both unsupported controls have two grade-1 golds each. They remain outside the
headline. The maintained metric definition remains authoritative. With 8-12
common positives, one rank-2 to rank-1 gain contributes 0.0461-0.0308 to the
global mean. With 2-4 tasks per family, the inverse top-rank loss costs that
family 0.1845-0.0923 without an offset. The proposed 0.05 family screen thus
protects against a single unoffset top-rank loss and may reject aggregate gains
with a family tradeoff. This strict practical choice is prospective, not tuned
after observation; descriptive route B remains available.
The threshold was not fitted to body ranks. Report all paired differences;
do not select the weaker comparator or best representation after observation.
Exact-v2 quality is secondary and cannot rescue a failed primary fusion test.

Metadata-anchor metrics and controls remain distinct. Report absent-answer
correctness, ambiguous relevance, semantic refusals and all unexpected results
without relabelling them. No empty-result guarantee is invented for a vector
surface that lacks one. Incorrect absence behavior remains an explicit W5
acceptance gap even if the descriptive body signal is positive. Any privacy
disclosure invalidates the study's admissibility; retain evidence without
publishing the disclosed content.

The outcome classifier uses this precedence, with all subordinate observations
retained rather than hidden by the headline:

1. `inadmissible`: broken source/publication/ownership integrity, privacy
   disclosure or actually executing forbidden semantic/model work. No signal
   can override an authority or integrity failure.
2. `incomplete_technical`: no preceding integrity violation, but failed required
   preparation or telemetry/cleanup proof; or, after the floor passed and
   retrieval became required, a missing, timed-out, `not_run` or unstarted frozen
   slot. Intentional unstarted retrieval after a valid low floor is not a
   technical failure. Never call an unstarted positive unsupported or
   remove it from the denominator. Diagnose a technical cause before a fresh
   reviewed one-use attempt; the state label alone grants no retry.
3. `inconclusive_floor`: valid complete pre-query preparation fails the eight/
   two-per-family structural or tokenizer floor. Do not run ranked retrieval
   or retry solely to repair this valid scientific result. Stop the entire
   retrieval phase, including anchor/control ranked queries. Retain their
   intentional unstarted slots; preflight refusal decisions are not relabelled
   as executed query outcomes.
4. Otherwise, with the floor met and a complete admissible matrix,
   `calibration_signal_present` only if every numerical and observed refusal
   requirement above passes; else `signal_not_demonstrated`. Correct expected
   privacy-refusal codes and semantics are also required. A wrong well-formed
   privacy refusal that discloses nothing and executes no forbidden work is a
   valid negative, as is an incorrect well-formed semantic refusal; neither
   authorizes a technical retry.
   Actually executing a forbidden mode instead has precedence 1. Numerical
   nDCG/MRR/recall/family failures map only to this complete negative state.

Before queries, disclose the candidate-file universe for each task and selected
arm using publication/representation facts, not top-k results or gold success.
The frozen static inventory has 11 public files in each of two bindings. Body
positives select both, giving a prospective 22-file universe; anchors select
one, giving 11. Reconcile actual prepared universes and record discrepancies.
For uniform random ordering of N eligible files with one gold, expected recall
at k is min(k,N)/N, and expected nDCG@k is
sum(1/log2(r+1), r=1..min(k,N))/N. At k=10, the prospective body values are
0.4545 recall and 0.2065 nDCG; anchor values are 0.9091 and 0.4131. These are
analytic context, not an executed random-ranking arm or a modified threshold.
Recall@10 is near-saturated for the anchors, not for the 22-file body universe.
Keep paired competent-baseline contrasts primary and publish actual N and
gold counts; never collapse same-path files from different bindings.

A ceiling effect
or a valid negative is a scientific result, not a technical failure warranting
repetition or reauthoring. A positive is still small public calibration, not
generalization, scientific acceptance, production admission or W5 completion.
Matching held-outs and their criteria remain separately selected before use.

## Source and proof before conditional execution

Implement new body-specific study plan/admission, cross-backend report join,
v1-anchor query exchange, controller/capture/analysis owners and one closed
opt-in body-study cohort. Never relabel `SupplementIdentity` or reuse an old
supplement cohort, journal, nonce or grant for this class. Formatter declarations
stay inert. Separate parent-issued one-use admission binds operator selection,
frozen input/protocol, fresh source, runtime/provider, both publications,
requests and the runner-provided live ownership attestation. Lost or reused
authority refuses before model/query start; preserve failed reservations.

Required scoped unit owners cover matrix completeness, exact publication and
policy joins, stale/fabricated admission, anchor-only v1 inputs, record/token
limits, file metrics and all output classifications. Containerized integration
owners use unrelated synthetic `unit-body-` publications in real SQLite and
PostgreSQL to prove positive correspondence and unrelated-publication refusal,
then exact new-cohort ownership/cancellation/cleanup seams. They do not execute
the frozen body model study. Use canonical scoped `--no-coverage` selection;
no complete local runtime suite or Python coverage is included.

Independently review implementation, complete source closure, negative tests,
actual command schedule, all admission joins and diagnostics before activation.
Pure preflight must derive full read/transfer/time accounting from that schedule.
Current registered-input properties revalidate predecessor bytes repeatedly;
do not assume a two-publication per-row loop fits 12 GiB, cache away freshness,
or widen a limit. Refuse a nonfitting plan and return a concrete correction or
reserved boundary. A separate body-study closure may include new owners while
the original 325-member foundation stays unchanged and verifiable.

## Proposed conditional native envelope

Reuse the exact retained model/provider/runtime identities in sections 3 and
3a of the [supplement execution reference](cloe-w5-supplement-study-v1.md).
That reference supplies bytes and existing mechanics, not authority for this
new class. Reverify artifacts, license/security gate receipts, provider archive,
SDK recipe/config and complete source closures before admission. Current asset
availability and budget fit have not been established by this proposal.

One fresh native ARM SDK, one owned inner PostgreSQL lifecycle, one verified
retained-runtime import and two sequential sibling model workers are proposed.
Zero image/SDK/application builds, materializations, exports, pulls, downloads,
APT/pip network, compiler, provider substitution, emulation or host installation.
Existing hash-locked offline installation inside the two workers stays bounded.
No host Docker socket enters the SDK; only the runner-provided live host-pipe
attestation authorizes sibling model work. Cleanup is exact-attempt ownership
only; unknown ownership or incomplete cleanup blocks reuse.

Keep each worker network-none, root read-only, capabilities dropped,
no-new-privileges, 2 GiB, 1 CPU, 64 PIDs, 768 MiB scratch and 64 MiB temporary
mount. Preserve readiness/init/model deadlines 120/600/550 seconds and aggregate
worker limit 1700 seconds. Inner PostgreSQL stays socket-only, network-none,
JIT-off, 2 GiB/2 CPU/256 PIDs/256 MiB tmpfs. The new cohort alone may use a
4800-second SDK and 5400-second controller; old cohorts retain their deadlines.
Keep archive/read/transfer/owned-scratch/provider bounds 768 MiB/12 GiB/6 GiB/
8 GiB/4 MiB. These controlled counters are not physical-I/O guarantees.

The source-reviewed schedule must fit these nested limits with remaining-budget
checks; the historical forecast is not proof of fit for body preparation.
Measure quality and observed cost separately. Report shared RSS/I/O and unknowns
honestly; do not sum overlapping intervals, apportion whole-container time as
model latency or infer resource savings from a limit. Retain all partial slots,
telemetry and cleanup observations on every terminal outcome.

ADR0200 keeps routine allocation infinite. A diagnosed implementation correction
may use a fresh source-reviewed, finite one-use attempt under the same selected
class/envelope. Retain every failure and actual counter; no unchanged replay,
best-score selection, post-result input/criterion change or borrowed reservation.
For this new class, the first technically complete admissible attempt is
primary, irrespective of its quality. This body-only rule supersedes the old
supplement's last-complete selection without changing historical classes.
A proposed invalidation of that complete attempt requires an independent
review blind to its ranks/metrics, identifying an objective contract defect,
then operator disposition before a successor may replace it. Retain and report
both results. Ordinary incomplete technical attempts may still be diagnosed,
corrected and retried under standing routine authority; valid negative,
saturated or low-floor results cannot be rescued by relabelling or replay.

Any incomplete or inadmissible attempt that computed a similarity score, rank
or quality metric also requires an independent rank-blind objective-defect
review before a successor can run. Give that reviewer score/rank/metric-stripped
technical evidence and retain its hash-bound originals privately. A routine
successor is permitted only for the diagnosed transport, telemetry or cleanup
defect, with byte-identical model, corpus, inputs, query plans, retrieval/scoring/
fusion and grading/criterion behavior, proved by explicit source fingerprints.
Report every exposed observation; classification alone cannot rescue quality.
An exposed attempt requiring a change to those scientific treatment owners,
unprovable blindness or invalidation of a complete primary returns to the
operator before a successor. This is a scientific-scope disposition, not a new
numeric allocation. Inadmissible attempts additionally refuse all successors
until exact ownership, integrity and cleanup are restored and independently
proved. Without score/rank/metric exposure, a diagnosed correction within the
selected class/envelope may use the normal reviewed one-use recovery path.
No unknown ownership, privacy exposure or valid negative is waived by routine
allocation.

## Milestone, publication and rollback

After selection, batch three connected epics: body execution/ownership and
correspondence; one admitted frozen experiment; complete paired quality/control/
cost analysis and coverage improvement. Aim for useful 10k-100k LOC without
padding. Select a fresh source/event-bound hosted campaign only after integrated
proof/review; do not publish this proposal alone. The outstanding 82% integration
branch aim remains unchanged. Main, tags, release, held-outs, production and
full W5 acceptance are outside this selection.

Rollback disables only unused new-class admission/source. Retain immutable
inputs, declarations, failures and observed results; never roll back evidence.
No new product CLI/MCP/schema/default or graph vocabulary is introduced.

## Alternatives requiring operator selection

A selects the proposed quantitative calibration screen, new body-study
runner-policy class and implementation with conditional execution under the
gates/envelope above. It does not accept final scientific usefulness.

B selects the same bounded implementation/execution class for descriptive
calibration only, with no pass/fail usefulness screen. It cannot use those
ranks to select and retrospectively pass a criterion; a later scientific
decision requires a prospectively declared evaluation. W5 stays open.

C keeps this proposal pending, executes no body study and prioritizes another
already-authorized roadmap milestone. It changes priority without accepting
or deferring the v0.1.0 usefulness requirement.
