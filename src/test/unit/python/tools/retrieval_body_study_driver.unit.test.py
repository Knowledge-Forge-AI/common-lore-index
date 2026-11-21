"""Unit ownership for body ordering and journal-before-slot requirements."""
from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

from retrieval_body_study_driver import run_actual_body_study


def test_driver_requires_journal_before_any_worker_or_query_callback() -> None:
    calls: list[str] = []

    def exchange(_request):
        calls.append("exchange")
        return {}

    preparation = SimpleNamespace(require_current=lambda: None)
    requests = SimpleNamespace(require_current=lambda: None)
    with pytest.raises(ValueError, match="body.driver:journal"):
        run_actual_body_study(
            preparation=cast(Any, preparation), requests=cast(Any, requests), exchange=exchange,
            pins={}, execute_slot=lambda *_: {}, journal=None, cleanup={},
        )
    assert calls == []


def test_driver_preserves_primary_and_failure_evidence_for_immutable_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    digest = "sha256:" + "a" * 64
    finalized: list[str] = []
    cleanup_snapshots: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    terminal_calls: list[dict[str, Any]] = []

    def finalize() -> None:
        finalized.append("once")
        raise RuntimeError("secondary cleanup failure")

    def build_failure(**kwargs: Any) -> dict[str, Any]:
        cleanup_snapshots.append(dict(kwargs["cleanup"]))
        return {"report_identity": digest}

    def write_failure(_root, envelope, **kwargs: Any) -> str:
        writes.append(dict(kwargs))
        return cast(str, envelope["report_identity"])

    monkeypatch.setattr("retrieval_body_study_driver.build_body_failure_envelope", build_failure)
    monkeypatch.setattr("retrieval_body_study_driver.write_body_failure_envelope", write_failure)
    def terminal(**kwargs: Any) -> dict[str, Any]:
        terminal_calls.append(kwargs)
        return {"event": "terminal"}

    journal = SimpleNamespace(root=tmp_path / "journal", terminal=terminal)
    cleanup = MappingProxyType({"_finalize_cleanup": finalize, "cleanup_ok": False})

    def source_guard() -> None:
        raise ValueError("primary source failure")

    with pytest.raises(ValueError, match="primary source failure"):
        run_actual_body_study(
            preparation=cast(Any, SimpleNamespace()),
            requests=cast(Any, SimpleNamespace(
                v1=SimpleNamespace(descriptor={"attempt_id": "attempt-1"}),
            )),
            exchange=lambda _request: {},
            pins={},
            execute_slot=lambda *_: {},
            journal=cast(Any, journal),
            cleanup=cleanup,
            source_guard=source_guard,
        )

    assert finalized == ["once"]
    assert dict(cleanup) == {"_finalize_cleanup": finalize, "cleanup_ok": False}
    assert len(cleanup_snapshots) == 2
    assert cleanup_snapshots[0] == {"cleanup_ok": False}
    assert cleanup_snapshots[1]["cleanup_error"] == "RuntimeError"
    assert writes[0]["filename"].endswith("body-failure-evidence-v1.json")
    assert writes[1] == {}
    assert terminal_calls[0]["failure_envelope_sha256"] == digest
    assert terminal_calls[0]["outcome"] == "incomplete_technical"
