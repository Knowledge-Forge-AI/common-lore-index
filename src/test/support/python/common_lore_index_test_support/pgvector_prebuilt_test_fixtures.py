"""Small synthetic metadata/pipe fixtures; none is a qualified runtime."""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any
from types import SimpleNamespace

import runner_pgvector_archive as construction
import runner_pgvector_transport as transport
from runner_pgvector_budget import Allocation, CAPS
from runner_pgvector_prebuilt import digest, json_bytes


def allocation(tmp_path: Path) -> Allocation:
    path = tmp_path / "allocation.json"
    path.write_bytes(json_bytes({"schema": "cloe-pgvector-allocation-v1", "caps": CAPS,
                                "used": dict.fromkeys(CAPS, 0)}))
    path.chmod(0o600)
    return Allocation(path)


def tar_bytes(rows: list[tuple[str, bytes, str]]) -> bytes:
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, body, kind in rows:
            item = tarfile.TarInfo(name)
            if kind == "link":
                item.type, item.linkname = tarfile.SYMTYPE, "outside"
                archive.addfile(item)
            else:
                item.size = len(body)
                archive.addfile(item, io.BytesIO(body))
    return result.getvalue()


def synthetic_construction(tmp_path, monkeypatch, mutation):
    """Real gzip/hash/USTAR; replace only supplier descriptors and selected data."""
    layer = tar_bytes([("usr/synthetic", b"base-data", "file")])
    compressed = gzip.compress(layer, mtime=0)
    layer_sha = "sha256:" + digest(layer)
    row = {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
           "size": len(compressed), "digest": "sha256:" + digest(compressed), "diff_id": layer_sha}
    if mutation == "digest":
        row["digest"] = "sha256:" + "0" * 64
    base: dict[str, Any] = {"os": "linux", "architecture": "arm64",
        "config": {"Cmd": ["postgres"], "Entrypoint": ["synthetic-entry"], "Labels": {}},
        "rootfs": {"type": "layers", "diff_ids": [layer_sha]}, "history": [{}]}
    config_raw = json_bytes(base)
    config_id = "sha256:" + digest(config_raw)
    manifest = {"config": {"digest": config_id},
                "layers": [{k: row[k] for k in ("mediaType", "size", "digest")}]}
    manifest_raw = json_bytes(manifest)
    for name, raw in [("config.json", config_raw), ("manifest.json", manifest_raw),
                      ("layer-00.tar.gz", compressed)]:
        (tmp_path / name).write_bytes(raw)
    monkeypatch.setattr(construction, "BASE_CONFIG", config_id)
    monkeypatch.setattr(construction, "BASE_MANIFEST", "sha256:" + digest(manifest_raw))
    monkeypatch.setattr(construction, "provider_facts", lambda: {"base_layers": [row]})
    monkeypatch.setattr(construction, "selected_payloads", lambda root, meter: {
        "usr/synthetic-extension": b"selected-data"})
    budget = allocation(tmp_path)
    output = tmp_path / "constructed.tar"
    with budget.session() as meter:
        if mutation == "budget":
            meter.charge("read_bytes", CAPS["read_bytes"] - 8)
        try:
            result = construction.derive_archive(tmp_path, tmp_path, output, meter)
        except RuntimeError:
            assert mutation != "none"
            assert meter.used["materializations"] == 1
            return
        assert mutation == "none"
        assert result["native_abi_or_loading_proven"] is False
        with tarfile.open(output, mode="r:") as archive:
            def member_bytes(name: str) -> bytes:
                member = archive.extractfile(name)
                assert member is not None
                return member.read()
            rows = json.loads(member_bytes("manifest.json"))
            assert len(rows) == 2 and rows[1]["Layers"][:-1] == rows[0]["Layers"]
            first = member_bytes(rows[0]["Layers"][0])
            assert first == layer
            runtime = json.loads(member_bytes(rows[1]["Config"]))
            assert runtime["config"]["Entrypoint"] == base["config"]["Entrypoint"]
            assert runtime["rootfs"]["diff_ids"][:-1] == [layer_sha]
        assert meter.used["materializations"] == 1
        assert result["selected_file_count"] == 1


