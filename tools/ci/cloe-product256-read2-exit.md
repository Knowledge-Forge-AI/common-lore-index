# CLOE-PRODUCT256-READ2 Source Readback and UTF-8 Accounting Exit

SQLite Local source inventory keeps only the lexically selected groups.
Source-run queries aggregate each run as it arrives and preserve ordering,
integer validation, null handling and parse-error status.

UTF-8 byte counts use bounded temporary buffers in extraction and spool
accounting. Strict Unicode encoding failures preserve the original position.
The byte count includes the full text even when the existing size guard refuses.

Archive import hashes the complete admitted file without reading beyond its
observed length.

Scoped verification passed 94 unit and 15 containerized integration cases,
including real SQLite/PostgreSQL readback parity. The complete hosted
candidate remains pending as part of the cumulative staging batch.
