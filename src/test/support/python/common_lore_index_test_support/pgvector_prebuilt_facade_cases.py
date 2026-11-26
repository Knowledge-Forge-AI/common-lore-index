"""Doubles-only facade/dispatch ownership assertions for the allocated unit owner."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from typing import Any
SDK_IMAGE = "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42"


def facade_case(monkeypatch: Any, *, canonical_error: bool = False) -> dict[str, Any]:
    """Verify facade callback wiring and that ``finished`` follows execution."""
    import runner_pgvector_context as contexts
    import runner_pgvector_sandbox as sandbox_module
    import test_sandbox as facade
    calls: list[str] = []
    context = SimpleNamespace(plan={}, fixed=lambda: None)

    class Offline:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None: calls.append("construct")
        def outer(self, **_kwargs: Any) -> list[str]: calls.append("outer"); return []
        def boundary(self, *_args: Any, **_kwargs: Any) -> None: calls.append("boundary")
        def prepare(self, *_args: Any, **_kwargs: Any) -> None: calls.append("prepare")
        def run_tests(self, *_args: Any, **_kwargs: Any) -> int: calls.append("run_tests"); return 0
        def report_arguments(self, argv: list[str], **kwargs: Any): return argv, None
        def read_report_archive(self, *args: Any) -> bytes: return b"synthetic"
        def finished(self, result: int) -> None: calls.append("finished:" + str(result))

    def execution(*_args: Any, **kwargs: Any) -> int:
        for name in ("outer_run_command", "prepare_images", "run_inner_tests", "cleanup_outer"):
            assert name in kwargs
        callbacks = ("outer_run_command", "boundary_prover", "prepare_images", "run_inner_tests", "report_arguments", "archive_reader")
        assert all(isinstance(kwargs[key].__self__, Offline) for key in callbacks)
        assert len({id(kwargs[key].__self__) for key in callbacks}) == 1
        calls.append("execution")
        if canonical_error:
            raise RuntimeError("canonical cleanup failed")
        calls.append("canonical_cleanup")
        return 0

    monkeypatch.setattr(contexts, "host_context", lambda *_a, **_k: context)
    monkeypatch.setattr(sandbox_module, "OfflineSandbox", Offline)
    monkeypatch.setattr(facade._execution, "run_in_sandbox", execution)
    try:
        facade.run_in_sandbox(["--", "selector"], repo_root=Path("/workspace"), image_id=SDK_IMAGE)
    except RuntimeError:
        if not canonical_error:
            raise
    else:
        if canonical_error:
            raise AssertionError("canonical execution failure was swallowed")
    assert ("finished:0" in calls) is (not canonical_error)
    if not canonical_error:
        assert calls.index("canonical_cleanup") < calls.index("finished:0")
    return {"calls": calls}


def dispatch_case(monkeypatch: Any, *, pgvector: bool = True, failure: str | None = None) -> dict[str, Any]:
    """Prove pgvector dispatch uses the cached SDK and never the build fallback."""
    import runner_pgvector_context as contexts
    import test_sandbox as facade
    import test_sandbox_dispatch as dispatch
    import test_sandbox_ownership as ownership
    import common_lore_index_test_support.unit_purity as purity
    args = SimpleNamespace(suite="int", pg_test_profile="pgvector" if pgvector else "core",
                           d12_system_proof_json=None)
    calls: list[str] = []
    context = SimpleNamespace(plan={"sdk_image": SDK_IMAGE}) if pgvector else None
    monkeypatch.setattr(facade, "active_sandbox", lambda: False)
    def cached(**kwargs: Any) -> str:
        calls.append("cached")
        assert kwargs["expected_image_id"] == SDK_IMAGE
        if failure == "cache":
            raise RuntimeError("cache miss")
        return SDK_IMAGE
    monkeypatch.setattr(facade, "cached_sandbox_image", cached)
    monkeypatch.setattr(facade, "ensure_sandbox_image", lambda **_k: (_ for _ in ()).throw(AssertionError("ensure fallback used")))
    def run(*_args: Any, **_kwargs: Any) -> int:
        calls.append("run")
        return 0
    monkeypatch.setattr(facade, "run_in_sandbox", run)
    monkeypatch.setattr(ownership, "prepare_ownership_reporter", lambda *_a, **_k: None)
    monkeypatch.setattr(purity, "require_live_resource_allowed", lambda *_a, **_k: None)
    monkeypatch.setattr(contexts, "host_context", lambda *_a, **_k: None if failure == "plan" else context)
    def native() -> None:
        calls.append("native")
        if failure == "host":
            raise RuntimeError("unsupported host")
    monkeypatch.setattr(contexts, "require_native_host", native)
    monkeypatch.setattr(dispatch, "verify_host_system_proof_for_dispatch", lambda *_a, **_k: None)
    result = dispatch.integration_sandbox_dispatch(args, ["--suite", "int", "--no-coverage"])
    if failure is None:
        assert result == 0 and "cached" in calls and "run" in calls
    else:
        assert result == 2 and "run" not in calls
        if failure in {"host", "plan"}:
            assert "cached" not in calls
    return {"result": result, "calls": calls}
