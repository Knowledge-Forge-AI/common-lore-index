# ADR 0219: Retain frozen Trixie package acquisition

## Status

Accepted for the CI163 repair under ADR 0089 D3’s official-archive rule.

## Context

CI163 stopped the native and system gates before an application build.
The live PGDG index could no longer supply the PostgreSQL base's exact
libpq runtime and header pair. A disposable probe of the pinned Python
base also reproduced the smoke dependency failure. The live Trixie
glibc headers require a newer installed libc than the pinned base holds.

## Decision

Keep the existing base digests, package identity manifest, accepted
signing keys and dynamic libpq linking. Declare the official PGDG archive
and dated Debian security snapshot as additional sources for the frozen
D12 builder selection. Its preflight requests the frozen package
versions and checks every selected archive identity against the manifest.
The build uses the same sources and retains offline installation of
verified archives.

The dependency-only smoke materialization adds dated official Trixie
sources and selects the exact headers matching its held libc and
OpenSSL rows. APT validates the archive signatures using the keyrings
from the pinned bases. Expiry checking is disabled only for dated
Debian snapshots. Existing package holds and per-archive verification
apply to both routes.

Rollback removes the additional source configuration and restores the
previous recipe. The preflight then refuses if the live repositories
cannot supply the frozen versions.
