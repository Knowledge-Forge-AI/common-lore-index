# CLOE-LEXICAL-PROJECTION1 Streaming Lexical Projection Exit

LF-only input reuses its validated bytes. CRLF and CR still normalize to
LF. Strict UTF-8 and NUL checks cover the full input before a projected
prefix can be admitted. Chunk assembly consumes lines and chunk
specifications incrementally. Wide UTF-8 lines split only at complete
code points and retain their original offsets and continuation flags.

A deterministic 250-case parity witness compared complete normalization
and chunk records, including identifiers and refusals. The four additional
fixed files matched as well. For the dense short-line fixture, peak traced
allocation fell from 9,492,906 to 1,536,797 bytes over text
normalization and chunking. The retained Local publication reproduced all
bytes and the continuation positions of a real multi-chunk Unicode source.

The combined candidate passed 146 scoped unit and 57 scoped
containerized integration cases, including SQLite Local projection,
PostgreSQL lexical capture and installed-wheel checks. Exact selectors
and verification are recorded in the commit. Complete local suites and
Python coverage were unselected. Hosted qualification remains pending.
