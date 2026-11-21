"""Fresh source, treatment, and worker closures for one body attempt.

The worker identity intentionally delegates to the maintained public-text
source helper.  Its identity is the official JSON object
``{"closure":"worker","files":...}``; the body study must not mint a
second hash domain for that same 17-file closure.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from retrieval_body_study_contracts import MAX_SOURCE_CLOSURE_BYTES, WORKER_CLOSURE_FILES, refuse

_HEX = set("0123456789abcdef")
_MAX_MEMBER_BYTES = 4 * 1024 * 1024

# These lists are only defaults.  The plan carries the exact lists and the
# host re-reads them on every authority hop, so adding a file cannot silently
# change the admitted closure.
from retrieval_body_study_closure_paths import (
    BODY_SOURCE_MEMBERS as BODY_SOURCE_MEMBERS,
    BODY_TREATMENT_MEMBERS as BODY_TREATMENT_MEMBERS,
)

def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _valid_relative(name: object) -> str:
    refuse(type(name) is str and bool(name), "body closure member")
    text = str(name)
    parts = PurePosixPath(text).parts
    refuse("\x00" not in text and "\\" not in text and not text.startswith("/")
           and all(part not in {"", ".", "..", "__pycache__"} for part in parts)
           and PurePosixPath(*parts).as_posix() == text, "body closure member")
    return text


def read_source_member(root: Path, name: str, *, maximum: int = _MAX_MEMBER_BYTES) -> bytes:
    """Read one regular member, checking the descriptor before and after bytes."""
    refuse(isinstance(root, Path) and root.is_absolute() and not root.is_symlink(),
           "body closure root")
    relative = _valid_relative(name)
    path = root / relative
    for ancestor in path.parents:
        if ancestor == root:
            break
        refuse(not ancestor.is_symlink(), "body closure parent link")
    try:
        before = os.lstat(path)
    except OSError:
        raise ValueError("body closure member unavailable") from None
    refuse(stat.S_ISREG(before.st_mode) and before.st_size <= maximum,
           "body closure member custody")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - sum(map(len, chunks))))
            if not chunk:
                break
            chunks.append(chunk)
            refuse(sum(map(len, chunks)) <= maximum, "body closure member bounds")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    refuse(len(raw) == before.st_size
           and (before.st_ino, before.st_size, before.st_mtime_ns)
           == (after.st_ino, after.st_size, after.st_mtime_ns),
           "body closure member changed")
    return raw


def _digest_map(files: Mapping[str, str], *, expected: int | None = None) -> dict[str, str]:
    refuse(isinstance(files, Mapping) and bool(files), "body closure map")
    result: dict[str, str] = {}
    for key, value in sorted(files.items()):
        name = _valid_relative(key)
        refuse(name not in result and type(value) is str and value.startswith("sha256:")
               and len(value) == 71 and set(value[7:]) <= _HEX, "body closure digest")
        result[name] = value
    if expected is not None:
        refuse(len(result) == expected, "body closure member count")
    return result


def _domain_identity(name: str, files: Mapping[str, str]) -> str:
    # Keep source/treatment domains separate from the official worker domain.
    return _digest(_canonical({"closure": name, "files": dict(sorted(files.items()))}))


def worker_closure_sha256(files: Mapping[str, str]) -> str:
    """Return the official worker identity over exactly the maintained 17 map."""
    checked = _digest_map(files, expected=WORKER_CLOSURE_FILES)
    return _domain_identity("worker", checked)


def source_closure_sha256(files: Mapping[str, str]) -> str:
    checked = _digest_map(files)
    return _domain_identity("body-source", checked)


def treatment_sha256(files: Mapping[str, str]) -> str:
    checked = _digest_map(files)
    return _domain_identity("body-treatment", checked)




def build_closure(root: Path, members: Sequence[str], *, name: str,
                  maximums: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Freshly read a named body closure; duplicate members are refused."""
    refuse(name in {"body-source", "body-treatment"}, "body closure name")
    names = tuple(_valid_relative(member) for member in members)
    refuse(names and len(set(names)) == len(names), "body closure members")
    if maximums is not None:
        refuse(set(maximums) == set(names), "body closure maximums")
    files: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for member in sorted(names):
        maximum = _MAX_MEMBER_BYTES if maximums is None else maximums[member]
        refuse(type(maximum) is int and 0 <= maximum <= _MAX_MEMBER_BYTES, "body closure maximum")
        raw = read_source_member(root, member, maximum=maximum)
        files[member] = raw
        digests[member] = _digest(raw)
    total = sum(len(raw) for raw in files.values())
    refuse(total <= MAX_SOURCE_CLOSURE_BYTES, "body closure bytes")
    identity = _domain_identity(name, digests)
    return {"name": name, "files": files, "digests": digests,
            "member_count": len(files), "total_bytes": total,
            "identity": identity, "schema": "cloe-body-study-closure-v1"}




__all__ = [
    "BODY_SOURCE_MEMBERS", "BODY_TREATMENT_MEMBERS", "build_closure",
    "read_source_member",
    "source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
]
