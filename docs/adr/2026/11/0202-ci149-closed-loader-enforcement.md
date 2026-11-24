# ADR 0202: CI149 Closed Loader Enforcement

## Status and authority

Accepted before implementation for the operator's CI149 remediation request
under ADR0200's standing routine allocation. One exact staging successor is
authorized after scoped proof and independent APGR review. Main and releases
remain unchanged. No new runtime dependency or runner-policy class is selected.

## Context

CI149 passed eight jobs, including unit, staging integration, application system
and native Rust qualification. Static root profiles passed, but full retention
enforcement left 34 files unenforced across six cohorts. Three W5 test helpers
use isolated file loaders without closed declarations. Six prior declarations
for the integration guard tests bind a stale conftest target. Dependency closure
propagates these blockers; changed-file profiles alone cannot prove admission.

## Decision

Extend the existing file-loader-spec recipe only for the three exact W5 loader
functions. Retain complete inert owner AST contracts, exact function contracts,
runtime aliases, three-operation chains, target source hashes and maintained
unit-test dependencies. Isolated module loading remains unchanged. Every added
edge remains a real enforcement obligation, including the integration conftest
and native test owner. No generic dynamic-import allowance or governance credit
is introduced.

Append fifteen records: nine for the three W5 chains and six successors for the
existing guard chains. Preserve every historical record. Add an isolation-name
assertion to the existing inspection test so the successor has fresh owner/test
evidence, and update its maintained owner/function AST contracts. Supersession
requires changed commitments and never edits an old row in place.

Retain all cohort scopes, baseline findings, debt ceilings, exclusions, source
custody and history rules. New inert contract modules receive ordinary clean
tool ownership and dependency enforcement. Do not replace isolated loader tests
with imports of live pytest plugins.

## Proof and publication

Exercise each exact loader and reject alias, target, surrounding-context and
chain drift. Run the affected unit owners and containerized integration owners
with exact selectors and no coverage. Run complete static inventory/cohort
enforcement, retaining raw findings and recording any tool-environment gap
separately. Complete local runtime suites and coverage remain unselected.

Preserve CI149 as a failure. Carry its observed application, qualifier and wheel
spends forward, reserve one exact CI150 synchronize campaign on PR27 with a
fresh nonce, and bind the current grant and Rust source manifest. Fresh remote
state, independent review, an atomic authored commit and an exact local report
precede the single staging push. No manual duplicate or PR-state churn follows.

## Alternatives and rollback

Changing only lint selection misses the failing aggregate. Ignoring dynamic
uncertainty, replacing an old declaration or sharing live conftest imports would
weaken enforcement or change plugin isolation. Repair forward with immutable
failure evidence; never rewrite the published candidate or reset spent counts.
