# W5 secondary lexical replay v1

[Proposed ADR 0213](../adr/2026/11/0213-proposed-w5-lexical-replay.md) reserves
operator selection of this protocol and its execution class. The question
is whether the verified request planner recovers relevant files on the
exposed captured-text workload and what it does to absence and ambiguity
controls. This comparison uses the maintained PostgreSQL service.

## Frozen inputs and population

Use the registered body-v2 input pin
`sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b`.
The catalog file has SHA-256
`f6583c871cdbeba09ae10517a5bbb6143a3e9bbf424289450d3fc808dc7a0d0e`.
Preserve the 24 task, plan, gold, scope and intent identities and the
original generation. The design follows exposure to the first admissible
calibration and its lexical diagnostic. That calibration remains primary
under [ADR 0206](../adr/2026/11/0206-proposed-w5-captured-text-calibration-protocol.md).
The replay may describe this workload and clarify its comparator.

Register arms `legacy_fts` and `public-lexical-comparator-v1` with 48 keyed
slots, one per task and arm. Order tasks by their registered ID in ascending
UTF-8 byte order. Run legacy first for odd one-based ordinals and candidate
first for even ordinals. Record the actual order, including skipped
executions, with no warm-up queries or automatic retries. A slot may
terminate at policy preflight without issuing a PostgreSQL query.

| Registered cases | Count | Candidate arm |
| --- | ---: | --- |
| Body positives | 12 | Plan the authored ranked request as `question`. |
| Absence and ambiguity controls | 4 | Plan the authored ranked request as `question`. |
| Metadata anchors | 4 | Run the authored legacy route as an unchanged control. |
| Privacy controls | 2 | Require the frozen evaluator refusal before planner and connection, with the enforcement layer recorded. |
| Literal and metadata mode controls | 2 | Preserve the legacy mode, fallback and capability handling. |

Use the existing authored `permitted_query_plan.query` as the sole source
of transformed text. The sixteen selected requests declare ranked terms,
ranked discovery and limit ten. An inert census confirmed printable ASCII
and a maximum of 100 bytes. The source preflight still has to prove planner
admission and at least one term for each selected request. No case, term or
input shape may be replaced after that freeze. Legacy passthrough cases bypass the new planner. The
planner accepts no fallback declaration.

## Execution and equivalence

Use the registered full-ID tasks and publication admission used by
attempt 5. Both arms use `execute_after_admission` from
`tools/retrieval_evaluation_admission_w5.py`. In an admitted callback,
prepare the authored request with `prepare_fts_request` from
`tools/retrieval_supplement_baselines.py` against the registered policy.
This is the FTS preparation path in the maintained body-study slot owner.
For a transformed slot, call
`plan_lexical_comparator(request, shape="question")` in
`tools/retrieval_lexical_comparator.py`. The historical runner retains its
original route. Use an admission callback that only prepares requests to
freeze both arms before any ranked slot. At execution, revalidate admission
and compare each canonical request with its frozen bytes before the service
call. Restoring the candidate's query text to
the legacy value must make the entire `LexicalQueryRequest` equal. This
checks style, requested mode, fallback, ordered bindings, both path scope
fields, limit and offset. Both transformed requests use `ranked_terms`,
no fallback, limit ten and offset zero. The service's ranked parser uses
`websearch_to_tsquery`, where the planner's uppercase ` OR ` is a
disjunction. Both arms retain the alias-vector branch owned by
`storage/lexical_query_sql.py`. Use the same service fences and timeout.

The W5 admission owner denies the whole selected scope if any selected
binding is denied, before request preparation or a service callback.
Apply the same capability, scope, omissions and file evidence handling
to both arms. For privacy refusals, validate the exact code and prove
that no planner, query connection or task-specific captured-body read
occurred. Attribute that refusal to evaluator admission.

Prepare one owned public PostgreSQL publication and lexical generation from
the registered-input factory. Reuse the PostgreSQL publication and lexical
build portion of `retrieval_body_study_pg.py` through a PostgreSQL-only
entrypoint. Preserve the factory-issued input, registered identity, current
policy and fresh workspace checks. Build with the admitted public bindings
and require a published result. The existing combined preparation keeps
its full SQLite and PostgreSQL behavior.
Bind the verified corpus, snapshot vector, class, source closure, full
projection policy and the accepted run, publication, snapshot and
lexical-generation IDs
before scheduling any query. Leave caller-supplied publication and snapshot
IDs unset on both replay service calls so the envelope cannot fill
missing observed IDs from caller scope. The SQL service's existing fence
remains in force. Require the actual backend
publication, snapshot, accepted run and lexical-generation IDs on each
successful query envelope and compare them with the frozen values. A missing
ID on an otherwise successful envelope is incomplete telemetry. Keep capture
and build closed for the replay. Include a generation-scoped candidate-file
census keyed by binding and relative path. The recorded expectation for
the common-positive universe is 22 files across two bindings. A valid
census difference closes as inconclusive before ranking. Broken identity
or policy evidence takes precedence as inadmissible. Join the preflight
and all actual query observations to that exact publication and generation.
Fence movement, policy drift, scope discrepancies or a changed closure
invalidate the comparison. An empty available result on a healthy accepted
generation remains an observation.

