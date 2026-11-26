"""Doubles-only native owner plus real report-rewrite/export ownership cases."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

from runner_pgvector_context import CORE_SELECTOR, NATIVE_SELECTOR, validate_cohort
from runner_pgvector_sandbox import OfflineSandbox
from test_sandbox_arguments import sandbox_report_arguments
from test_sandbox_contract import INNER_REPORT_ROOT
from test_sandbox_report import export_sandbox_report
from common_lore_index_test_support.pgvector_prebuilt_test_fixtures import tar_bytes


def report_seam_case(tmp_path: Path) -> None:
    destination = tmp_path / "native-report"
    argv = ["--suite", "int", "--no-coverage", "--pg-container-port", "55433",
            "--pg-test-profile", "pgvector", "--report-dir", str(destination), "--", NATIVE_SELECTOR]
    # Ordinary callers preserve the existing no-report behavior.
    ordinary = sandbox_report_arguments(argv, repo_root=tmp_path)
    assert ordinary.inner_argv == argv and ordinary.report_destination is None
    context: Any = SimpleNamespace(fixed=lambda: None)
    callback = OfflineSandbox(context, lambda *_a, **_k: None,
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    result = callback.report_arguments(argv, repo_root=tmp_path)
    assert result.report_destination == destination
    assert result.inner_argv[result.inner_argv.index("--report-dir") + 1] == str(INNER_REPORT_ROOT)
    assert "--report" not in result.inner_argv and "--d12-core-image-preflight" not in result.inner_argv
    validate_cohort(result.inner_argv, "native")
    receipt = b'{"native_qualification":"passed","synthetic":true}\n'
    relative = Path("/sandbox-report/report/pgvector-native-qualification.json").relative_to(INNER_REPORT_ROOT)
    archive = tar_bytes([(relative.as_posix(), receipt, "file")])
    export_sandbox_report(archive, destination, max_members=8, max_content_bytes=65536)
    assert (destination / relative).read_bytes() == receipt


def native_owner_case(tmp_path: Path, monkeypatch: Any, module: Any) -> None:
    # Exercise the actual owner's body with the unit owner's isolated module.
    image = "sha256:" + "3" * 64
    plan = {"role": "native", "derived": {"runtime_config": image}}
    calls: list[str] = []
    def load(environment: Any) -> dict[str, Any]:
        calls.append("sealed_plan")
        assert environment["REPOMAP_TEST_PG_IMAGE"] == image
        return plan
    report = {"native_qualification": "passed", "cleanup_proven": True,
              "native_lifecycles": 1, "jit": "off", "production_or_scientific_admission": False}
    def qualify(execute: Any, event: Any, identity: str) -> dict[str, Any]:
        assert identity == image
        calls.append("fake_qualifier")
        return report
    monkeypatch.setenv("REPOMAP_TEST_PG_IMAGE", image)
    monkeypatch.setattr(module, "load_inner_plan", load)
    monkeypatch.setattr(module, "qualify", qualify)
    monkeypatch.setattr(module, "_SetupCommands", lambda *_a: SimpleNamespace(execute=None, event=None))
    receipt = tmp_path / "native.json"
    monkeypatch.setattr(module, "Path", lambda path: receipt)
    module.test_prebuilt_runtime_native_extension_index_rollback_and_cleanup()
    assert calls == ["sealed_plan", "fake_qualifier"]
    assert json.loads(receipt.read_bytes()) == report


def report_reader_case(tmp_path: Path, monkeypatch: Any) -> None:
    import test_sandbox_execution
    from common_lore_index_test_support.pgvector_prebuilt_budget_cases import allocation
    budget = allocation(tmp_path)
    context: Any = SimpleNamespace(fixed=lambda: None, allocation=lambda: budget)
    calls: list[bool] = []
    def reader(container: str, required: bool, **kwargs: Any) -> bytes | None:
        assert container == "owned"
        calls.append(required)
        assert kwargs["max_archive_bytes"] == 4 * 1024**2
        state = json.loads(budget.path.read_bytes())
        assert state["used"]["read_bytes"] == state["used"]["transfer_bytes"] == 4 * 1024**2
        return b"public report" if required else None
    monkeypatch.setattr(test_sandbox_execution, "read_container_report_archive", reader)
    callback = OfflineSandbox(context, lambda *_a: None,
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    assert callback.read_report_archive("owned", False) is None
    with budget.session() as meter:
        assert meter.used["read_bytes"] == meter.used["transfer_bytes"] == 0
    assert callback.read_report_archive("owned", True) == b"public report"
    with budget.session() as meter:
        assert meter.used["read_bytes"] == meter.used["transfer_bytes"] == len(b"public report")
    assert calls == [False, True]


def core_report_seam_case(tmp_path: Path) -> None:
    # Use the real report writer/exporter with public data, not native evidence.
    from runner_failure_evidence import FailureEvidence
    from staging_report_contract import persist_staging_report
    destination = tmp_path / "core-report"
    argv = ["--suite", "int", "--no-coverage", "--pg-container-port", "55433",
            "--report-dir", str(destination), "--", CORE_SELECTOR]
    context: Any = SimpleNamespace(fixed=lambda: None)
    callback = OfflineSandbox(context, lambda *_a, **_k: None,
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    rewritten = callback.report_arguments(argv, repo_root=tmp_path)
    assert rewritten.report_destination == destination
    assert rewritten.inner_argv[rewritten.inner_argv.index("--report-dir") + 1] == str(INNER_REPORT_ROOT)
    assert "--report" not in rewritten.inner_argv and "--no-coverage" in rewritten.inner_argv
    validate_cohort(rewritten.inner_argv, "core")
    inner = tmp_path / "inner-report"
    output = inner / "int/latest/staging_contract_report.json"
    observed = {"invocation_id": "public-synthetic", "errors": [], "scoped": True}
    assert not inner.exists()
    evidence = FailureEvidence(output, observed, contract_writer=persist_staging_report)
    assert evidence.persist_contract() and inner.is_dir()
    raw = output.read_bytes()
    assert json.loads(raw) == observed
    relative = output.relative_to(inner).as_posix()
    export_sandbox_report(tar_bytes([(relative, raw, "file")]), destination,
                          max_members=8, max_content_bytes=65536)
    assert (destination / relative).read_bytes() == raw
