# ADR 0222: Canonical-only v0.1.0 source cutover and reversible conversion

## Status

The operator approved the canonical-only source cutover on 2026-11-24,
including consumer updates and disposable conversion and recovery proof.
Live activation remains a separate operation after Checkpoint A with a
verified recovery plan.

## Context

[ADR 0123](../10/0123-coordinated-cloe-package-and-namespace-cutover.md)
selected finite legacy wrappers and home/launch compatibility. The
v0.0.5 preview still includes the legacy console script, six Python facade
files and runtime fallbacks. An installed predecessor and its clients may
depend on them. Live recovery needs the coherent predecessor environment
and a tested backup of its state.

## Decision

Use Common Lore Index, `common-lore-index`, `common_lore_index`, `cloe`
and the canonical full-name console entry. Use `CLOE_HOME` and `~/.cloe`,
`CLOE_*` public selectors, `*.cloe.toml` and `*.local.cloe.toml` for
configuration, and `cloe_*` for renamed first-party MCP tools. Adopt
`common_lore_index_test_support` as the support namespace after exact
ownership and collection reconciliation. Update current clients and
installed validation together. This supersedes the legacy interface
promises in ADRs 0109 and 0123 for v0.1.0.

Classify legacy spellings by active interface, one-time conversion input,
immutable identity or historical evidence, and negative test or third-party
attribution. Analyze first-party Go naming and current tool/CI artifacts
by consumer. Transition only with the actual module path and evidence
paths. Historical hashes, applied migrations, graph/source identities,
closed receipts and release artifacts keep their original representation.
Use forward-only migration where a selected current state identifier truly
must change.

Separate the candidate source from deployment. Rehearse the supported
installation and the one-time conversion in an owned disposable location.
Use the existing migration journal and backup owners wherever they cover
the selected boundary. Demonstrate source binding, effective overlay order,
relative path meaning, data equality, obsolete entrypoint absence,
canonical source-blind MCP, and rollback before Checkpoint A. Begin a live service
operation only after that candidate qualifies. A live plan identifies the exact package, runtime owner,
clients, writer quiescence, verified backup and treatment of later writes.

## Verification and rollback

Reconcile the measured and abrupt populations, maintained-source coverage
denominator, type/length/debt owners, import/runtime isolation and existing
security checks after any path changes. Compare candidate and predecessor
in independent clean environments. Rollback reinstates the predecessor
environment only against its compatible retained state with later writes
accounted for. Do not repair a failed cutover by reintroducing wrappers.
