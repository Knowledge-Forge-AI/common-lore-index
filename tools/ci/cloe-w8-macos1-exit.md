# CLOE-W8-MACOS1 Native Apple Silicon Local Install Exit

The attended W8 check ran the frozen offline kit against the exact CI162
staging source `d5ab23730ae306a1c259c45af3f59b18bf492725`. The native
host was Darwin arm64 with CPython 3.13.12. The archive and all pinned
members matched before installation.

| Evidence | SHA-256 |
| --- | --- |
| Kit archive | `79827abd0a72dfd0445f8055b066ccdca719ce39c80236ba8e1e633edc61aa7a` |
| Kit manifest | `d218c0d07d97994ef83a879c59dc850813f30fc54756ed6c3cdb3a78525c1c76` |
| Product wheel | `6cdb68d2a24feabc1f65f7af177cdc0538ddfda79ee299f0b5350f734539fd1b` |

The proof ran under a process sandbox that denied networking and confined
writes to its disposable workspace. Child probes confirmed denial of IPv4
and IPv6 socket binds. Networking on the host was unchanged. The wheel
installed offline in a fresh venv with the locked Local dependency and
without Psycopg.

All 21 steps and 13 checks matched their expected outcomes. The kit published
SQLite generation 1, measured equal hashes for the backup and restored
database files, and received matching MCP replies with the source hidden.
The deliberate PostgreSQL invocation returned the missing-driver refusal.
The kit removed its owned work directory.

The observation covers a fresh SQLite Local install. Native upgrades,
Windows, Server, service activation and package-index delivery are separate
release gates. The [Linux control](cloe-w8-platform1-exit.md) and
[CI162](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44674001015)
remain evidence for their respective scopes.

Repository changes are documentation-only. Unit, integration, smoke,
staging, system, compileall and coverage were unselected. This checkpoint
adds no remote write or workflow trigger.
