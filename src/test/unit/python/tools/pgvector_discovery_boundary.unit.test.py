"""Bounded offline discovery/diagnostic/third-lineage regressions; no native admission."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from repomap_test_support.pgvector_prebuilt_discovery_cases import (
    environment_mismatch_case, harmless_child_case, nested_lineage_case,
    optional_report_case, projection_case, refusal_case, lost_authority_case,
    canonical_request_case, maximal_capture_export_case,
    partial_optional_report_case,
)


@pytest.mark.parametrize("role", ["core", "native"])
def test_authenticated_exact_plan_survives_actual_discovery_filter(monkeypatch: Any, role: str) -> None:
    projection_case(monkeypatch, role)


@pytest.mark.parametrize("case", ["ordinary", "unauthenticated", "stale", "malformed", "profile",
    "role", "head", "suite", "unscoped", "cwd", "extra-selector", "scratch", "cache", "node", "marker"])
def test_discovery_refuses_before_pytest_or_ordinary_is_unchanged(monkeypatch: Any, case: str) -> None:
    refusal_case(monkeypatch, case)


def test_actual_child_refuses_environment_loss_before_pytest(tmp_path: Path, monkeypatch: Any) -> None:
    environment_mismatch_case(tmp_path, monkeypatch)


@pytest.mark.parametrize("marker", ["absent", "file", "link", "unreadable"])
def test_actual_session_refuses_lost_authority_before_ordinary_setup(
    tmp_path: Path, monkeypatch: Any, marker: str,
) -> None:
    lost_authority_case(tmp_path, monkeypatch, marker)


def test_real_argument_identity_and_layout_builders_match_handoff(tmp_path: Path, monkeypatch: Any) -> None:
    canonical_request_case(tmp_path, monkeypatch)


def test_maximal_private_capture_exports_with_real_report_bounds(tmp_path: Path, monkeypatch: Any) -> None:
    maximal_capture_export_case(tmp_path, monkeypatch)


def test_partial_optional_report_failure_keeps_consumed_reservation(tmp_path: Path, monkeypatch: Any) -> None:
    partial_optional_report_case(tmp_path, monkeypatch)


def test_real_pytest_collect_only_is_compatible_with_prefix_streams(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[5]
    subject = tmp_path / "public_subject.py"
    subject.write_text("def test_public_subject():\n    raise AssertionError('body must not execute')\n")
    driver = tmp_path / "collect_driver.py"
    driver.write_text(dedent('''
        import sys
        from pathlib import Path
        source, root = Path(sys.argv[1]), Path(sys.argv[2])
        sys.path.insert(0, str(source/'tools'))
        import pytest
        import runner_pgvector_discovery_capture as captures
        captures.REPORT_ROOT = root/'private-report'
        capture = captures.CollectionCapture({'invocation_id':'public-real-collect'},
            {'role':'core','candidate_head':'a'*40})
        with capture.streams():
            code = int(pytest.main(['--collect-only','-q','--confcutdir',str(root),
                '-o','addopts=','-p','no:cacheprovider',str(root/'public_subject.py')]))
        capture.persist(code, None)
        raise SystemExit(code)
    '''))
    environment = {k:v for k,v in os.environ.items()
        if not k.startswith(('PYTEST_', 'COVERAGE_'))}
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    completed = subprocess.run([sys.executable, '-I', '-B', str(driver), str(source), str(tmp_path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (completed.returncode, completed.stderr[:4096])
    facts = json.loads((tmp_path/'private-report/offline-discovery-capture.public-real-collect.json').read_bytes())
    assert facts['pytest_exit'] == 0 and facts['stdout']['retained_bytes'] > 0
    assert facts['qualification_evidence'] is False


def test_actual_harmless_failed_child_retains_bounded_collection_prefixes(tmp_path: Path) -> None:
    harmless_child_case(tmp_path)


@pytest.mark.parametrize("case", ["present", "optional-absent", "required-absent", "error", "oversize"])
def test_failure_report_attempt_is_bounded_and_keeps_uncertain_reservations(
    tmp_path: Path, monkeypatch: Any, case: str,
) -> None:
    optional_report_case(tmp_path, monkeypatch, case)


@pytest.mark.parametrize("case", ["nested-failure", "nested-counter", "ordinal4", "skip-core2"])
def test_third_attempt_cannot_discard_nested_failure_or_skip_predecessor(
    tmp_path: Path, monkeypatch: Any, case: str,
) -> None:
    nested_lineage_case(tmp_path, monkeypatch, case)


def test_prefix_is_a_contiguous_utf8_bounded_projection() -> None:
    from runner_pgvector_discovery_capture import PrefixStream
    stream = PrefixStream()
    assert stream.write("x" * 65535) == 65535
    assert stream.write("é") == 1
    assert stream.write("later") == 5
    facts = stream.facts()
    assert base64.b64decode(facts["prefix_base64"]) == b"x" * 65535
    assert facts["retained_bytes"] == 65535 and facts["dropped_characters"] == 6
    assert facts["truncated"] is True


def test_capture_write_once_and_directory_link_refusal(tmp_path: Path, monkeypatch: Any) -> None:
    import runner_pgvector_discovery_capture as captures
    report = tmp_path / "report"
    monkeypatch.setattr(captures, "REPORT_ROOT", report)
    capture = captures.CollectionCapture({"invocation_id": "public-once"}, {"role": "core", "candidate_head": "a"*40})
    capture.persist(2, "RuntimeError")
    with pytest.raises(FileExistsError):
        capture.persist(2, "RuntimeError")
    facts = json.loads((report / "offline-discovery-capture.public-once.json").read_bytes())
    assert facts["pytest_exit"] == 2 and facts["qualification_evidence"] is False
    link = tmp_path / "alias"
    link.symlink_to(report, target_is_directory=True)
    monkeypatch.setattr(captures, "REPORT_ROOT", link)
    with pytest.raises(RuntimeError, match="directory link"):
        capture.persist(2, "RuntimeError")


def test_exact_report_pipe_cannot_read_beyond_reservation() -> None:
    from runner_pgvector_sandbox import _ReportProcess
    process = subprocess.Popen([sys.executable, "-B", "-c", "import sys; sys.stdout.buffer.write(b'public-prefix-overflow')"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        bounded = _ReportProcess(process, 6)
        assert bounded.read(65536) == b"public"
        with pytest.raises(RuntimeError, match="cap reached"):
            bounded.read(65536)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        assert process.stdout is not None
        process.stdout.close()
