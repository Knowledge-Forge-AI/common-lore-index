"""Real filesystem custody and complete-archive publication with concurrent readers.

All custody and racing publication calls use actual files. One interruption test
injects KeyboardInterrupt after an actual partial write; it is not an OS-signal
qualification or a process-death cleanup claim.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import threading
import zipfile

import pytest

from repomap_test_support.retrieval_supplement_analysis_fixtures import (
    SYNTHETIC_ANALYZER, synthetic_bytes, synthetic_provenance, synthetic_tasks,
)
import retrieval_supplement_analysis_custody as custody
from retrieval_supplement_analysis_output import analyze_retained_member
from retrieval_supplement_analysis_values import AnalysisRefusal
from retrieval_supplement_files import file_inventory, publish_member
from retrieval_supplement_primitives import digest_bytes

MEMBER = "retained/bundle.json"
BUNDLE = "analysis-bundle.zip"
JSON_MEMBER = "analysis/analysis.json"
REPORT_MEMBER = "analysis/analysis.md"


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    source, output = tmp_path / "input", tmp_path / "output"
    source.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    return source, output


def _analyze(source: Path, output: Path, raw: bytes) -> dict[str, str]:
    return analyze_retained_member(
        source, MEMBER, output, provenance=synthetic_provenance(raw),
        tasks=synthetic_tasks(), analyzer_source_sha256=SYNTHETIC_ANALYZER,
        json_member=JSON_MEMBER, report_member=REPORT_MEMBER,
    )


def _read_complete(path: Path) -> tuple[bytes, bytes]:
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        assert archive.namelist() == [JSON_MEMBER, REPORT_MEMBER]
        assert archive.testzip() is None
        return archive.read(JSON_MEMBER), archive.read(REPORT_MEMBER)


def test_real_input_custody_and_exclusive_atomic_archive(tmp_path: Path) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes(emit_slot_query_costs=True)
    publish_member(source, MEMBER, raw)
    result = _analyze(source, output, raw)
    assert file_inventory(output) == (BUNDLE,)
    json_raw, report_raw = _read_complete(output / BUNDLE)
    assert result["json_sha256"] == digest_bytes(json_raw)
    assert result["report_sha256"] == digest_bytes(report_raw)
    assert result["bundle_sha256"] == digest_bytes((output / BUNDLE).read_bytes())
    assert json.loads(json_raw)["counts"]["tasks"] == 8
    assert b"Limitations" in report_raw
    assert (output / BUNDLE).stat().st_nlink == 1
    assert (source / MEMBER).read_bytes() == raw
    assert (source / MEMBER).stat().st_nlink == 1


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_existing_archive_destination_is_never_clobbered(tmp_path: Path, kind: str) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    publish_member(source, MEMBER, raw)
    target = output / BUNDLE
    if kind == "file":
        target.write_bytes(b"pre-existing-bytes")
    elif kind == "directory":
        target.mkdir()
    else:
        outside = tmp_path / "outside"
        outside.write_bytes(b"pre-existing-bytes")
        target.symlink_to(outside)
    with pytest.raises(AnalysisRefusal, match="analysis:output_destination_exists"):
        _analyze(source, output, raw)
    if kind == "directory":
        assert target.is_dir() and list(target.iterdir()) == []
    else:
        assert target.read_bytes() == b"pre-existing-bytes"
    assert sorted(p.name for p in output.iterdir()) == [BUNDLE]


@pytest.mark.parametrize("part", ["root", "parent", "leaf"])
def test_symlinked_input_component_is_refused(tmp_path: Path, part: str) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bundle.json").write_bytes(raw)
    if part == "root":
        publish_member(source, MEMBER, raw)
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif part == "parent":
        (source / "retained").symlink_to(outside, target_is_directory=True)
    else:
        (source / "retained").mkdir()
        (source / MEMBER).symlink_to(outside / "bundle.json")
    with pytest.raises(AnalysisRefusal):
        _analyze(source, output, raw)
    assert list(output.iterdir()) == []


def test_symlinked_output_root_is_refused(tmp_path: Path) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    publish_member(source, MEMBER, raw)
    alias = tmp_path / "alias-output"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(AnalysisRefusal, match="analysis:custody_unsafe_directory"):
        _analyze(source, alias, raw)
    assert list(output.iterdir()) == []


def test_hardlink_alias_and_fifo_input_are_refused_without_blocking(tmp_path: Path) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    outside = tmp_path / "outside.json"
    outside.write_bytes(raw)
    (source / "retained").mkdir()
    os.link(outside, source / MEMBER)
    with pytest.raises(AnalysisRefusal, match="analysis:custody_hardlink_refused"):
        _analyze(source, output, raw)
    (source / MEMBER).unlink()
    os.mkfifo(source / MEMBER)
    with pytest.raises(AnalysisRefusal, match="analysis:custody_not_regular"):
        _analyze(source, output, raw)
    assert list(output.iterdir()) == []


def test_partial_write_interruption_preserves_primary_and_removes_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    publish_member(source, MEMBER, raw)

    def interrupted_write(fd: int, data: bytes) -> None:
        assert os.write(fd, data[:1]) == 1
        raise KeyboardInterrupt("injected-after-real-write")

    monkeypatch.setattr(custody, "_write_all", interrupted_write)
    with pytest.raises(KeyboardInterrupt, match="injected-after-real-write"):
        _analyze(source, output, raw)
    assert list(output.iterdir()) == []
    assert (source / MEMBER).read_bytes() == raw


def test_six_concurrent_publishers_leave_exactly_one_complete_archive(tmp_path: Path) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    publish_member(source, MEMBER, raw)
    barrier = threading.Barrier(6)

    def worker(_idx: int) -> str:
        barrier.wait(timeout=10)
        try:
            result = _analyze(source, output, raw)
            _read_complete(output / BUNDLE)
            return result["bundle_sha256"]
        except AnalysisRefusal as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, range(6)))
    assert sum(result.startswith("sha256:") for result in results) == 1
    assert results.count("analysis:output_destination_exists") == 5
    assert file_inventory(output) == (BUNDLE,)
    _read_complete(output / BUNDLE)


def test_changed_input_refuses_before_output(tmp_path: Path) -> None:
    source, output = _roots(tmp_path)
    raw = synthetic_bytes()
    publish_member(source, MEMBER, raw + b" ")
    with pytest.raises(AnalysisRefusal, match="analysis:raw_digest_mismatch"):
        _analyze(source, output, raw)
    assert list(output.iterdir()) == []