Prepare and freeze the 48-slot ledger, treatment fingerprint and all 12
common-positive memberships before ranking. Require all 12 across the
three registered families to pass structural preflight. Require the four
transformed absence and ambiguity requests to pass the same mode, style,
bounds and planner admission checks. A valid shortfall in either group
closes as inconclusive before any ranked execution. The existing published primary
study has its own eight-intent and two-per-family threshold. This replay's
fixed denominator keeps every planned contrast comparable. Any missing
observation after the freeze remains visible in its original slot.

## Descriptive analysis

Apply the maintained file-level nDCG@10, MRR@10 and recall@10 definitions.
Deduplicate the returned hit sequence by binding ID and source-relative
path in first-hit order. The service returns at most ten hits and no second
page is requested. Record the resulting count of distinct files. Grade
complete available observations and bounded pages with a complete top-hit
order and no other omission. Report the paired differences by task and
family. The mean weights the 12 body positives equally. A missing required
arm result leaves the aggregate unavailable. Use golds for grading only and
report empty available positive results as actual zeros.

Report metadata anchors, absence, ambiguity, literal/mode handling and
privacy separately. For absence, an available scope-complete zero is
correct, a nonempty comparable response is incorrect, and an incomplete
empty or unavailable result stays unresolved. Report both ambiguous golds
and their returned ranks when present. Keep unavailable, refused, stale,
failed, unscheduled and explicit not-applicable conditions distinct from
complete empty results. Report paired candidate recovery and loss and
application outcomes regardless of direction. Report measured service
elapsed time per slot with order and setup caveats. Alternating arm order
does not establish independent timing samples across case strata. Physical
PostgreSQL I/O and whole-operation latency remain unknown without direct
measurements.

Use the terminal precedence below.

1. `inadmissible` for identity or policy drift, forbidden execution,
   disclosure, unknown ownership, or a broken publication fence.
2. `incomplete_technical` for missing required telemetry or cleanup
   evidence, query failure, or a required unobserved slot after admission.
   Preserve a statement timeout on a healthy accepted generation as an
   observed treatment limit with unavailable metrics. That timeout leaves
   the comparison technically incomplete and supplies no cause for a retry.
   Bracketing reads provide generation context for a failed call. They
   supply no per-slot identity or ranking evidence.
3. `inconclusive_preflight` for a valid pre-query structural shortfall.
4. `complete_descriptive` for admissible complete observations, including
   negative results. Report incorrect control outcomes in that record.

The report uses paired lexical observations from this run and carries no
usefulness threshold. Historical aggregates stay in the primary report.
A fresh independently designed study
would require its own candidate treatment, inputs, controls and criterion
before its first result exposure.

## Proposed implementation and execution admission

Use one new closed lexical-only class in the existing owned sandbox with a
fresh one-use reservation. Retain the existing core PostgreSQL runtime and
read-only SDK identity, resource policy, default statement timeouts
(2 seconds query and 30 seconds build), and path/identity fences. Budget at
most one SDK start, one core PostgreSQL lifecycle, one publication, one
lexical generation build, and 48 service calls for one admitted replay.
Allow two additional read-only protocol queries, one before ranked execution
to bind the accepted identity and census and one at exit to reconcile them.
Include service setup, publication and cleanup commands in the separately
bounded schedule. The retained core sandbox and runtime artifacts must be available without
builds, pulls or installation. Use the existing offline path only after its
lexical-only profile and ownership receipts are proven.

Before execution, give the independent reviewer the exact command schedule,
retained runtime identity, source fingerprints, ownership route, and
derived finite limits for time, scratch, reads and transfers under the
unchanged core envelope. If the exact plan cannot fit, return the failed
preflight and its limiting factor. No automatic fallback to the online
runtime path, native model workers, pgvector, a live database, or a default
test population. Conditional evaluated execution follows independent
source and preflight acceptance under the operator-selected class.

Prove closed admission and forbidden input refusal with exact scoped
unit cases. Prove request equivalence, policy-first behavior, paired capture,
partial-page grading, stale-generation refusal, interrupted ledger and
owned cleanup with the maintained disposable synthetic integration harness.
Use new synthetic witnesses for proof. Keep the evaluated driver outside
default test discovery and staging. The standard scoped unit and containerized
integration selectors run with coverage disabled.

Preserve every attempt and provisional slot privately until integrity and
cleanup are verified. Routine recovery follows ADR 0200. A valid completed
negative remains the secondary observation. After partial result exposure,
a technical retry requires a rank-blind objective-defect review and
byte-identical treatment. Any treatment change returns to the operator.
Scientific acceptance, held-outs, and production qualification remain
separately owned.
