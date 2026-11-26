"""Public synthetic declaration, materialization and plan-launch ownership cases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import qualify_pgvector_prebuilt as launch
import runner_pgvector_declaration as declarations
from runner_pgvector_archive import runtime_config_for_diff_id
from runner_pgvector_budget import Allocation, CAPS
from runner_pgvector_declaration import AUTHORITY_KEYS, load_declaration, write_once
from runner_pgvector_prebuilt import digest, json_bytes, provider_facts


def declaration_fixture(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path, dict[str, Any]]:
    source = tmp_path / "source"
    source.mkdir()
    snapshot = {f"public-{i}.py": {"mode": 0o644, "sha256": "1" * 64} for i in range(101)}
    payload: dict[str, Any] = {"schema": declarations.DECLARATION_SCHEMA,
        "phase": "CLOE-W5-PGDG1", "caps": CAPS, "provider_sha256": declarations.PROVIDER_SHA256,
        "sdk_image": declarations.SDK_IMAGE, "candidate_head": "baseline",
        "candidate_snapshot_sha256": digest(json_bytes(snapshot))}
    authority: dict[str, Any] = {}
    for key in sorted(AUTHORITY_KEYS):
        raw = (json_bytes({"stage": "work_review", "body": "SOURCE-ADOPTION-GATE: PASS\nsynthetic"})
               if key == "review1_receipt_sha256" else ("synthetic " + key).encode())
        path = tmp_path / (key + ".json")
        write_once(path, raw)
        payload[key] = digest(raw)
        authority[key] = {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}
    document: dict[str, Any] = {"payload": payload, "authority": authority, "candidate_snapshot": snapshot}
    path = tmp_path / "declaration.json"
    write_once(path, json_bytes(document))
    def verify(root: Path, observed: dict[str, Any], head: str) -> None:
        assert root == source and observed == snapshot and head == "baseline"
    monkeypatch.setattr(declarations, "verify_checkout", verify)
    monkeypatch.setattr(launch, "verify_checkout", verify)
    return source, path, document


def declaration_case(tmp_path: Path, monkeypatch: Any, mutation: str) -> None:
    source, path, document = declaration_fixture(tmp_path, monkeypatch)
    if mutation == "source":
        document["candidate_snapshot"]["extra.py"] = {"mode": 0o644, "sha256": "2" * 64}
    elif mutation == "authority":
        target = Path(document["authority"]["operator_decision_sha256"]["path"])
        target.write_bytes(b"changed")
    elif mutation == "blocked":
        key = "review1_receipt_sha256"
        raw = json_bytes({"stage": "work_review", "body": "SOURCE-ADOPTION-GATE: BLOCK"})
        row = document["authority"][key]
        Path(row["path"]).write_bytes(raw)
        row.update(bytes=len(raw), sha256=digest(raw))
        document["payload"][key] = digest(raw)
    elif mutation == "caps":
        document["payload"]["caps"] = {**CAPS, "materializations": 2}
    elif mutation == "zero":
        document["payload"]["operator_decision_sha256"] = "0" * 64
    path.write_bytes(json_bytes(document))
    if mutation == "none":
        assert load_declaration(path, source) == document
    else:
        with pytest.raises(RuntimeError):
            load_declaration(path, source)


def derived_fixture() -> dict[str, Any]:
    facts = provider_facts()
    raw_base = "".join(facts["base_config_utf8_chunks"])
    base = json.loads(raw_base)
    runtime = runtime_config_for_diff_id(base, "sha256:" + "d" * 64)
    return {"base_config": facts["base_config"], "base_config_document": base,
        "base_config_raw": raw_base, "runtime_config_document": runtime,
        "runtime_config": "sha256:" + digest(json_bytes(runtime)),
        "runtime_layers": [{"sha256": item[7:], "bytes": 1} for item in runtime["rootfs"]["diff_ids"]],
        "bytes": len(b"archive"), "sha256": digest(b"archive")}


def launch_case(tmp_path: Path, monkeypatch: Any, mutation: str) -> None:
    source, declaration, document = declaration_fixture(tmp_path, monkeypatch)
    session = tmp_path / "session"
    derived = derived_fixture()
    events: list[str] = []
    def forecast(base: Path, meter: Allocation) -> dict[str, int]:
        events.append("forecast")
        return {"read_upper_bytes": 100, "read_headroom_bytes": CAPS["read_bytes"] - 100}
    def bundle(index: Path, package: Path, destination: Path, meter: Allocation) -> None:
        events.append("bundle")
        destination.mkdir()
    def materialize(base: Path, bundle: Path, destination: Path, meter: Allocation) -> dict[str, Any]:
        events.append("materialize")
        assert (session / "materialization-INTENT.json").is_file()
        meter.charge("materializations", 1)
        if mutation == "derive_failure":
            raise RuntimeError("synthetic derivation failure")
        write_once(destination, b"archive")
        meter.bind("derived_archive", derived)
        return derived
    monkeypatch.setattr(launch, "read_forecast", forecast)
    monkeypatch.setattr(launch, "copy_bundle", bundle)
    monkeypatch.setattr(launch, "derive_archive", materialize)
    args = {"source": source, "declaration": declaration, "session": session,
            "base": tmp_path, "index": tmp_path, "package": tmp_path}
    if mutation == "derive_failure":
        with pytest.raises(RuntimeError, match="synthetic derivation"):
            launch.materialize(**args)
        assert json.loads((session / "allocation.json").read_bytes())["used"]["materializations"] == 1
        with pytest.raises(RuntimeError, match="fresh session"):
            launch.materialize(**args)
        return
    launch.materialize(**args)
    assert events == ["forecast", "bundle", "materialize"]
    core = json.loads((session / "core.plan.json").read_bytes())
    assert core["diagnostic_declaration"] == document["payload"]
    with pytest.raises(RuntimeError, match="fresh session"):
        launch.materialize(**args)
    if mutation == "missing_core":
        with pytest.raises(RuntimeError, match="prerequisites"):
            launch.native_plan(source=source, declaration=declaration, session=session)
        assert not (session / "native.plan.json").exists()
        return
    exported: dict[str, Any] = {"bytes": len(b"export"), "sha256": digest(b"export"),
        "runtime_config": derived["runtime_config"], "validated_ordered_layers": True}
    write_once(session / "runtime-export.tar", b"export")
    with Allocation(session / "allocation.json").session() as meter:
        for key in ("sdk_core", "base_loads", "runtime_exports"):
            meter.charge(key, 1)
        meter.bind("core_export", exported)
        result: dict[str, Any] = {"exit": 0, "cleanup_proven": True, "candidate_head": core["candidate_head"],
                  "candidate_snapshot_sha256": digest(json_bytes(core["candidate_snapshot"]))}
        if mutation == "core_failure":
            result["exit"] = 1
        elif mutation == "cleanup":
            result["cleanup_proven"] = False
        elif mutation == "candidate":
            result["candidate_snapshot_sha256"] = "f" * 64
        elif mutation == "export_identity":
            exported["runtime_config"] = "sha256:" + "f" * 64
            # bind() owns the original dictionary; persist the deliberate fixture mutation.
        elif mutation == "native_spent":
            meter.charge("sdk_native", 1)
        meter.bind("core_result", result)
    if mutation == "export_size":
        (session / "runtime-export.tar").write_bytes(b"changed-size")
    if mutation == "none":
        launch.native_plan(source=source, declaration=declaration, session=session)
        native = json.loads((session / "native.plan.json").read_bytes())
        assert native["role"] == "native" and native["derived"] == derived
        assert native["archive"]["sha256"] == exported["sha256"]
        with pytest.raises(FileExistsError):
            launch.native_plan(source=source, declaration=declaration, session=session)
    else:
        with pytest.raises(RuntimeError):
            launch.native_plan(source=source, declaration=declaration, session=session)
        assert not (session / "native.plan.json").exists()


def bundle_case(tmp_path: Path, monkeypatch: Any, mutation: str) -> None:
    from common_lore_index_test_support.pgvector_prebuilt_budget_cases import allocation
    package, vector = b"public fake package", b"public fake library"
    row: dict[str, Any] = {"path": "usr/lib/vector.so", "bytes": len(vector), "sha256": digest(vector)}
    monkeypatch.setattr(declarations, "PACKAGE_SHA256", digest(package))
    monkeypatch.setattr(declarations, "provider_facts", lambda: {
        "version": "fixture", "package_bytes": len(package), "files": [row]})
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "vector.bin").write_bytes(vector)
    path = tmp_path / "package.deb"
    path.write_bytes(package)
    selected: dict[str, Any] = {"archive_path": row["path"], "retained": "artifacts/vector.bin",
                "bytes": len(vector), "sha256": digest(vector)}
    index: dict[str, Any] = {"packages": [{"payload_sha256": digest(package), "version": "fixture",
              "tar": [{"part": "data", "selected": [selected]}]}]}
    if mutation == "mapping":
        selected["sha256"] = "0" * 64
    elif mutation == "payload":
        (artifacts / "vector.bin").write_bytes(b"changed")
    elif mutation == "package":
        path.write_bytes(b"wrong")
    elif mutation == "duplicate":
        index["packages"][0]["tar"][0]["selected"].append(dict(selected))
    index_path = evidence / "index.json"
    index_path.write_bytes(json_bytes(index))
    output = tmp_path / "bundle"
    with allocation(tmp_path).session() as meter:
        if mutation == "none":
            declarations.copy_bundle(index_path, path, output, meter)
            assert (output / row["path"]).read_bytes() == vector
            assert (output / "pgvector.deb").read_bytes() == package
        else:
            with pytest.raises(RuntimeError):
                declarations.copy_bundle(index_path, path, output, meter)
        assert meter.used["materializations"] == meter.used["sdk_core"] == 0
