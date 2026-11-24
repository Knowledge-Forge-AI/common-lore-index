"""Bounded file metadata helpers used by repository discovery."""

from __future__ import annotations

import hashlib
from pathlib import Path


def first_line(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().strip()
    except UnicodeDecodeError:
        return ""


def read_shebang_candidate(path: Path) -> str:
    """Read a stripped first line only when it can contain a shebang."""
    prefix = ""
    parts: list[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            while piece := handle.readline(4096):
                ended = piece.endswith("\n")
                if parts:
                    parts.append(piece)
                else:
                    prefix += piece if prefix else piece.lstrip()
                    if prefix.startswith("#!"):
                        parts.append(prefix)
                    elif prefix and prefix != "#":
                        return ""
                if ended:
                    break
    except UnicodeDecodeError:
        return ""
    return "".join(parts).rstrip()


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["content_hash", "first_line"]
