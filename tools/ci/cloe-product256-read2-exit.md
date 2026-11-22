# CLOE-PRODUCT256-READ2 Source Readback and UTF-8 Accounting Exit

SQLite Local source inventory keeps only the lexically selected groups.
Source-run queries aggregate each run as it arrives and preserve ordering,
integer validation, null handling and parse-error status.

UTF-8 byte counts use bounded temporary buffers in extraction and spool
accounting. Strict Unicode encoding failures preserve the original position.
The byte count includes the full text even when the existing size guard refuses.

Scoped verification passed 85 unit and 14 containerized integration cases,
including real SQLite/PostgreSQL readback parity. The complete hosted
candidate remains pending as part of the cumulative staging batch.
