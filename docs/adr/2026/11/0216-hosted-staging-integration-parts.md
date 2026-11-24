# ADR 0216 — Run hosted staging integration in concurrent jobs

## Status

Accepted for implementation by the operator's CI159 follow-up. The next
publication must also include the selected Server resource report milestone.

## Context

CI159 passed eight jobs. Its integration job reached the repository's
240-minute envelope during the staging command. The complete measured
integration population and intentional abrupt behaviors must still qualify
the candidate. [CI159](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44540555733)

## Decision

Run the canonical smoke path first. Upon success, run three measured
integration parts and one abrupt part on separate GitHub-hosted runners.
Each part uses the existing disposable sandbox and ordinary serial pytest
execution. Each integration part independently seals the full discovery
population and reconciles its live collection with that seal. Assign each
measured test file to a fixed part by hashing its repository-relative path.
Keep all intentional abrupt cases together. An empty required part fails.

The stable `staging-integration-gate` check verifies results from smoke and
every part. Require exact same-run, attempt, commit and source identity,
identical ordered full populations, disjoint exact selection, exit and
terminal cleanup success. Bind aggregate evidence to a shared invocation ID.
Settle a missing, canceled, refused or failed worker as a failed aggregate.
Use distinct part artifact names scoped to run and attempt. Refuse malformed,
missing or extra evidence.

Combine measured Python data by union over the complete source denominator
and measure Go statements over the combined original block identities. Apply
the existing 80 percent Python statement and branch floors and the 80 percent
Go statement floor to that aggregate. Abrupt coverage never enters measured
coverage. Emit the existing canonical staging report plus the part receipt
closure. The legacy local monolithic command keeps its existing behavior.
Carry the first-use and installed-experience report trees through the
measured receipts to the canonical artifact.

The aggregate downloads same-run artifacts with the MIT-licensed GitHub
`actions/download-artifact` v8.0.2 at the exact upstream commit
`9000827ccba6bdab643e8b6fd33ac0654aef8333`. Existing read-only checkout
permissions apply. Attempt-specific names and closed receipts constrain
the download and consumption paths. A missing or refused action result
fails the stable check. [GitHub artifact transfer](https://docs.github.com/en/actions/tutorials/store-and-share-data)
· [Action release](https://github.com/actions/download-artifact/releases/tag/v8.0.2)

## Alternatives

A longer single-job timeout leaves elapsed time dominated by one runner.
Concurrent pytest workers in the same sandbox would share database and
resource state. Averaging shard percentages loses branch and denominator
identity. Independent sandboxes preserve the existing isolation model.

## Acceptance

Exercise missing, repeated, cross-attempt, drifted and failed parts,
coverage union and floor refusal through scoped tests. A local scoped run
remains diagnostic. The next hosted run supplies wall-clock and full-population
evidence.
