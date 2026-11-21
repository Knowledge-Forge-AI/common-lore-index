"""Fail-closed canonical USTAR custody reader for the matched W5 source adapter.

Reads one bounded regular archive file beneath a symlink-free root via
``retrieval_body_study_files.read_member``, validates strict uncompressed POSIX
USTAR framing, portable relative member paths, canonical file metadata, census
and byte bounds in memory, and verifies deterministic ``tarfile.USTAR_FORMAT``
byte equality before returning an immutable ``Mapping[str, bytes]``. Importing
this module performs no I/O and grants no execution or publication authority.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import os
from pathlib import Path, PosixPath, PurePosixPath, WindowsPath
import re
import tarfile
from types import MappingProxyType
from typing import Final, NoReturn

from retrieval_body_study_contracts import BodyStudyRefusal
import retrieval_body_study_files

MAX_ARCHIVE_BYTES: Final = 2 * 1024 * 1024
MAX_ARCHIVE_FILES: Final = 128

_BLOCK_BYTES: Final = tarfile.BLOCKSIZE
_RECORD_BYTES: Final = tarfile.RECORDSIZE
_MAX_MEMBER_BYTES: Final = MAX_ARCHIVE_BYTES - (3 * _BLOCK_BYTES)
_CANONICAL_MODE: Final = 0o400

_MODE_FIELD: Final = b"0000400\x00"
_ZERO_OCTAL_8: Final = b"0000000\x00"
_ZERO_OCTAL_12: Final = b"00000000000\x00"
_ZERO_8: Final = b"\x00" * 8
_ZERO_12: Final = b"\x00" * 12
_ZERO_32: Final = b"\x00" * 32
_ZERO_100: Final = b"\x00" * 100
_ZERO_BLOCK: Final = b"\x00" * _BLOCK_BYTES

_EXTENDED_TYPES: Final = frozenset({b"x", b"g", b"X", b"L", b"K"})
_PATH_TYPES: Final = (Path, PosixPath, WindowsPath)
_HEX_SHA256: Final = re.compile(r"\A[0-9a-f]{64}\Z")
_OCTAL_11: Final = re.compile(rb"\A[0-7]{11}\x00\Z")
_PORTABLE_SEGMENT: Final = re.compile(r"\A[A-Za-z0-9._][A-Za-z0-9._-]*\Z")


class MatchedArchiveRefusal(ValueError):
    """Public-safe archive custody failure; raw paths and payloads never escape."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise MatchedArchiveRefusal(code) from None


def _validate_inputs(root: Path, relative: str, expected_sha256: str) -> None:
    if type(root) not in _PATH_TYPES:
        _fail("matched_archive:root_path_required")
    raw_root = os.fspath(root)
    if type(raw_root) is not str or not raw_root or "\x00" in raw_root:
        _fail("matched_archive:invalid_root")
    invalid_root = False
    try:
        raw_root.encode("utf-8")
    except UnicodeEncodeError:
        invalid_root = True
    if invalid_root:
        _fail("matched_archive:invalid_root")

    if type(relative) is not str or not relative:
        _fail("matched_archive:invalid_relative_path")
    invalid_relative = False
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        invalid_relative = True
    if invalid_relative:
        _fail("matched_archive:invalid_relative_path")

    if type(expected_sha256) is not str or _HEX_SHA256.fullmatch(expected_sha256) is None:
        _fail("matched_archive:invalid_sha256")


def _extract_ustar_field(raw_field: bytes) -> str:
    nul = raw_field.find(0)
    if nul == -1:
        content = raw_field
    else:
        content = raw_field[:nul]
        if any(raw_field[nul:]):
            _fail("matched_archive:malformed_header")
    if any(byte >= 0x80 for byte in content):
        _fail("matched_archive:nonportable_name")
    return content.decode("ascii")


