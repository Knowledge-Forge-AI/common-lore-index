# ADR 0218: W5 prospective matched held-out comparison

## Status

Operator selected the matched held-out design for W5 on 2026-11-21.
This phase prepares the protocol and inert source.

## Context

[ADR 0206](0206-proposed-w5-captured-text-calibration-protocol.md)
qualified a descriptive calibration signal for captured-text fusion.
The historical PostgreSQL FTS baseline found zero files for the 12 body
positives. The candidate in [ADR 0213](0213-proposed-w5-lexical-replay.md)
reached mean recall@10 of 0.9167 on the same exposed tasks. That replay
compared two lexical arms. The paired benefit of fusion over the stronger
lexical comparator on independent tasks is still unknown.

## Decision

Keep matched held-out evidence as a W5 requirement for v0.1.0. Use
[the prospective protocol](../../../specs/cloe-w5-matched-heldout-v1.md)
for a fresh public captured-text cohort. It compares native tools, the
ADR 0212 lexical comparator and the exact incumbent public-text-v2 fusion
against common task and file populations. Freeze the protocol before case
selection. Require an independently reviewed, exact sealed cohort and
runtime envelope before any study execution.

Other available choices were to defer the usefulness requirement or extend
the exposed replay. Deferral leaves the release criterion unresolved.
Replaying the existing cases measures behavior on familiar inputs and
cannot establish independent performance. Replacing fusion's internal
lexical component would evaluate a different candidate and would need its
own prospective selection.

## Consequences

The historical calibration and replay retain their original meaning. The
original held-out cohorts keep their custody and reservations. Separate
the paired quality signal, control behavior and resource costs. The design
phase has no runtime effects. A later positive signal addresses only the
bounded comparison. Incorrect absence remains an explicit acceptance gap.
Release and production defaults require separate decisions.
