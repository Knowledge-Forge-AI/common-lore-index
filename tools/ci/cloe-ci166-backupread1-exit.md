# CLOE-CI166-BACKUPREAD1 Exit

CI166 failed 24 bounded-neighborhood unit cases because the SQLite cursor
wrapper did not delegate close. The fixture now delegates close and the bounded
fetch assertion checks cursor closure. The other 13 hosted jobs passed.

The Server backup read paths now enumerate manifests in path order and stop
ID lookup on its first match. Inspection rejects a known size mismatch before
hashing a dump. Restore TOC summaries retain up to 50 unique names per category
while counting every recognized entry.

The source commit records the scoped verification. The CI167 reservation binds
one staging synchronize attempt to draft PR 28 and the existing website base.
