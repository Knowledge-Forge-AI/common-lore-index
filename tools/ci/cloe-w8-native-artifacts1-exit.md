# CLOE-W8-NATIVE-ARTIFACTS1 Offline Apple Silicon Installed Exit

The local source commit `e6809da11f9bd627ec5f255af4005bb65d3b7ace` produced an offline wheel from
872 exact package inputs. The wheel SHA-256 was
`fef9c3047f2247885814b3ed3b74d43e5b1ab582608776d5c12519deab033fd2`. The installed filesystem verifier matched
the committed module byte for byte.

On Darwin arm64 with CPython 3.13.12, the candidate replaced
released v0.0.4 in the same disposable virtual environment. The original
SQLite generation and source-blind MCP output were preserved. A subsequent
source edit published generation 2 and exposed the added symbol. Both
wheel versions restored the original backup and reproduced its MCP output.
The backup database and manifest kept their original hashes.

The installed verifier accepted a 6,144,000-byte artifact at
its exact limit, refused a smaller limit and rejected a same-size damaged
object. Peak traced Python allocations during the successful verification
were 71,637 bytes.

Child IPv4 and IPv6 bind probes and two out-of-workspace write probes were
denied by the local sandbox. The rehearsal removed its owned workspace.
In a fresh environment, pip resolved the locked dependency from the offline
wheel directory. Both hashes in its install report matched the input pins.
With an empty directory, pip refused before installing either package.

The runtime report binds the wheel to the named code commit. Scoped Linux
unit, integration and installed proofs for the same code are in the
[bounded artifact and digest exit](cloe-digest-fanin1-exit.md).
Package-channel delivery and hosted qualification remain pending.
