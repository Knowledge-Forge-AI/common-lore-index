# ADR 0220: Remove the unit runner's free-disk admission check

## Status

Accepted by the operator for the unit suite.

## Context

The host free-disk percentage blocked scoped unit tests before collection.
The unit runner already owns and measures its scratch use through quotas and
retains custody through teardown.

## Decision

The canonical runner selects the exception from the parsed unit suite.
Unit runs skip free-disk sampling and both reserve thresholds. Scratch
filesystem identity, admission records, quotas, and cleanup still apply.
Integration, smoke, staging, system, and direct resource-run callers retain
their existing disk rules.

## Verification and rollback

Exercise admission with absent disk samples for the unit suite and verify that
the same exception is unavailable to the other runner suites. Run the scoped
projection tests on the host that previously refused admission. Revert this
decision and the matching runner changes to restore the former unit reserve.