def _validate_member_name(name: str) -> str:
    if not name:
        _fail("matched_archive:empty_name")
    if "\\" in name:
        _fail("matched_archive:backslash_name")
    if any(char in " \t\n\r\v\f" for char in name):
        _fail("matched_archive:whitespace_name")
    if any(ord(char) < 0x20 or ord(char) >= 0x7F for char in name):
        _fail("matched_archive:nonportable_name")
    if name.startswith("/"):
        _fail("matched_archive:absolute_name")
    if name.endswith("/"):
        _fail("matched_archive:aliased_name")
    parts = name.split("/")
    if any(part == "" for part in parts):
        _fail("matched_archive:aliased_name")
    if any(part in {".", ".."} for part in parts):
        _fail("matched_archive:traversal_name")
    if any(_PORTABLE_SEGMENT.fullmatch(part) is None for part in parts):
        _fail("matched_archive:nonportable_name")
    if PurePosixPath(name).as_posix() != name:
        _fail("matched_archive:aliased_name")
    return name


def _record_unaliased_path(
    name: str,
    seen_files_lower: set[str],
    seen_dirs_lower: dict[str, str],
) -> None:
    lower_name = name.lower()
    if lower_name in seen_files_lower or lower_name in seen_dirs_lower:
        _fail("matched_archive:aliased_name")
    parts = name.split("/")
    for depth in range(1, len(parts)):
        dir_exact = "/".join(parts[:depth])
        dir_lower = dir_exact.lower()
        if dir_lower in seen_files_lower:
            _fail("matched_archive:aliased_name")
        existing = seen_dirs_lower.get(dir_lower)
        if existing is not None and existing != dir_exact:
            _fail("matched_archive:aliased_name")
        seen_dirs_lower[dir_lower] = dir_exact
    seen_files_lower.add(lower_name)


def _parse_ustar_header(header: memoryview) -> tuple[str, int]:
    checksum = 256 + sum(header[:148]) + sum(header[156:])
    expected_checksum = f"{checksum:06o}\x00 ".encode("ascii")
    if bytes(header[148:156]) != expected_checksum:
        _fail("matched_archive:malformed_header")

    typeflag = bytes(header[156:157])
    if typeflag in _EXTENDED_TYPES:
        _fail("matched_archive:extended_header")
    if typeflag != tarfile.REGTYPE:
        _fail("matched_archive:unsupported_type")

    if bytes(header[257:265]) != tarfile.POSIX_MAGIC:
        _fail("matched_archive:non_ustar_magic")

    if (
        bytes(header[100:108]) != _MODE_FIELD
        or bytes(header[108:116]) != _ZERO_OCTAL_8
        or bytes(header[116:124]) != _ZERO_OCTAL_8
        or bytes(header[136:148]) != _ZERO_OCTAL_12
        or bytes(header[157:257]) != _ZERO_100
        or bytes(header[265:297]) != _ZERO_32
        or bytes(header[297:329]) != _ZERO_32
        or bytes(header[329:337]) != _ZERO_8
        or bytes(header[337:345]) != _ZERO_8
        or bytes(header[500:512]) != _ZERO_12
    ):
        _fail("matched_archive:noncanonical_header")

    size_field = bytes(header[124:136])
    if _OCTAL_11.fullmatch(size_field) is None:
        _fail("matched_archive:noncanonical_header")
    size = int(size_field[:11], 8)
    if size > _MAX_MEMBER_BYTES:
        _fail("matched_archive:oversized_member")

    name_part = _extract_ustar_field(bytes(header[0:100]))
    prefix_part = _extract_ustar_field(bytes(header[345:500]))
    if prefix_part:
        if not name_part:
            _fail("matched_archive:malformed_header")
        full_name = f"{prefix_part}/{name_part}"
        if len(full_name) <= tarfile.LENGTH_NAME:
            _fail("matched_archive:aliased_name")
    else:
        full_name = name_part

    return _validate_member_name(full_name), size


