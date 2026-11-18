# ADR 0208: Body-study low-level custody module boundary

## Status

Accepted implementation decision within operator-selected ADR0206 Route A.
Scoped proof, independent source review and native admission remain pending.

## Date

2026-11-18

## Context

The new body runner's low-level plan, reservation and read-accounting owners
imported helpers from broader evaluator cohorts. The retention dependency graph
therefore joined runner code to evaluator test support, which returns to the
runner through coverage infrastructure. Clean lint and type checks do not
satisfy the separate dependency-enforcement gate.

The existing cohorts, source freeze, worker closure and retention rules must
remain intact. Disabling cycle detection or declaring an additional dynamic
import would conceal this coupling.

## Decision

Keep BODY plan, source-file reading and durable accounting independent of
evaluator imports. Put BODY directory-descriptor file I/O in a leaf module using
only the standard library and BODY contracts. Preserve bounded regular-file
reads, no-follow traversal of every directory component, descriptor/path
identity checks, digest checks, exclusive publication, fsync and exact temporary
cleanup. Retain existing `io:*` refusal codes in the BODY error family. Existing
supplement helpers and cohorts remain unchanged.

Separate fresh worker-closure validation from the low-level source-closure
module. The high-level BODY worker owner continues to call the maintained
public-text worker helper and use its official 17-file `worker` identity domain.
It checks the complete filename population against the BODY bookkeeping list
before accepting the returned closure. That fixed leaf list supplies only read
reservations; it does not authenticate bytes or admit a worker. Actual authority
still requires fresh maintained helper reads and the fixed worker identity.

The BODY source closure includes both new helper owners. Every actual source,
treatment, input, worker, ownership and cleanup check remains required. No
freshness result is cached. The original worker files and frozen body inputs
are not edited.

New BODY regression cases belong to BODY test owners. Preserve the exact legacy
loader owner and its six existing dynamic declarations rather than changing
their closed owner-context contract to accommodate a new BODY test.

## Verification and boundaries

Prove exact worker-name and identity correspondence, mismatched-population
refusal, real file custody and exclusive publication, and unchanged legacy
loader bytes/declarations. Run fresh scoped unit and containerized integration
selectors plus complete static enforcement. Recompute the finite byte forecast
before native admission and require independent source acceptance.

This is an internal implementation boundary of the already-selected BODY
class. It selects no dependency, runtime provider, scientific criterion, input
class, held-out, production behavior or public product contract. Existing caps,
retention profiles, debt, cohort memberships and public CI gates stay unchanged.
The new source/test owners receive additive zero-debt entries only.

## Alternatives and rollback

Central runner callback changes alone do not break the mixed-root circuit.
Broad cohort reassignment, checker exceptions and undeclared dynamic imports
are rejected. If the leaf implementation does not preserve custody or fails
independent review, retain the refusal evidence and correct the source before
any native execution; no automatic weaker fallback is allowed.
