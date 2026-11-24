# CLOE-W7-INTERACTIVE1 Resource and Installed Readback Exit

The Local restore validates the backup database length before hashing.
Verification and the install copy refuse growth after opening the file.
The copy cannot write more bytes than the manifest admits. Digest,
SQLite integrity, publication readback and no-clobber installation still
govern success.

Source capture reads at most the size reported by the open descriptor
plus one byte per attempt. It reuses the existing three attempts and
source-change refusal. A stable retry preserves the exact staged bytes.
Repeated growth leaves no staged copy, and cancellation precedence is
covered by the original capture owners.

Structural-digest preparation streams its remaining spill runs directly
when they fit the existing merge fan-in. Six workloads matched the
incumbent's exact row-stream digests. The 4 MiB-default workload wrote
21,150,564 bytes instead of 42,301,128. Its final merge removed one
temporary run write. Cases above the fan-in retain the established
consolidation path. With a configured fan-in of two or three, that
existing fallback can leave up to four final inputs. The direct path
holds at most the configured fan-in. The scoped prepared-corpus integration checks that
this removes the redundant output artifact and that cancellation cleans
up the remaining runs.

## Interactive and installed proof

The Server first-use workflow builds one lexical generation, then observes
the accepted generation through separate context and lexical reads at the
next publication's validated staging boundary. Both readers return the
accepted generation while the candidate awaits its final transaction.
The normal follow-up still detects the stale pin after publication,
builds the next lexical generation and reads it.

The offline Local, same-environment upgrade and provisioned Server owners
exercise the exact combined Python source through the installed wheel.
All work uses owned temporary homes and disposable database fixtures.

## Verification

- 149 scoped unit and 69 containerized integration cases passed.
- Canonical scoped tests used `--no-coverage`; hosted integration coverage
  and exact-candidate qualification are pending.
- Combined retention, tracked file lengths, bytecode compilation and Git
  whitespace checks passed.
- The sorter implementation and independent advisory review used dogfood
  `dynamic_default`. The final
  focused integration regression uses the ordinary staged-ingestion
  owner. Scoped proof includes unchanged debt registry membership.

Group these local changes with the previously tested compact-context
and resource report work for the next substantive public batch.