def _canonical_ustar_bytes(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    encode_failed = False
    try:
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(entries):
                payload = entries[name]
                info = tarfile.TarInfo(name=name)
                info.type = tarfile.REGTYPE
                info.size = len(payload)
                info.mode = _CANONICAL_MODE
                info.uid = 0
                info.gid = 0
                info.mtime = 0
                info.uname = ""
                info.gname = ""
                info.linkname = ""
                info.devmajor = 0
                info.devminor = 0
                archive.addfile(info, io.BytesIO(payload))
    except (OSError, ValueError, tarfile.TarError):
        encode_failed = True
    if encode_failed:
        _fail("matched_archive:noncanonical_ustar")
    return buffer.getvalue()


def _decode_canonical_ustar(raw: bytes) -> Mapping[str, bytes]:
    if not raw or len(raw) < 2 * _BLOCK_BYTES or len(raw) % _BLOCK_BYTES != 0:
        _fail("matched_archive:truncated")

    view = memoryview(raw)
    entries: dict[str, bytes] = {}
    seen_files_lower: set[str] = set()
    seen_dirs_lower: dict[str, str] = {}
    previous_name: str | None = None
    total_payload_bytes = 0
    offset = 0
    saw_eof = False

    while offset < len(raw):
        header = view[offset : offset + _BLOCK_BYTES]
        if header == _ZERO_BLOCK:
            if len(raw) - offset < 2 * _BLOCK_BYTES:
                _fail("matched_archive:truncated")
            if any(view[offset:]):
                _fail("matched_archive:trailing_bytes")
            end_after_eof = offset + (2 * _BLOCK_BYTES)
            remainder = end_after_eof % _RECORD_BYTES
            expected_len = (
                end_after_eof if remainder == 0 else end_after_eof + (_RECORD_BYTES - remainder)
            )
            if len(raw) < expected_len:
                _fail("matched_archive:truncated")
            if len(raw) > expected_len:
                _fail("matched_archive:trailing_bytes")
            saw_eof = True
            break

        name, size = _parse_ustar_header(header)
        if len(entries) >= MAX_ARCHIVE_FILES:
            _fail("matched_archive:excessive_census")
        if name in entries:
            _fail("matched_archive:duplicate_name")
        _record_unaliased_path(name, seen_files_lower, seen_dirs_lower)
        if previous_name is not None and name < previous_name:
            _fail("matched_archive:unsorted_members")
        previous_name = name

        total_payload_bytes += size
        if total_payload_bytes > MAX_ARCHIVE_BYTES:
            _fail("matched_archive:oversized_member")

        data_start = offset + _BLOCK_BYTES
        data_end = data_start + size
        pad_remainder = size % _BLOCK_BYTES
        pad_bytes = 0 if pad_remainder == 0 else _BLOCK_BYTES - pad_remainder
        block_end = data_end + pad_bytes
        if block_end > len(raw):
            _fail("matched_archive:truncated")
        if pad_bytes and any(view[data_end:block_end]):
            _fail("matched_archive:malformed_padding")

        entries[name] = bytes(view[data_start:data_end])
        offset = block_end

    if not saw_eof:
        _fail("matched_archive:truncated")
    if _canonical_ustar_bytes(entries) != raw:
        _fail("matched_archive:noncanonical_ustar")
    return MappingProxyType(entries)


def read_matched_archive(
    root: Path, relative: str, *, expected_sha256: str
) -> Mapping[str, bytes]:
    """Read and validate one canonical USTAR matched-source archive beneath ``root``."""
    _validate_inputs(root, relative, expected_sha256)
    refusal_code: str | None = None
    failure_code: str | None = None
    raw = b""
    try:
        raw = retrieval_body_study_files.read_member(
            root,
            relative,
            max_bytes=MAX_ARCHIVE_BYTES,
            expected_sha256=f"sha256:{expected_sha256}",
        )
    except BodyStudyRefusal as exc:
        refusal_code = (
            exc.code if type(exc.code) is str and exc.code else "matched_archive:read_refused"
        )
    except (OSError, ValueError):
        failure_code = "matched_archive:read_failed"

    if refusal_code is not None:
        raise BodyStudyRefusal(refusal_code)
    if failure_code is not None:
        _fail(failure_code)

    if type(raw) is not bytes:
        _fail("matched_archive:bytes_required")
    if len(raw) > MAX_ARCHIVE_BYTES:
        _fail("matched_archive:archive_too_large")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail("matched_archive:digest_mismatch")

    return _decode_canonical_ustar(raw)


__all__ = ["read_matched_archive"]
