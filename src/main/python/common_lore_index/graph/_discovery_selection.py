"""Repository traversal selection, re-exported unchanged by ``graph.discovery``.

``selected_files`` is the incumbent ``discover_repository`` selection loop over
any ``os.walk``-shaped top-down walk, so the default ``os.walk`` route and the
metered governed walker share one ordering and filtering implementation.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import fnmatch
from pathlib import Path, PurePosixPath
from typing import Sequence


IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".direnv",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".serena",
        ".terraform",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
    }
)
DEFAULT_DISCOVERY_EXCLUDE_PATHS = tuple(sorted(IGNORED_DIR_NAMES))


def normalize_discovery_exclude_paths(
    exclude_paths: Sequence[str] = (),
) -> tuple[str, ...]:
    normalized: list[str] = []
    for exclude_path in exclude_paths:
        if not isinstance(exclude_path, str):
            raise ValueError("exclude path must be a string")
        candidate = exclude_path.strip().replace("\\", "/")
        while candidate.startswith("./"):
            candidate = candidate[2:]
        candidate = candidate.rstrip("/")
        parsed = PurePosixPath(candidate)
        if not candidate or parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError("exclude path must stay relative to the repository root")
        normalized.append(candidate)
    return tuple(normalized)


def _matches_glob(relative_path: str, pattern: str) -> bool:
    candidate = relative_path
    while candidate:
        if fnmatch.fnmatchcase(candidate, pattern):
            return True
        if "/" not in candidate:
            break
        candidate = candidate.rsplit("/", 1)[0]
    return False


def _is_excluded_relative_path(
    relative_path: str,
    exclude_paths: Sequence[str],
) -> bool:
    return first_matching_exclude(relative_path, exclude_paths) is not None


def first_matching_exclude(relative_path: str, exclude_paths: Sequence[str]) -> str | None:
    """Return the first matching rule, including ancestor matches for globs."""
    relative_path = relative_path.strip("/")
    if not relative_path:
        return None
    parts = relative_path.split("/")
    for exclude_path in exclude_paths:
        if any(marker in exclude_path for marker in "*?["):
            if _matches_glob(relative_path, exclude_path):
                return exclude_path
            continue
        if "/" not in exclude_path and exclude_path in parts:
            return exclude_path
        if relative_path == exclude_path or relative_path.startswith(f"{exclude_path}/"):
            return exclude_path
    return None


def _relative_posix_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_link_target(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _resolved_path_stays_under(root: Path, path: Path) -> bool:
    target = _resolve_link_target(path)
    return target is not None and target.is_relative_to(root)


def selected_files(
    repository_root: Path,
    configured_excludes: Sequence[str],
    walk: Iterable[tuple[str, list[str], list[str]]],
) -> Iterator[Path]:
    """Yield selected regular files in incumbent order, pruning ``dirnames`` in place."""

    for original, _ in _selected_file_paths(repository_root, configured_excludes, walk):
        yield original


def _distinct_selected_files(
    repository_root: Path,
    configured_excludes: Sequence[str],
    walk: Iterable[tuple[str, list[str], list[str]]],
) -> Iterator[Path]:
    seen: set[Path] = set()
    for _, canonical in _selected_file_paths(repository_root, configured_excludes, walk):
        if canonical not in seen:
            seen.add(canonical)
            yield canonical


def _selected_file_paths(
    repository_root: Path,
    configured_excludes: Sequence[str],
    walk: Iterable[tuple[str, list[str], list[str]]],
) -> Iterator[tuple[Path, Path]]:
    for directory, dirnames, filenames in walk:
        directory_path = Path(directory)
        kept_dirnames = []
        for dirname in sorted(dirnames):
            dir_path = directory_path / dirname
            relative_dir = _relative_posix_path(repository_root, dir_path)
            if dir_path.is_symlink() and not _resolved_path_stays_under(
                repository_root, dir_path
            ):
                continue
            if dirname in IGNORED_DIR_NAMES or _is_excluded_relative_path(
                relative_dir, configured_excludes
            ):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames
        for filename in sorted(filenames):
            file_path = directory_path / filename
            relative_file = _relative_posix_path(repository_root, file_path)
            canonical = file_path
            if file_path.is_symlink():
                resolved = _resolve_link_target(file_path)
                if resolved is None or not resolved.is_relative_to(repository_root):
                    continue
                resolved_relative = resolved.relative_to(repository_root)
                if any(part in IGNORED_DIR_NAMES for part in resolved_relative.parts[:-1]):
                    continue
                if _is_excluded_relative_path(resolved_relative.as_posix(), configured_excludes):
                    continue
                canonical = resolved
            if not file_path.is_file():
                continue
            if _is_excluded_relative_path(relative_file, configured_excludes):
                continue
            yield file_path, canonical


__all__ = (
    "DEFAULT_DISCOVERY_EXCLUDE_PATHS",
    "IGNORED_DIR_NAMES",
    "_is_excluded_relative_path",
    "_matches_glob",
    "_relative_posix_path",
    "_resolved_path_stays_under",
    "normalize_discovery_exclude_paths",
    "selected_files",
)
