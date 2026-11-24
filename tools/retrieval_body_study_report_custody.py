"""Contract one owned canonical report export to private BODY custody.

The unchanged sandbox exporter creates a 0750 directory and 0640 files.
This phase-owned transition changes permissions only after every exact ledger
member has been opened and validated. It grants no execution authority.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from retrieval_body_study_contracts import refuse
from retrieval_body_study_inner_read_report import (
    MAX_SNAPSHOT_RECEIPT_BYTES, REPORT_NAME, SNAPSHOT_RECEIPT_NAME,
)
from retrieval_body_study_inner_read_report_finalizer import (
    CLOSURE_RECORD_NAME, SOURCE_RECORD_NAME,
)

_MEMBERS = ((REPORT_NAME, 65536),
            (SNAPSHOT_RECEIPT_NAME, MAX_SNAPSHOT_RECEIPT_BYTES),
            (SOURCE_RECORD_NAME, 16384), (CLOSURE_RECORD_NAME, 16384))


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def privatize_body_report_export(root: Path) -> None:
    """Require the known exporter shape before contracting its permissions."""
    refuse(root.is_absolute() and root.resolve() == root, "body export root alias")
    before = root.lstat()
    refuse(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid()
           and stat.S_IMODE(before.st_mode) == 0o750, "body export root custody")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened: list[tuple[str, int, os.stat_result]] = []
    try:
        refuse(_identity(os.fstat(directory)) == _identity(before), "body export root changed")
        for name, maximum in _MEMBERS:
            details = os.stat(name, dir_fd=directory, follow_symlinks=False)
            refuse(stat.S_ISREG(details.st_mode) and details.st_uid == os.getuid()
                   and stat.S_IMODE(details.st_mode) == 0o640 and details.st_nlink == 1
                   and 0 < details.st_size <= maximum, "body export member custody")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            opened.append((name, descriptor, details))
            refuse(_identity(os.fstat(descriptor)) == _identity(details),
                   "body export member changed")
        # No mode changes occur before the complete population passes.
        for name, descriptor, details in opened:
            refuse(_identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                   == _identity(details), "body export member changed")
        refuse(_identity(root.lstat()) == _identity(before), "body export root changed")
        for name, descriptor, details in opened:
            os.fchmod(descriptor, 0o600)
            after = os.fstat(descriptor)
            refuse(_identity(after) == _identity(details)
                   and stat.S_IMODE(after.st_mode) == 0o600,
                   "body export member contraction")
            refuse(_identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                   == _identity(after), "body export member changed")
        os.fchmod(directory, 0o700)
        current = root.lstat()
        refuse(_identity(current) == _identity(before)
               and stat.S_IMODE(current.st_mode) == 0o700, "body export root contraction")
    finally:
        for _name, descriptor, _details in opened:
            os.close(descriptor)
        os.close(directory)


__all__ = ["privatize_body_report_export"]