def sender_case(tmp_path, monkeypatch, mutation):
    """The sender uses real stdin pipes; Docker is replaced with harmless Python."""
    source = tmp_path / "binary-input"
    raw = b"\x00\xff\x80\n\r" * 10000
    source.write_bytes(raw)
    source.chmod(0o600)
    descriptor = {"bytes": len(raw), "sha256": digest(raw)}
    if mutation == "source_digest":
        descriptor["sha256"] = "0" * 64
    native_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []
    def spawn(command, **kwargs):
        assert command[:3] == ["docker", "exec", "-i"]
        if mutation == "exit":
            code = "raise SystemExit(42)"
        else:
            code = ("import sys,json,hashlib; raw=sys.stdin.buffer.read(50001); "
                    "result={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}; ")
            code += ("print('x'*20000)" if mutation == "receipt_cap" else
                     "result['sha256']='0'*64; print(json.dumps(result))" if mutation == "receipt_digest" else
                     "print(json.dumps(result))")
        child = native_popen([sys.executable, "-c", code], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(transport.subprocess, "Popen", spawn)
    budget = allocation(tmp_path)
    with budget.session() as meter:
        try:
            transport.send_file("synthetic-container", source, "image.tar", descriptor, meter)
        except (OSError, RuntimeError):
            assert mutation != "none"
        else:
            assert mutation == "none"
        assert meter.used["read_bytes"] >= len(raw) + 1
    assert len(children) == 1 and all(child.poll() is not None for child in children)


def offline_probe_case(monkeypatch):
    """Preserve the real probe owner; replace only external commands."""
    import test_sandbox as facade
    from runner_pgvector_sandbox import OfflineSandbox
    operations: list[list[str] | tuple[str, ...]] = []
    child = "a" * 64
    def required(runner, outer, command, **kwargs):
        operations.append(command)
        if command[:3] == ("docker", "run", "-d"):
            assert "--pull=never" in command and "--entrypoint" in command
            assert "--user" in command and command[command.index("--user") + 1] == "postgres"
            assert not any("src=/workspace" in token for token in command)
            assert any("src=/sandbox-scratch/cloe-public-probe,dst=/sandbox-scratch/cloe-public-probe" in token
                       for token in command)
            return subprocess.CompletedProcess(command, 0, child, "")
        if command[:2] == ("docker", "wait"):
            return subprocess.CompletedProcess(command, 0, "0\n", "")
        if command[:2] == ("go", "version"):
            return subprocess.CompletedProcess(command, 0, "go1.25", "")
        if command[:2] == ("golangci-lint", "version"):
            return subprocess.CompletedProcess(command, 0, "2.6.2", "")
        if command[:2] == ("git", "config"):
            return subprocess.CompletedProcess(command, 0, "/workspace\n", "")
        if command[:2] == ("git", "-C") and "--show-toplevel" in command:
            return subprocess.CompletedProcess(command, 0, "/workspace\n", "")
        if command[0] == "cat":
            assert any(cmd[:2] == ("docker", "wait") for cmd in operations)
            return subprocess.CompletedProcess(command, 0, "ok\n", "")
        if command[:3] == ("docker", "volume", "create"):
            return subprocess.CompletedProcess(command, 0, command[-1], "")
        return subprocess.CompletedProcess(command, 0, "", "")
    def captured(runner, command, **kwargs):
        return subprocess.CompletedProcess(command, 1 if "inspect" in command else 0, "", "")
    monkeypatch.setattr(facade, "_require_inner_command", required)
    monkeypatch.setattr(facade, "_captured", captured)
    context: Any = SimpleNamespace()
    callback = OfflineSandbox(context, lambda *args: None, captured)
    callback.probe_image = "sha256:" + "b" * 64
    callback.boundary(lambda *args: None, container_id="outer", token="synthetic")
    assert sum(cmd[:3] == ("docker", "run", "-d") for cmd in operations) == 1
