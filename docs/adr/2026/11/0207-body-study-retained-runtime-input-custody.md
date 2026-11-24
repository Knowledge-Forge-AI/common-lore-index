# ADR 0207: Read-only retained runtime input for the body study

## Status

Accepted implementation decision within the operator-selected ADR0206 Route A.
Source, native execution and public qualification gates remain required.

## Date

2026-11-18

## Context

ADR0206 permits reuse of the exact retained Debian Trixie/PGDG runtime archive.
The initial BODY launcher copied that archive into each attempt before hashing
and streaming it into its disposable SDK. The additional host copy consumed a
full archive read, a transfer and temporary storage without changing the input
or adding execution isolation. The read forecast also initially omitted some
archive and artifact reservations; those deficient forecasts remain evidence
and cannot admit execution.

The approved archive, 12 GiB controlled-read cap, 6 GiB transfer cap and 8 GiB
scratch cap remain fixed. Source freshness cannot be replaced with cached
validation to obtain a passing forecast.

## Decision

The BODY class borrows the existing retained archive as a read-only input.
Its host path may differ from the plan directory. It must be an absolute,
owned, private regular file without aliases, with the exact size and SHA-256
already selected by ADR0206. This exception applies only to the archive input;
the allocation, reservation and admission records remain local to the attempt.
Other offline cohorts retain their existing locality rules.

The host hashes the archive before consuming execution admission or launching
an SDK. The sender freshly hashes its streamed bytes, the SDK receiver verifies
the same size/hash, and the imported image must match the retained configuration,
ordered layers, platform and provider. Neither preparation nor cleanup writes,
renames or deletes the borrowed host input. A changed or unavailable input
refuses execution; there is no implicit copy, acquisition or fallback.

The temporary `image.tar` received inside the SDK remains attempt-owned. Only
that copy and independently attested attempt resources may be removed. The
previous phase retains custody of its original archive and evidence.

## Accounting

The forecast includes four archive passes: host verification, sender read,
receiver read and Docker load. It preserves separate artifact-bank, source,
registered-input, Git-custody, response and report reservations. These are
conservative controlled-I/O bounds, not measured physical operating-system or
PostgreSQL I/O.

Scratch accounting names the SDK's 4 GiB Docker store, 2 GiB scratch tmpfs,
64 MiB run tmpfs and 64 MiB shared-memory tmpfs; PostgreSQL's 256 MiB data,
32 MiB temporary and 64 MiB shared-memory tmpfs; one sequential model worker's
768 MiB scratch, 64 MiB temporary and 64 MiB shared-memory tmpfs; and 256 MiB
for host/SDK outputs. The retained host input is existing input storage, not
new attempt scratch. The source-derived totals must pass before admission.

The BODY whole-checkout ceiling retains two spare traversals beyond its
14-call valid-path map. Every actual traversal still reserves a durable slot
before reading. Closure guards, ownership checks and all semantic deadlines
remain mandatory.

## Verification and boundaries

Scoped tests must prove nonlocal read-only input custody, refusal of aliased,
wrong-size or changed inputs, unchanged locality for control records, exact
accounting and preservation of the borrowed file. Native proof still requires
fresh source review, complete scoped proof, artifact admission, one-use
reservation, actual ownership and cleanup evidence.

This changes delivery inside the approved BODY class. It does not change model
bytes, questions, golds, ranking, criteria, treatment, runtime providers,
production behavior, old cohorts, standard public CI jobs or release authority.
Held-outs, final W5 acceptance and production admission remain separate.

## Alternatives and rollback

Keeping a host copy adds avoidable I/O and scratch. Increasing a cap, omitting
verification or accepting a stale source check is rejected. A rollback requires
a reviewed source correction and a complete fresh forecast within the same
approved bounds; a failed borrow does not trigger an automatic fallback.
