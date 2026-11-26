"""Check the project-owned source archive before handing it to the wheel builder."""

from __future__ import annotations

import hashlib
import tarfile
from collections.abc import Mapping
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from installed_experience_pins import CANDIDATE_DISTRIBUTION, CANDIDATE_VERSION

_SDIST_STEM = f"common_lore_index-{CANDIDATE_VERSION}"
SDIST_FILENAME = f"{_SDIST_STEM}.tar.gz"
_EGG_INFO = "src/main/python/common_lore_index.egg-info"
_GENERATED = frozenset({"PKG-INFO", "setup.cfg", *(
    f"{_EGG_INFO}/{name}" for name in ("PKG-INFO", "SOURCES.txt", "dependency_links.txt",
                                      "entry_points.txt", "requires.txt", "top_level.txt")
)})
_MAX_MEMBERS = 4096
_MAX_TAR_BYTES = 64 * 1024 * 1024
_MAX_METADATA = 64 * 1024


def source_hashes(root: Path, names: list[str]) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def _validate_metadata(raw: bytes, present: set[str]) -> None:
    metadata = BytesParser().parsebytes(raw)
    if metadata.get_all("Name") != [CANDIDATE_DISTRIBUTION] or metadata.get_all("Version") != [CANDIDATE_VERSION]:
        raise AssertionError("source archive metadata identity differs from the project")
    if set(metadata.get_all("License-File") or ()) != {"LICENSE", "NOTICE"}:
        raise AssertionError("source archive license metadata differs from the project")
    if not {"LICENSE", "NOTICE"} <= present:
        raise AssertionError("source archive license files missing")


def verify_sdist(archive: Path, expected: Mapping[str, str]) -> None:
    """Verify an exact closed source census with bounded, read-only tar inspection."""
    if archive.name != SDIST_FILENAME or archive.stat().st_size > _MAX_TAR_BYTES:
        raise AssertionError("source archive name or size differs from its contract")
    allowed = set(expected) | _GENERATED
    directories = {parent.as_posix() for name in allowed for parent in PurePosixPath(name).parents}
    seen: set[str] = set()
    regular: set[str] = set()
    metadata = b""
    remaining = _MAX_TAR_BYTES
    with tarfile.open(archive, mode="r|gz") as stream:
        for index, member in enumerate(stream):
            if index >= _MAX_MEMBERS or not (member.isfile() or member.isdir()) or member.issparse():
                raise AssertionError("source archive has excess or unsupported members")
            path = PurePosixPath(member.name)
            if path.as_posix() != member.name or not path.parts or path.parts[0] != _SDIST_STEM or ".." in path.parts:
                raise AssertionError("source archive has an invalid member path")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if relative in seen:
                raise AssertionError("source archive has a duplicate member")
            seen.add(relative)
            if member.isdir():
                if relative not in directories or member.size:
                    raise AssertionError("source archive has an unexpected directory")
                continue
            if relative not in allowed or not 0 <= member.size <= remaining:
                raise AssertionError("source archive has an unexpected or oversized file")
            remaining -= member.size
            raw = stream.extractfile(member)
            if raw is None:
                raise AssertionError("source archive member could not be read")
            with raw:
                if relative == "PKG-INFO":
                    if member.size > _MAX_METADATA:
                        raise AssertionError("source archive metadata exceeds its bound")
                    metadata = raw.read()
                elif relative in expected:
                    digest = hashlib.sha256()
                    while chunk := raw.read(64 * 1024):
                        digest.update(chunk)
                    if digest.hexdigest() != expected[relative]:
                        raise AssertionError("source archive file differs from the package input")
            regular.add(relative)
    if not {*expected, "PKG-INFO"} <= regular or "." not in seen:
        raise AssertionError("source archive omitted required inputs")
    _validate_metadata(metadata, regular)
