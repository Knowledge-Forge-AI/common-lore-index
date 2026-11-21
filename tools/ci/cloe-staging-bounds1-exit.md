# CLOE-STAGING-BOUNDS1 Prepared Staging and Bundle Consolidation Exit

Prepared rows now use the existing private spool once a family's compact
UTF-8 JSONL payload would exceed 1 MiB or its row count would exceed 4,096.
The existing observer admits each spilled row before its write. The
short-circuit allows the crossing row and avoids retaining a full family
before the first spool write. Both paths produce identical row checksums and identities.

On 100 synthetic rows of roughly 128 KiB, the incumbent kept all rows in
memory. The new path wrote the first spool after acquiring
8 rows. Peak traced preparation
allocation fell from 13,149,914 to
1,492,763 bytes. Both row checksums
matched. The threshold counts serialized payload and the measurement covers
this preparation call. In the 240-observation small fixture, median
preparation time over ten sampled calls went from
23.1 ms to 28.9 ms.

The publication bundle and structural digest sorters share byte-weighted
final consolidation. Five workloads retained the same sorted bytes.
At fan-in 32, the uneven 95-run fixture rewrote
1,531,867 bytes at final consolidation before
the change and 11,924 bytes afterward.
Initial carry compaction continues to use its existing level rules.

The actual portable worker produced the same valid publication bundle
through a byte-triggered spill. A wide row also passed real PostgreSQL
staging COPY, published, returned its full payload on readback and left
no stage spool. The dogfood `dynamic_default` dispatcher implemented and
independently reviewed prepared buffering. The parent aligned test
measurement signatures and ran the frozen checks on the corrected files.

The combined frozen source passed 146 scoped unit tests, 57
scoped containerized integration tests, changed-file compilation and the
retained Python and file-length checks. An earlier integration preview
ran while its source manifest was changing and was rejected by the runner.
The frozen proof passed its source-identity and cleanup checks.
Complete local suites and Python coverage were unselected. Hosted
qualification remains pending.
