# CLOE-W8-MACOSUP1 Native Local Upgrade and Offline Index Exit

These checks extend the [fresh-install proof](cloe-w8-macos1-exit.md) for the
CI162 Local wheel. The observed platform was Darwin arm64 with CPython
3.13.12. Both processes ran under a sandbox that denied network access
and writes outside the disposable workspace. Child IPv4 and IPv6 bind
probes confirmed the network boundary. Both workspaces were removed.

## In-place installed upgrade

The pinned Linux arm64 SDK rebuilt the released wheel offline from 872
package inputs at `39cb2b3452bfd32b05d38f842c0f891614cbf58d`. Its wheel
SHA-256 was `ba5647fd96caef995586399c15c8503020137aef127b68254dcc19e0a57ca184`.
The candidate came from the reviewed CI162 kit and had wheel SHA-256
`6cdb68d2a24feabc1f65f7af177cdc0538ddfda79ee299f0b5350f734539fd1b`.
The Linux control and the native run both passed.

The release initialized and published SQLite generation 1, then created
a backup. Replacing the wheel in place changed the installed parser
member to the candidate's exact bytes. Its source-blind MCP readback
matched the release result. The read left the graph database and
configuration bytes intact. A source edit advanced the candidate to
generation 2 and the added symbol appeared in MCP results.

Both wheel versions separately restored the original backup into empty
homes. Their restored database hashes matched the backup file, and
source-blind MCP results matched the original. The retained backup file
and manifest stayed byte-identical across replacement and recovery.

## Package-name install from a local index

Pip resolved the candidate by distribution name and the existing
`typing-extensions` dependency transitively in a fresh native venv.
Its install report bound both wheel hashes to files under the temporary
index. The installed package published SQLite generation 1 and answered
MCP after the source was hidden. Pip refused a modified wheel and an
incompatible `Requires-Python` declaration before installing product
or dependency bytes. The index used normalized project pages and
hash-bearing file links. [Simple Repository API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)

This evidence covers the two offline Local workflows. Package-index
publication, installed Server, Windows and service activation remain
separate release gates.

Repository changes are documentation-only. Complete and scoped unit,
integration, smoke, staging, system, compileall and coverage were
unselected. This checkpoint adds no remote write or workflow trigger.
