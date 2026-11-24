# CLOE-W8-SDIST1 Offline Source Distribution Proof Exit

The candidate's declared version is built with the existing pinned packaging
toolchain. The diagnostic uses the same 872 package
inputs for the direct wheel and the source distribution. The archive retains
the exact bytes of the package sources, both migration catalogs and the
license and notice files. The inspector checks the name, metadata, file
census, path shape and content digests before the offline rebuild.

The source archive is the sole input to the rebuilt wheel. Both wheel
inventories and `RECORD` contents validate independently. All
874 compared wheel member payloads match, including
metadata, CLI entry points and both migration catalogs. The comparison
uses member bytes and excludes `RECORD` itself and ZIP headers. The rebuilt
artifact passes the existing pure Local environment checks and
`cloe --version`. The dependency projection comes from the existing SDK.

The measured case records the input, source archive and both wheel digests.
Its installed proof combines with the [released-source upgrade checkpoint](cloe-w8-upgrade1-exit.md)
for the next W8 batch. The release image, native platform and public
package-index paths have separate qualification.

## Scoped proof

The canonical unit run passed 23 cases under
`--suite unit --no-coverage --jobs 1 --` on the following owner.
It used the documented phase-owned directory within shared scratch.

```text
src/test/unit/python/repomap_kg/runtime/installed_candidate.unit.test.py
```

The canonical integration run passed four cases under
`--suite int --no-coverage --jobs 1 --pg-container-port 55433 -- -rP`
on these two owners. Terminal custody passed.

```text
src/test/int/python/repomap_kg/runtime/arch7f_release_wheel.int.test.py
src/test/int/python/repomap_kg/runtime/cloe_installed_local_upgrade.int.test.py
```

Static retention and file-length checks passed. Full local suites and
local coverage were unselected. The next larger milestone owns public
qualification. [CI161](cloe-ci161-qualification.md) is the qualified baseline.
