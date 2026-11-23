"""Directory and file scanning helpers for archive sources."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from common_lore_index.ops.ingestion._source_archive_records import (
    ARCHIVE_EXCLUDED_DIR_NAMES,
    JAVASCRIPT_ARCHIVE_EXTENSIONS,
    ArchiveIncludedFile,
    ArchiveSkippedFile,
    ArchiveSourceConfig,
)
from common_lore_index.ops.ingestion.source_common import SourcePolicyError


def _scan_archive_directory(
    config: ArchiveSourceConfig,
    root: Path,
    artifact_root: Path,
) -> tuple[tuple[ArchiveIncludedFile, ...], tuple[ArchiveSkippedFile, ...]]:
    included: list[ArchiveIncludedFile] = []
    skipped: list[ArchiveSkippedFile] = []
    total_bytes = 0

    pending = [iter(sorted(artifact_root.iterdir(), key=lambda path: path.name))]
    while pending:
        child = next(pending[-1], None)
        if child is None:
            pending.pop()
            continue
        relative_path = child.relative_to(artifact_root).as_posix()
        if child.name in ARCHIVE_EXCLUDED_DIR_NAMES and child.is_dir():
            skipped.append(ArchiveSkippedFile(relative_path, "excluded-directory"))
            continue
        if _is_hidden_relative_path(relative_path) and not config.hidden_files:
            skipped.append(ArchiveSkippedFile(relative_path, "hidden"))
            continue
        if child.is_symlink():
            skipped.append(ArchiveSkippedFile(relative_path, "symlink"))
            continue
        if len(Path(relative_path).parts) > config.max_depth:
            skipped.append(ArchiveSkippedFile(relative_path, "max_depth"))
            continue
        if child.is_dir():
            pending.append(iter(sorted(child.iterdir(), key=lambda path: path.name)))
            continue
        if not child.is_file():
            skipped.append(ArchiveSkippedFile(relative_path, "special-file"))
            continue
        if len(included) >= config.max_file_count:
            skipped.append(ArchiveSkippedFile(relative_path, "max_file_count"))
            continue
        byte_length = child.stat().st_size
        if total_bytes + byte_length > config.max_artifact_bytes:
            skipped.append(ArchiveSkippedFile(relative_path, "max_artifact_bytes"))
            continue
        included.append(_archive_included_file(root, artifact_root, child, byte_length))
        total_bytes += byte_length

    return tuple(included), tuple(skipped)


def _scan_archive_file(
    config: ArchiveSourceConfig,
    root: Path,
    artifact_path: Path,
) -> tuple[tuple[ArchiveIncludedFile, ...], tuple[ArchiveSkippedFile, ...]]:
    byte_length = artifact_path.stat().st_size
    if byte_length > config.max_artifact_bytes:
        return (), (ArchiveSkippedFile(artifact_path.name, "max_artifact_bytes"),)
    return (
        (
            _archive_included_file(
                root,
                artifact_path.parent,
                artifact_path,
                byte_length,
            ),
        ),
        (),
    )


def _archive_included_file(
    root: Path,
    artifact_root: Path,
    file_path: Path,
    byte_length: int,
) -> ArchiveIncludedFile:
    expected = file_path.lstat()
    if not stat.S_ISREG(expected.st_mode):
        raise SourcePolicyError("archive manifest input must be a regular file")
    if expected.st_size != byte_length:
        raise SourcePolicyError("archive file size changed during manifest scan")

    digest = hashlib.sha256()
    with open(file_path, "rb", opener=_open_archive_file) as source:
        initial = os.fstat(source.fileno())
        if _file_version(initial) != _file_version(expected):
            raise SourcePolicyError("archive file changed during manifest scan")
        remaining = byte_length
        while remaining:
            block = source.read(min(remaining, 256 * 1024))
            if not block:
                raise SourcePolicyError("archive file shrank during manifest scan")
            digest.update(block)
            remaining -= len(block)
        final = os.fstat(source.fileno())
        if _file_version(initial) != _file_version(final):
            raise SourcePolicyError("archive file changed during manifest scan")
    return ArchiveIncludedFile(
        relative_path=file_path.relative_to(artifact_root).as_posix(),
        repository_path=file_path.relative_to(root).as_posix(),
        byte_length=byte_length,
        sha256=digest.hexdigest(),
        extractor_route=_archive_extractor_route(file_path),
        media_type=_archive_media_type(file_path),
    )


def _open_archive_file(path: str, flags: int) -> int:
    try:
        return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        raise SourcePolicyError("archive manifest input could not be opened safely") from error


def _file_version(item: os.stat_result) -> tuple[int, ...]:
    # Path-based and handle-based stat may report different synthesized permission bits.
    return (item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode), item.st_size, item.st_mtime_ns, item.st_ctime_ns)


def _archive_extractor_route(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".css":
        return "css"
    if suffix in {".json", ".jsonc", ".jsonl", ".toml", ".plist", ".xml"}:
        return "config-or-feed"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".bash":
        return "bash"
    if suffix == ".bats":
        return "bats"
    if suffix == ".awk":
        return "awk"
    if suffix == ".zsh":
        return "zsh"
    if suffix == ".zunit":
        return "zunit"
    if suffix == ".sh":
        return "shell"
    if suffix == ".py":
        return "python"
    if suffix == ".nix":
        return "nix"
    if suffix in {".ps1", ".psm1", ".psd1"}:
        return "powershell"
    if suffix in JAVASCRIPT_ARCHIVE_EXTENSIONS:
        return "javascript"
    return "file"


def _archive_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html"
    if suffix == ".css":
        return "text/css"
    if suffix == ".json":
        return "application/json"
    if suffix == ".jsonc":
        return "application/jsonc"
    if suffix == ".jsonl":
        return "application/jsonl"
    if suffix == ".toml":
        return "application/toml"
    if suffix in {".xml", ".plist"}:
        return "application/xml"
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix in {".js", ".mjs", ".cjs", ".jsx"}:
        return "text/javascript"
    if suffix in {".ts", ".mts", ".cts", ".tsx"}:
        return "text/typescript"
    if suffix in {".ps1", ".psm1", ".psd1"}:
        return "text/x-powershell"
    return "application/octet-stream"


def _resolve_archive_artifact_path(root: Path, artifact_path: str) -> Path:
    candidate = root / artifact_path
    if _archive_path_contains_symlink(root, candidate):
        raise SourcePolicyError("artifact.path must not be a symlink")
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise SourcePolicyError("artifact.path must normalize inside root_path") from error
    return candidate


def _archive_path_contains_symlink(root: Path, candidate: Path) -> bool:
    """Check lexical path components without resolving any symlink targets."""

    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _is_hidden_relative_path(relative_path: str) -> bool:
    return any(part.startswith(".") for part in Path(relative_path).parts)
