# ADR 0200: Standing execution allocation and initial milestone CI admission

## Status

Accepted for implementation under the operator's 2026-11-15 instruction,
“allocation: infinite”, and the standing request to advance the accepted
roadmap through substantial public-staging milestones. Publication still
requires exact source/proof, independent review and current event binding.

## Context

The public calibration supplement is committed locally. ADR0199's exact
Trixie/PGDG provider passed scoped proof and disposable native ARM qualification.
Together these are a substantial connected engineering milestone; their
publication does not claim that the prospective supplement study has run.

PR23 was merged for v0.0.4. Public main and staging are both at that released
commit, so the next milestone needs a fresh draft staging-to-main PR. The old
CI146 grant admits only a synchronize event on PR23, requires before/base to
differ, and has spent its nonce. Reusing it, opening an intentionally failing
bootstrap campaign, or fabricating synchronize fields would be incorrect.

## Decision

Routine execution allocations for the accepted roadmap have no operator-set
cumulative numerical ceiling. Preserve every historical counter, failure,
spent nonce and source binding. Each real candidate still receives a concrete
finite, one-use reservation and a recorded actual result. A failed campaign
is analyzed and corrected before a new reservation; unlimited allocation is
not permission to replay an unchanged failure or run intermediate public CI.

Keep per-operation time, memory, byte, privacy, isolation and cleanup limits.
Keep local tests scoped with coverage disabled. Complete local populations,
new dependency or data classes, held-out protocols, scientific acceptance,
production admission, public-main promotion and releases retain their separate
authority. One local dispatcher runs at a time using dynamic_default; standing
allocation does not authorize interrupting an active dispatcher.

### Initial PR event proof

Introduce version2 of the existing closed system-build-grant and admission
proof schemas. Their fields remain closed, but initial publication explicitly
admits the selected `opened` event instead of pretending it was synchronize.
For this event:

- The frozen before baseline equals the approved base commit. It denotes the
  initial-publication baseline, not an observed event.before field.
- Actual `before` and `after` keys are absent. Their presence is refused,
  including null values; a malformed synchronize event cannot be relabeled.
- The actual head comes from the PR head block. It differs from the baseline.
- Repository, non-fork origin, draft=true, staging/main refs, exact PR number,
  base SHA, workflow path/ordinal/attempt, native host and real two-parent merge
  identity retain their existing strict checks.
- The proof's before is the declared baseline; after is the verified PR head.
  Its raw-event hash retains the actual opened payload. The schema version
  makes this meaning distinct from the historical synchronize proof.

Retain strict synchronize transition validation for a separately selected
future campaign. Do not broaden the current grant to accept arbitrary event
types, PRs, branches, ordinals, sources, retries or platforms. Refuse every
spent or superseded nonce/reservation, including CI146. Keep version1 records
as immutable history rather than accepting them as version2 evidence.

The next observed workflow ordinal is 148; the next observed PR number is27.
These are forecasts, not reserved GitHub identities. Recheck both immediately
before publication. A concurrent event or PR creation fails closed and requires
a fresh binding under standing allocation; it does not relax identity checks.

### Concrete next campaign

Select one ordinary existing release-qualification campaign after review.
Previous actual application/qualifier/wheel counts are 54/26/54; the unrelated
Dependabot CI147 stopped at source policy and spent none of them. This candidate
reserves one application start, one qualifier entry and two unchanged pinned
wheels: cumulative bounds55/27/56. Actual component runtime 25 is a separate
counter. Keep the existing builders, parser packages, SDK recipe, production
dependency closure and all hard quality gates.

Refresh the exact source/context and Rust-manifest closure, including the small
new internal event-validation owner. Do not substitute parser files or wheel
bytes. Create one fresh draft staging-to-main PR after the final staging push;
its opening event triggers the normal pipeline. No manual duplicate dispatch,
main merge, tag or release is selected.

## Alternatives and rollback

The status quo cannot admit the required fresh PR. A disposable bootstrap
campaign spends feedback on a deliberately invalid grant. PR/base manipulation
or trigger changes would obscure the real publication event. A strict initial
event proof preserves the existing authority boundary with explicit semantics.

Before publication, a failed implementation remains local. After publication,
retain the result and select a corrected candidate with a new reservation;
never rewrite a reported commit or reactivate CI146. Production behavior and
the qualified provider image remain unchanged.

## Required evidence

Scoped unit and real containerized admission owners cover valid initial and
synchronize transitions, missing/forged fields, booleans as integers, wrong PR,
base/ref/repository/action/ordinal/attempt/host/merge parents, old schema and
spent identity refusal. The original gate and per-campaign resource checks
remain. Independent resulting-state review precedes publication.

The existing 85/85 unit and80/80 integration hard floors are unchanged. Desired
87/82 margins remain targets. Add useful portable-publication boundary coverage
while preparing the milestone; only the hosted run measures the final coverage.
Native ARM evidence remains a bounded local diagnostic, not cross-platform,
scientific, production or full W5 acceptance.
