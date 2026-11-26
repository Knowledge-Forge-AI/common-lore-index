"""Doubles-only ownership helpers for the sealed offline sandbox callbacks.

The helpers are intentionally lazy: importing this support module does not
import the sandbox implementation or touch Docker.  Unit owners pass their
``monkeypatch`` fixture to the high-level cases below.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Iterator


SDK_IMAGE = "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42"
BASE_IMAGE = "sha256:2e7ed4ea03deedbb45c26c68bf2a7090dc68dd8518a9c75928c4b9b0f59bc411"
RUNTIME_IMAGE = "sha256:" + "3" * 64
PROVIDER = "b366c8507b36746ea1512c4a9a3d6819214b65203645b7c9555bd450d5f797ec"
HEAD = "a" * 40

CAPS = {
    "read_bytes": 12 * 1024**3, "transfer_bytes": 6 * 1024**3,
    "materializations": 1, "sdk_core": 3, "sdk_native": 1,
    "native_lifecycles": 1, "base_loads": 3, "runtime_exports": 3,
    "native_runtime_loads": 1,
}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        allow_nan=False) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _empty_used() -> dict[str, int]:
    return {name: 0 for name in (
        "read_bytes", "transfer_bytes", "materializations", "sdk_core", "sdk_native",
        "native_lifecycles", "base_loads", "runtime_exports", "native_runtime_loads",
    )}


@dataclass
class FakeMeter:
    """Small in-memory ledger that records charge-before-command ordering."""

    used: dict[str, int] = field(default_factory=_empty_used)
    records: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, str, int]] = field(default_factory=list)
    fail_charge: str | None = None

    def charge(self, key: str, amount: int) -> None:
        self.events.append(("charge", key, amount))
        if key == self.fail_charge:
            raise RuntimeError("synthetic charge failure: " + key)
        self.used[key] = self.used.get(key, 0) + amount

    def bind(self, name: str, value: dict[str, Any]) -> None:
        if name in self.records:
            raise RuntimeError("already bound allocation record")
        self.events.append(("bind", name, 1))
        self.records[name] = value

    def note(self, name: str) -> None:
        self.events.append(("operation", name, 0))


@dataclass
class FakeAllocation:
    meter: FakeMeter

    @contextmanager
    def session(self) -> Iterator[FakeMeter]:
        yield self.meter


@dataclass
class FakeContext:
    plan: dict[str, Any]
    meter: FakeMeter
    plan_path: Path
    repo_root: Path
    fixed_calls: int = 0

    def fixed(self) -> None:
        self.fixed_calls += 1

    def allocation(self) -> FakeAllocation:
        return FakeAllocation(self.meter)


def offline_plan(tmp_path: Path, role: str = "core", *, records: bool = True) -> dict[str, Any]:
    """Build a minimal plan accepted by the callback seams."""
    base = {"os": "linux", "architecture": "arm64",
            "config": {"Cmd": ["postgres"], "Entrypoint": ["postgres"], "Labels": {}},
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]}}
    runtime = {"os": "linux", "architecture": "arm64",
               "config": {"Cmd": ["postgres"], "Entrypoint": ["postgres"],
                           "Labels": {"owner": "synthetic"}},
               "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64,
                                                               "sha256:" + "b" * 64]}}
    derived: dict[str, Any] = {
        "base_config": BASE_IMAGE, "base_config_document": base,
        "runtime_config": RUNTIME_IMAGE, "runtime_config_document": runtime,
        "base_config_raw": _json_bytes(base).decode(),
        "runtime_layers": [{"sha256": "a" * 64, "bytes": 1},
                           {"sha256": "b" * 64, "bytes": 1}],
        "bytes": 11, "sha256": "d" * 64,
    }
    snapshot = {f"src/example-{index}.py": {"sha256": "e" * 64, "mode": 0o644}
                for index in range(101)}
    declaration = {
        "schema": "cloe-pgvector-diagnostic-declaration-v1", "phase": "CLOE-W5-PGDG1",
        "caps": CAPS, "provider_sha256": PROVIDER, "sdk_image": SDK_IMAGE,
        "candidate_head": HEAD, "candidate_snapshot_sha256": _sha256(_json_bytes(snapshot)),
        "review1_receipt_sha256": "f" * 64, "operator_decision_sha256": "9" * 64,
        "original_allocation_sha256": "1" * 64, "corrective_selection_sha256": "2" * 64,
        "signature_disposition_sha256": "3" * 64,
    }
    plan: dict[str, Any] = {
        "schema": "cloe-pgvector-offline-plan-v1", "phase": "CLOE-W5-PGDG1",
        "role": role, "provider_sha256": PROVIDER, "sdk_image": SDK_IMAGE,
        "candidate_head": HEAD, "candidate_snapshot": snapshot,
        "review1_receipt_sha256": "f" * 64, "operator_decision_sha256": "9" * 64,
        "original_allocation_sha256": "1" * 64, "corrective_selection_sha256": "2" * 64,
        "signature_disposition_sha256": "3" * 64,
        "diagnostic_declaration": declaration,
        "diagnostic_declaration_sha256": _sha256(_json_bytes(declaration)),
        "derived": derived, "archive": {"path": str(tmp_path / "runtime.tar"),
        "bytes": 11, "sha256": "d" * 64, "runtime_config": RUNTIME_IMAGE},
        "allocation_path": str(tmp_path / "allocation.json"),
        "export_path": str(tmp_path / "runtime-export.tar"),
    }
    if records:
        plan["_records"] = True
    return plan


def offline_context(tmp_path: Path, role: str = "core", *, records: bool = True) -> FakeContext:
    plan = offline_plan(tmp_path, role, records=records)
    meter = FakeMeter()
    if records:
        meter.used["materializations"] = 1
        meter.records["derived_archive"] = plan["derived"]
        if role == "native":
            meter.used.update({"sdk_core": 1, "base_loads": 1, "runtime_exports": 1})
            meter.records["core_result"] = {"exit": 0, "cleanup_proven": True,
                "candidate_head": HEAD,
                "candidate_snapshot_sha256": _sha256(_json_bytes(plan["candidate_snapshot"]))}
            meter.records["core_export"] = {"bytes": 11, "sha256": "d" * 64,
                                              "runtime_config": RUNTIME_IMAGE}
    return FakeContext(plan, meter, tmp_path / "plan.json", tmp_path)


def outer_command_case(tmp_path: Path, role: str = "core", failure: str | None = None) -> dict[str, Any]:
    """Exercise ``outer_command`` and return the recorded command/ledger."""
    from runner_pgvector_sandbox import OfflineSandbox
    context: Any = offline_context(tmp_path, role)
    if failure == "materialization":
        context.meter.used["materializations"] = 0
    if failure in {"archive", "archive_missing"}:
        context.meter.records.pop("derived_archive", None)
    if failure in {"core_export", "core_export_missing"} and role == "native":
        context.meter.records.pop("core_export", None)
    if failure == "core_result_missing" and role == "native":
        context.meter.records.pop("core_result", None)
    if failure == "core_result" and role == "native":
        context.meter.records["core_result"]["cleanup_proven"] = False
    if failure == "core_steps" and role == "native":
        context.meter.used["base_loads"] = 0
    if failure == "source" and role == "native":
        context.meter.records["core_result"]["candidate_head"] = "wrong"
    if failure == "export_bytes" and role == "native":
        context.meter.records["core_export"]["bytes"] = 999
    if failure == "export_digest" and role == "native":
        context.meter.records["core_export"]["sha256"] = "wrong"
    if failure == "export_runtime" and role == "native":
        context.meter.records["core_export"]["runtime_config"] = "wrong"
    if failure == "archive_identity" and role == "core":
        context.plan["archive"]["sha256"] = "wrong"
    if failure == "sdk":
        command = ["docker", "run", "sha256:" + "9" * 64, "other"]
    else:
        command = ["docker", "run", "--name", "synthetic", SDK_IMAGE, "other"]
    callback = OfflineSandbox(context, lambda *_args, **_kwargs: None,
                              lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""))
    try:
        result = callback.outer_command(command)
    except RuntimeError as error:
        if failure is None:
            raise
        return {"error": str(error), "events": context.meter.events, "context": context}
    if failure is not None:
        raise AssertionError("synthetic outer failure was not observed: " + failure)
    assert "--pull=never" in result and "--network" in result and "none" in result
    assert context.meter.events[-1][:2] == ("charge", "sdk_" + role)
    return {"command": result, "events": context.meter.events, "context": context}


def prepare_case(monkeypatch: Any, tmp_path: Path, role: str = "core",
                 failure: str | None = None) -> dict[str, Any]:
    """Run the full fake prepare flow, including overlay2 and image identity."""
    import runner_pgvector_sandbox as module
    context: Any = offline_context(tmp_path, role)
    callback = module.OfflineSandbox(context, lambda *_a, **_k: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    operations: list[list[str]] = []
    loaded = False
    expected = {RUNTIME_IMAGE} | ({BASE_IMAGE} if role == "core" else set())
    observed = {"Id": RUNTIME_IMAGE, "Os": "linux", "Architecture": "arm64",
                "RootFS": {"Layers": context.plan["derived"]["runtime_config_document"]["rootfs"]["diff_ids"]},
                "Config": {"Cmd": ["postgres"], "Entrypoint": ["postgres"],
                            "Labels": context.plan["derived"]["runtime_config_document"]["config"]["Labels"]}}

    def captured(_runner: Any, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        operations.append(command)
        tail = command[3:]
        if failure == "architecture" and tail[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(command, 0, "amd64\n", "")
        if failure == "graphdriver" and tail[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(command, 0, "btrfs\n", "")
        if tail[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(command, 0, "arm64\n", "")
        if tail[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(command, 0, "overlay2\n", "")
        if tail[:4] == ["docker", "image", "ls", "--all"]:
            ids = "sha256:unexpected\n" if failure == "inventory" or (failure == "imported_ids" and loaded) else ("\n" if not loaded else "\n".join(sorted(expected)) + "\n")
            return subprocess.CompletedProcess(command, 0, ids, "")
        if tail[:3] == ["docker", "image", "load"]:
            context.meter.note("image_load")
            if failure == "load":
                return subprocess.CompletedProcess(command, 1, "", "load failed")
            loaded = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if tail[:3] == ["docker", "image", "inspect"]:
            value = dict(observed)
            if failure == "identity":
                value["Architecture"] = "amd64"
            if tail[-1] == BASE_IMAGE:
                value["Id"] = BASE_IMAGE
                value["RootFS"] = {"Layers": context.plan["derived"]["base_config_document"]["rootfs"]["diff_ids"]}
                value["Config"] = {"Cmd": ["postgres"], "Entrypoint": ["postgres"], "Labels": {}}
            return subprocess.CompletedProcess(command, 0, json.dumps([value]), "")
        if failure == "command":
            return subprocess.CompletedProcess(command, 1, "", "command failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    def send_file(*_args: Any, **_kwargs: Any) -> None:
        operations.append(["send_file"])
        context.meter.note("send_file")
        if failure == "send_file":
            raise RuntimeError("synthetic send failure")

    def send_plan(*_args: Any, **_kwargs: Any) -> None:
        operations.append(["send_plan"])
        context.meter.note("send_plan")
        if failure == "send_plan":
            raise RuntimeError("synthetic plan failure")

    def export(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        operations.append(["export_runtime"])
        context.meter.note("export_runtime")
        if failure == "export":
            raise RuntimeError("synthetic export failure")
        return {"bytes": 11, "sha256": "d" * 64, "runtime_config": RUNTIME_IMAGE}

    monkeypatch.setattr(module, "send_file", send_file)
    monkeypatch.setattr(module, "send_plan", send_plan)
    monkeypatch.setattr(module, "export_runtime", export)
    callback.captured = captured
    try:
        callback.prepare(lambda *_a, **_k: None, "outer")
    except RuntimeError as error:
        if failure is None:
            raise
        return {"error": str(error), "operations": operations, "context": context}
    if failure is not None:
        raise AssertionError("synthetic prepare failure was not observed: " + failure)
    charged = "base_loads" if role == "core" else "native_runtime_loads"
    send_index = context.meter.events.index(("operation", "send_file", 0))
    assert context.meter.events.index(("charge", charged, 1)) < send_index
    if role == "native":
        assert context.meter.events.index(("charge", "native_lifecycles", 1)) < send_index
    assert context.meter.events.index(("charge", "read_bytes", 11)) < context.meter.events.index(("operation", "image_load", 0))
    assert any(item == ["send_file"] for item in operations)
    if role == "core":
        assert any(item == ["export_runtime"] for item in operations)
    return {"operations": operations, "context": context}


def run_tests_case(tmp_path: Path, statuses: list[tuple[str, str]], *, expired: bool = False) -> int:
    """Exercise bounded polling with a true/false sequence or expiry."""
    from runner_pgvector_sandbox import OfflineSandbox
    context: Any = offline_context(tmp_path)
    callback = OfflineSandbox(context, lambda *_a, **_k: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    if expired:
        callback.deadline = time.monotonic() - 1
    values = iter(statuses)

    def captured(_runner: Any, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "container", "inspect"]:
            running, code = next(values)
            return subprocess.CompletedProcess(command, 0, running + " " + code, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    callback.captured = captured
    return callback.run_tests(lambda *_a, **_k: None, "outer")


def finished_case(monkeypatch: Any, tmp_path: Path, result: int = 0, *, source_error: bool = False,
                  repeat: bool = False) -> FakeContext:
    """Exercise post-cleanup write-once result binding and source verification."""
    import runner_pgvector_context as contexts
    from runner_pgvector_sandbox import OfflineSandbox
    context: Any = offline_context(tmp_path)
    if source_error:
        monkeypatch.setattr(contexts, "verify_checkout", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("source drift")))
    else:
        monkeypatch.setattr(contexts, "verify_checkout", lambda *_a, **_k: None)
    OfflineSandbox(context, lambda *_a, **_k: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", "")).finished(result)
    assert context.meter.records["core_result"]["cleanup_proven"] is (result == 0)
    if repeat:
        try:
            OfflineSandbox(context, lambda *_a, **_k: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", "")).finished(result)
        except RuntimeError as error:
            assert "already bound" in str(error)
        else:
            raise AssertionError("finished must refuse a duplicate result binding")
    return context


__all__ = ["FakeContext", "FakeMeter", "finished_case", "offline_context",
           "offline_plan", "outer_command_case", "prepare_case", "run_tests_case"]
