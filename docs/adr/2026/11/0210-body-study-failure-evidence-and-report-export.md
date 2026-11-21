# ADR 0210: Body-study failure evidence and report export

## Status

Accepted implementation correction within selected ADR0206 Route A.
Fresh proof and independent source acceptance remain required before execution.

## Date

2026-11-18

## Context

Cleanup and technical failure must not erase observations already exposed during
the BODY study. Hash-only journal events cannot supply the original envelopes,
snapshot-bearing hits and grades needed for a rank-blind cause review. SDK
cleanup also cannot prove the absence of independently owned model siblings.
The BODY report path must be inside the canonical sandbox exporter root.

## Decision

Persist bounded sealed original observations before appending their journal
hashes. Retain the original event chain, sealed record identities and explicit
failure states. Failure envelopes remain distinct from the strict complete
actual-report schema. They preserve actual worker and slot counts, unknown
values and cleanup state; they claim neither origin nor scientific acceptance.
Do not synthesize missing rows, worker receipts, query execution or completion.
A preparation-derived refusal retains decision evidence and its typed outcome
without claiming that the corresponding query ran.

On every host terminal, inspect and bind available exported normal reports,
failure envelopes, journal events and original sealed records. Validate exact
plan, attempt, source, input and observation joins. Record explicit unavailable
or partial evidence when bytes are absent or validation fails. Bind raw report
and failure hashes plus the complete journal commitment into the consumed
reservation before settlement. Avoid circular terminal/report hashes. Failure
retention cannot satisfy a scientific or integrity acceptance gate.

Join every actual model sibling's cleanup with SDK/process-group cleanup. A
failed, uncertain or unverified worker cleanup blocks whole-attempt proof.
Keep actual known counts and uncertain upper bounds distinct. Cleanup runs in
finally paths and preserves primary and cleanup errors. The private wrapper
recovers a plan-bound host terminal after an exception; missing post-launch
cleanup evidence is unknown, never not-started.

Place BODY reports, journals, sealed originals and read-counter exports under
`/sandbox-report/report`, the unchanged canonical exporter root. The plan's
BODY subdirectory is `/sandbox-report/report/body-study`. Preserve the general
exporter's existing 72 MiB archive, 64 MiB content and member limits. Forecast
that actual bounded envelope rather than assuming a 4 MiB export. Charge all
new bookkeeping and readback inside the existing 12 GiB controlled-read cap;
no attempt, transfer, scratch or runtime allowance is enlarged.

## Verification and boundaries

Prove pre/post-launch failures, mixed SDK/worker cleanup, creation uncertainty,
BaseException cleanup, altered or absent observations, journal/report mismatch,
strict failure-envelope non-acceptance and canonical export correspondence.
Run scoped unit and containerized integration owners, static enforcement and a
fresh finite forecast. Independent review must accept source before execution.
All historical attempts and failed proofs remain immutable. No old cohort,
source foundation, worker closure, criterion, held-out, production behavior,
main promotion or release changes. If evidence or ownership is unknown, preserve
the failure and apply ADR0206 disposition rules rather than choosing a rescue.
