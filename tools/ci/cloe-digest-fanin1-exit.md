# CLOE-DIGEST-FANIN1 Bounded Artifact and Digest Exit

Artifact verification now reads filesystem objects through the existing
verifying stream and releases each chunk after processing it. On a
16 MiB fixture it matched valid, undersized-limit and corrupt decisions
and reduced peak traced Python allocations from 16,784,848 to 71,637
bytes. These are allocation measurements for this operation and fixture.

The bundle parser validates each canonical frame once. Frames within one
family have identical framing around their canonical record bytes. The
parser uses these verified bytes to check order, including escaped
Unicode and nested fields. An independent witness matched the order of
340 records in three families. Both decoders returned the same diagnostics
for eight malformed encodings. The aggregate byte cap applies to the header
and each subsequent line, with one extra byte to detect overflow.
An overflow can now precede frame diagnostics once the byte cap is crossed.

Final digest consolidation combines the smallest tracked runs until the
remaining input count fits the configured fan-in. It preserves carry
compaction, stable row bytes and owned-file cleanup. All five measured
workloads retained the incumbent digest. The 32-way fixture reduced
spill writes from 1,439,725 to 911,500 bytes. The previously over-limit
two- and three-input fixtures now open at most two and three inputs.
The two-input fixture requires one more merge to respect that bound.

## Local verification

- 107 scoped unit and 85 containerized integration cases passed, including installed Local and provisioned Server.
- Exact byte comparisons, file length, compilation, Python retention and
  Git whitespace checks passed.
- The dogfood dispatcher in `dynamic_default` implemented and independently
  reviewed the sorter. The parent used focused unit cases and the existing
  scoped staged-ingestion integration owner.
- The initial aggregate integration run passed its cases and failed the
  host residue check on an unattributed image. The unchanged rerun passed
  the cases and terminal cleanup checks.
- Scoped tests used `--no-coverage`. Hosted coverage and exact-candidate
  qualification remain for public staging.
