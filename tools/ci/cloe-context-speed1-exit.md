# CLOE-CONTEXT-SPEED1 Compact Context Exit

Compact-context admission now projects the fields affected by reservation
and measures the exact UTF-8 size with one JSON encode per reservation.
It computes the decimal width of the self-referential byte ledger before
producing the final encoded packet. The existing packet schema and
byte limit still apply.

Omission detail records use the space left after ranked items, neighbors,
and relations. A tight packet preserves an admissible seed and emits the
fitting prefix of optional details while the totals cover every omission,
as specified by [rank-prefix admission](../../docs/specs/cloe-intent-context-v1.md#42-rank-prefix-admission-seeds_then_neighbors_v1-_destination-deduplication-and-stored-relations).

## Scoped verification

The optimization preserved reserved sizes and final bytes in 180
deterministic packet shapes, and emitted identical outputs in 70
admission workloads. Full JSON encodes in those workloads fell from
4,693 to 1,593 for the optimization alone. The combined change used
1,602 encodes and retained more ranked items in three workloads. All
70 packets met their byte limits. A 4 KiB omission fixture emitted 19
of 32 detail records and retained the exact aggregate total.

- 48 scoped unit cases passed across the compact-context budget and assembly owners.
- 22 containerized integration cases passed across SQLite Local and PostgreSQL,
  including CLI byte-ledger readback, freshness bounds, and the strict
  rank prefix for a multibyte packet.
- Scoped tests used `--no-coverage`. The repository static profiles
  and tracked-file length checks passed.

The budget optimization used the dogfood dispatcher for implementation,
independent source review, and review amendments. The parent corrected
ranked admission and verified the assembled source, including the post-review
amendments, against the incumbent and the scoped runtime tests.
This is a local checkpoint for the upcoming batch. Hosted coverage and
exact-candidate hosted qualification remain pending.
