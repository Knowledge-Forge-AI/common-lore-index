# ADR 0209: Body-study measured registered-input snapshots

## Status

Accepted implementation decision within operator-selected ADR0206 Route A.
Corrective proof, independent source acceptance and actual execution remain pending.

## Date

2026-11-18

## Context

The BODY forecast reserves 201 registered-input snapshots for the canonical SDK
path. The driver previously reported a call-site sum. Registered input properties
perform their own fresh snapshots, so an extra property access could exceed that
sum without a durable reservation or refusal. An arithmetic declaration is not
measured evidence. The frozen registered-input API and input bytes remain intact.

## Decision

Observe calls to the exact maintained registered-input snapshot code object in
the BODY SDK attempt. Bind the existing 201-call reservation to the plan and
count each actual invocation before input I/O. Refuse invocation 202. Keep the
reserved ceiling and measured calls separate. Require normal scope completion,
restored profiling and the durable final receipt before a passing budget export.
A killed process or missing receipt remains unknown and cannot establish proof.
Host-side registered checks retain their separate existing accounting.

Use a BODY-scoped CPython profile observer without replacing or caching the
registered-input implementation. Refuse an existing current-thread or default
thread profiler rather than disabling unknown instrumentation. Restore prior
profile slots after the BODY scope. A narrowly scoped audit backstop rejects
input opens under the exact snapshot code when its reservation is absent,
profiling is lost, the owner thread is wrong, or a previous violation occurred.
This remains active even if a caught observer exception has disabled profiling.
The process audit hook is inert outside the explicit BODY context; it grants no
execution authority and inspects code identity rather than payload values.

The counter remains a low-level leaf: the high-level driver supplies the trusted
code object and plan identity. Ledger writes alone receive a narrow recursion
exemption. Refusals, incomplete invocations and cleanup failures cannot be
converted into a clean measured receipt. Failure evidence remains non-passing
and retains the original observations, actual counters and primary error.

## Verification and boundaries

Prove the exact ceiling, measured entry before I/O, extra property access,
caught-limit attempts, foreign threads, existing profilers, hook loss and exact
restoration. Run the scoped unit and containerized integration owners, static
checks and a fresh finite byte forecast. Independent review must accept the
complete implementation before native execution. Bookkeeping overhead is charged
inside the existing read cap; this decision increases no budget.

A callback inherited by a thread remains inert after the explicit scope closes.
Restoration covers the owner thread and default thread profile slots, without
claiming control over unknown pre-existing per-thread profilers.

No dependency, provider, input class, scientific criterion, held-out or production
behavior changes. The original source foundation, input pin and worker closure
remain unchanged. No existing cohort receives the observer. Public CI gates and
retention debt ceilings remain intact. Adoption is source work, not native or
scientific acceptance.

## Alternatives and rollback

A call-site sum cannot detect an additional real snapshot. Caching input results,
editing frozen APIs, weakening freshness checks or raising the ceiling would
change the required proof. These alternatives are rejected. If instrumentation
cannot establish fail-closed counting and restoration, retain the failure and
correct it before the actual study; do not bypass the gate.
