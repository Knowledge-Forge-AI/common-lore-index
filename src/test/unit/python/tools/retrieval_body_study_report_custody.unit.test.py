"""BODY permission contraction preserves the known owned-export boundary."""
from __future__ import annotations

import io
from pathlib import Path
import tarfile

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_inner_read_report import REPORT_NAME, SNAPSHOT_RECEIPT_NAME
from retrieval_body_study_inner_read_report_finalizer import (
    CLOSURE_RECORD_NAME, SOURCE_RECORD_NAME,
)
from retrieval_body_study_report_custody import privatize_body_report_export
from test_sandbox_report import export_sandbox_report

_NAMES = (REPORT_NAME, SNAPSHOT_RECEIPT_NAME, SOURCE_RECORD_NAME, CLOSURE_RECORD_NAME)


def _export(tmp_path: Path) -> Path:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name in _NAMES:
            raw = b"{}\n"
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
    destination = tmp_path / "exported"
    export_sandbox_report(stream.getvalue(), destination,
                          max_members=8, max_content_bytes=65536)
    return destination


def test_known_export_is_private_without_rewriting_bytes(tmp_path: Path) -> None:
    root = _export(tmp_path)
    original = {name: (root / name).read_bytes() for name in _NAMES}
    assert root.stat().st_mode & 0o777 == 0o750
    assert all((root / name).stat().st_mode & 0o777 == 0o640 for name in _NAMES)
    privatize_body_report_export(root)
    assert root.stat().st_mode & 0o777 == 0o700
    assert all((root / name).stat().st_mode & 0o777 == 0o600 for name in _NAMES)
    assert {name: (root / name).read_bytes() for name in _NAMES} == original
    with pytest.raises(BodyStudyRefusal, match="root custody"):
        privatize_body_report_export(root)


@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink", "oversized", "missing"])
def test_bad_member_refuses_before_any_permission_change(tmp_path: Path, fault: str) -> None:
    root = _export(tmp_path)
    target = root / CLOSURE_RECORD_NAME
    if fault == "mode":
        target.chmod(0o600)
    elif fault in {"symlink", "hardlink"}:
        target.unlink()
        other = tmp_path / "other-owned-file"
        other.write_bytes(b"{}\n")
        other.chmod(0o640)
        if fault == "symlink":
            target.symlink_to(other)
        else:
            target.hardlink_to(other)
    elif fault == "oversized":
        target.write_bytes(b"x" * 16385)
    else:
        target.unlink()
    with pytest.raises((BodyStudyRefusal, OSError)):
        privatize_body_report_export(root)
    assert root.stat().st_mode & 0o777 == 0o750
    assert all((root / name).stat().st_mode & 0o777 == 0o640 for name in _NAMES[:2])


def test_root_alias_never_contracts_its_target(tmp_path: Path) -> None:
    root = _export(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(BodyStudyRefusal, match="alias"):
        privatize_body_report_export(alias)
    assert root.stat().st_mode & 0o777 == 0o750
