"""Synthetic cumulative recovery and actual copy/plan/entrypoint adverse cases."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import qualify_pgvector_prebuilt as launch
import runner_pgvector_recovery as recovery
from runner_pgvector_budget import Allocation, CAPS, INITIAL_CAPS, RECOVERY2_CAPS
from runner_pgvector_declaration import plan_common, write_once
from runner_pgvector_lineage import require_attempt
from runner_pgvector_prebuilt import SDK_IMAGE, digest, json_bytes
from runner_pgvector_sandbox import OfflineSandbox
from repomap_test_support.pgvector_prebuilt_launch_cases import declaration_fixture, derived_fixture


def fixture(tmp_path: Path, monkeypatch: Any, case: str) -> tuple[Path, Path, Path, Path, dict[str, bytes]]:
    source, declaration, document = declaration_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, "verify_checkout", lambda *_a: None)
    previous = tmp_path / "previous"
    previous.mkdir(mode=0o700)
    session = tmp_path / "recovery"
    old_document = copy.deepcopy(document)
    old_document["payload"]["caps"] = dict(RECOVERY2_CAPS)
    derived = derived_fixture()
    archive = previous / "base-runtime.tar"
    write_once(archive, b"archive")
    core = {**plan_common(old_document), "role": "core", "core_attempt": 2, "derived": derived,
        "allocation_path": str(previous / "allocation.json"),
        "export_path": str(previous / "runtime-export.tar"),
        "archive": {"path": str(archive), "bytes": 7, "sha256": digest(b"archive")}}
    used = dict.fromkeys(INITIAL_CAPS, 0)
    used.update(read_bytes=1000, transfer_bytes=1000, materializations=1,
                sdk_core=2, base_loads=2, runtime_exports=2)
    if case == "counter":
        used["sdk_core"] = 0
    if case == "forecast":
        monkeypatch.setattr(recovery, "MAX_ARCHIVE", CAPS["transfer_bytes"])
    old: dict[str, Any] = {"schema": "cloe-pgvector-allocation-v1", "caps": dict(RECOVERY2_CAPS), "used": used,
        "records": {"derived_archive": derived,
            "core_export": {"bytes": 6, "sha256": digest(b"export"),
                "runtime_config": derived["runtime_config"], "validated_ordered_layers": True},
            "core_result": {"exit": 1, "cleanup_proven": False, "candidate_head": "baseline",
                "candidate_snapshot_sha256": digest(json_bytes(core["candidate_snapshot"]))}}}
    first_used = dict.fromkeys(INITIAL_CAPS, 0)
    first_used.update(read_bytes=500, transfer_bytes=500, materializations=1,
                      sdk_core=1, base_loads=1, runtime_exports=1)
    first_parent = {"schema": "cloe-pgdg-core-recovery-parent-v1", "phase": "CLOE-W5-PGDG1",
        "previous_allocation_sha256": digest(b"first allocation"),
        "previous_plan_sha256": digest(b"first plan"), "previous_result_sha256": digest(b"first result"),
        "original_caps": dict(INITIAL_CAPS), "original_used": first_used,
        "original_records": copy.deepcopy(old["records"])}
    old["records"]["recovery_parent"] = first_parent
    core["recovery_parent_sha256"] = digest(json_bytes(first_parent))
    result = {"phase": "CLOE-W5-PGDG1", "role": "core", "exit": 1, "pytest_summaries": [],
        "source_preserved": True, "host_resources_preserved": True,
        "owned_process_cleanup_proven": case != "cleanup", "remaining_process_count": 0,
        "runtime_allocation_state": old, "offline_operations_zero": True, "phase_passed": False}
    rows: dict[str, Any] = {}
    for name, value in [("allocation.json", old), ("core.plan.json", core), ("result", result)]:
        path = previous / ("result.json" if name == "result" else name)
        raw = json_bytes(value)
        write_once(path, raw)
        rows[name] = {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}
    authority: dict[str, Any] = {"schema": recovery.AUTHORITY_SCHEMA, "phase": "CLOE-W5-PGDG1",
        "caps": CAPS, "previous_caps": RECOVERY2_CAPS, "core_attempt": 3, "expected_previous_used": used,
        "previous_session": str(previous), "recovery_session": str(session), "previous_files": rows}
    for key in ("operator_selection", "decision", "annex"):
        path = tmp_path / (key + ".md")
        raw = b"public synthetic recovery scope"
        write_once(path, raw)
        authority[key] = {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}
    if case == "caps":
        authority["caps"] = {**CAPS, "materializations": 2}
    if case == "authority":
        authority["previous_files"]["allocation.json"]["sha256"] = "0" * 64
    if case == "archive":
        archive.write_bytes(b"archivE")
    if case == "pending":
        write_once(previous / "allocation.next", b"uncertain prior write")
    key = "corrective_selection_sha256"
    row = document["authority"][key]
    raw = json_bytes(authority)
    Path(row["path"]).write_bytes(raw)
    row.update(bytes=len(raw), sha256=digest(raw))
    document["payload"][key] = digest(raw)
    declaration.write_bytes(json_bytes(document))
    frozen = {p.name: p.read_bytes() for p in previous.iterdir() if p.is_file()}
    return source, declaration, previous, session, frozen


def recovery_case(tmp_path: Path, monkeypatch: Any, case: str) -> None:
    if case == "cli-error":
        def fail(**_kwargs: Any) -> dict[str, Any]:
            raise OSError("fixture-private-detail /Users/example/do-not-export")
        monkeypatch.setattr(recovery, "recover_core", fail)
        monkeypatch.setattr(sys, "argv", ["qualify_pgvector_prebuilt.py", "recover-core",
            "--source", str(tmp_path), "--declaration", str(tmp_path / "decl.json"),
            "--session", str(tmp_path / "new"), "--previous", str(tmp_path / "old")])
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            assert launch.main() == 2
        observed = json.loads(errors.getvalue())
        assert observed["error_type"] == "OSError" and observed["code"] == "preparation_failed"
        assert "fixture-private-detail" not in output.getvalue() + errors.getvalue()
        return
    source, declaration, previous, session, frozen = fixture(tmp_path, monkeypatch, case)
    kwargs = {"source": source, "declaration": declaration, "previous": previous, "session": session}
    if case == "rematerialize":
        with pytest.raises(RuntimeError, match="cannot rematerialize"):
            launch.materialize(source=source, declaration=declaration, session=session,
                               base=tmp_path, index=tmp_path, package=tmp_path)
        assert not session.exists()
        assert {p.name: p.read_bytes() for p in previous.iterdir() if p.is_file()} == frozen
        return
    if case in {"caps", "cleanup", "counter", "archive", "pending", "forecast", "authority"}:
        with pytest.raises(RuntimeError):
            recovery.recover_core(**kwargs)
        assert not (session / "core.plan.json").exists()
        if case == "archive":
            with Allocation(session / "allocation.json").session() as meter:
                assert meter.used["read_bytes"] == 1007 and meter.used["transfer_bytes"] == 1007
                assert meter.used["materializations"] == 1 and meter.used["sdk_core"] == 2
    else:
        receipt = recovery.recover_core(**kwargs)
        assert receipt["new_materializations"] == 0 and receipt["cumulative_materializations"] == 1
        plan = json.loads((session / "core.plan.json").read_bytes())
        assert (session / "base-runtime.tar").read_bytes() == b"archive"
        budget = Allocation(session / "allocation.json")
        with budget.session() as meter:
            assert meter.used["read_bytes"] == meter.used["transfer_bytes"] == 1007
            assert meter.used["materializations"] == 1 and meter.used["sdk_core"] == 2
            parent = meter.records["recovery_parent"]
            assert parent["original_records"]["core_result"]["exit"] == 1
            assert parent["original_records"]["core_result"]["cleanup_proven"] is False
            assert parent["original_records"]["recovery_parent"]["original_records"]["core_result"]["exit"] == 1
            assert require_attempt(meter, plan) == 3
        if case == "replay":
            with pytest.raises(RuntimeError, match="fresh recovery"):
                recovery.recover_core(**kwargs)
        elif case in {"decrease", "parent"}:
            raw = json.loads(budget.path.read_bytes())
            if case == "decrease":
                raw["used"]["read_bytes"] = 999
            else:
                raw["records"]["recovery_parent"]["original_records"]["core_result"]["exit"] = 0
                plan["recovery_parent_sha256"] = digest(json_bytes(raw["records"]["recovery_parent"]))
            budget.path.write_bytes(json_bytes(raw))
            with budget.session() as meter, pytest.raises(RuntimeError):
                require_attempt(meter, plan)
        elif case == "ordinal1":
            stale = dict(plan, core_attempt=1)
            stale.pop("recovery_parent_sha256")
            with budget.session() as meter, pytest.raises(RuntimeError, match="unexpected recovery"):
                require_attempt(meter, stale)
        elif case == "outer":
            context: Any = SimpleNamespace(plan=plan, fixed=lambda: None, allocation=lambda: budget)
            sandbox = OfflineSandbox(context, lambda *_a: None,
                lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
            sandbox.outer_command(["docker", "run", SDK_IMAGE])
            with pytest.raises(RuntimeError, match="already spent"):
                sandbox.outer_command(["docker", "run", SDK_IMAGE])
        elif case in {"native-blocked", "native"}:
            if case == "native":
                derived = plan["derived"]
                write_once(session / "runtime-export.tar", b"export2")
                with budget.session() as meter:
                    for key in ("sdk_core", "base_loads", "runtime_exports"):
                        meter.charge(key, 1)
                    meter.bind("core_export", {"bytes": 7, "sha256": digest(b"export2"),
                        "runtime_config": derived["runtime_config"], "validated_ordered_layers": True})
                    meter.bind("core_result", {"exit": 0, "cleanup_proven": True,
                        "candidate_head": plan["candidate_head"],
                        "candidate_snapshot_sha256": digest(json_bytes(plan["candidate_snapshot"]))})
                launch.native_plan(source=source, declaration=declaration, session=session)
                native = json.loads((session / "native.plan.json").read_bytes())
                assert native["core_attempt"] == 3 and native["recovery_parent_sha256"] == plan["recovery_parent_sha256"]
            else:
                with pytest.raises(RuntimeError, match="prerequisites"):
                    launch.native_plan(source=source, declaration=declaration, session=session)
                assert not (session / "native.plan.json").exists()
    assert {p.name: p.read_bytes() for p in previous.iterdir() if p.is_file()} == frozen
