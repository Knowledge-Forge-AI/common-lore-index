"""Real harmless pipe peers for export command and parser failure ownership."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest

import runner_pgvector_export as exports
from runner_pgvector_prebuilt import digest, json_bytes
from runner_pgvector_budget import CAPS
from repomap_test_support.pgvector_prebuilt_budget_cases import allocation
from repomap_test_support.pgvector_prebuilt_export_cases import legacy_export_members, oci_export_members
from repomap_test_support.pgvector_prebuilt_test_fixtures import tar_bytes


def parser_layout_case(tmp_path: Path, layout: str, mutation: str) -> None:
    config = json_bytes({"os": "linux", "architecture": "arm64",
                         "created": "1970-01-01T00:00:00Z", "config": {"Cmd": ["postgres"]}})
    layers = [b"public first layer", b"public second layer"]
    expected = {"runtime_config": "sha256:" + digest(config),
                "runtime_layers": [{"bytes": len(raw), "sha256": digest(raw)} for raw in layers]}
    builder = oci_export_members if layout == "oci" else legacy_export_members
    rows = builder(config, layers, mutation=mutation)
    raw = tar_bytes(rows)
    if mutation == "buffered-trailer":
        # Put nonzero content inside the padding tarfile may have read ahead.
        raw = raw[:-512] + b"hidden" + raw[-506:]
    source = tmp_path / "public.tar"
    source.write_bytes(raw)
    with source.open("rb") as incoming, (tmp_path / "retained.tar").open("wb") as target:
        with allocation(tmp_path).session() as meter:
            stream = exports.RetainedStream(incoming, target, meter, time.monotonic() + 5)
            if mutation in {"none", "omit-platform", "moby-defaults"}:
                exports.inspect_export(stream, expected)
                assert stream.count == len(raw) and stream.hasher.hexdigest() == digest(raw)
            else:
                with pytest.raises((RuntimeError, ValueError, exports.tarfile.TarError)):
                    exports.inspect_export(stream, expected)


def export_command_case(tmp_path: Path, monkeypatch: Any, mutation: str) -> None:
    config = json_bytes({"os": "linux", "architecture": "arm64", "config": {}})
    layer = b"public opaque layer fixture"
    expected = {"runtime_config": "sha256:" + digest(config),
                "runtime_layers": [{"bytes": len(layer), "sha256": digest(layer)}]}
    members = oci_export_members(config, [layer], mutation="layer-size" if mutation == "layout" else None)
    raw = tar_bytes(members)
    if mutation == "short":
        raw = raw[:800]
    native_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []
    budget = allocation(tmp_path)
    def spawn(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        assert command == ["docker", "exec", "outer", "docker", "image", "save", expected["runtime_config"]]
        assert json.loads(budget.path.read_bytes())["used"]["runtime_exports"] == CAPS["runtime_exports"]
        if mutation == "timeout":
            code = "import time; time.sleep(60)"
        else:
            code = "import sys; "
            if mutation == "stderr":
                code += "sys.stderr.buffer.write(b'x'*70000); sys.stderr.flush(); "
            code += "sys.stdout.buffer.write(bytes.fromhex(sys.argv[1])); sys.stdout.flush(); "
            code += "raise SystemExit(42)" if mutation == "exit" else "raise SystemExit(0)"
        child = native_popen([sys.executable, "-B", "-c", code, raw.hex()], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(exports.subprocess, "Popen", spawn)
    if mutation == "timeout":
        moments = iter((0.0, 301.0))
        monkeypatch.setattr(exports.time, "monotonic", lambda: next(moments, 301.0))
    output = tmp_path / "export.tar"
    with budget.session() as meter:
        meter.charge("runtime_exports", CAPS["runtime_exports"] - 1)
        if mutation == "none":
            receipt = exports.export_runtime("outer", output, expected, meter)
            assert receipt["sha256"] == digest(raw) and receipt["bytes"] == len(raw)
            assert receipt["validated_ordered_layers"] is True and output.read_bytes() == raw
        else:
            with pytest.raises((RuntimeError, exports.tarfile.TarError)):
                exports.export_runtime("outer", output, expected, meter)
        assert meter.used["runtime_exports"] == CAPS["runtime_exports"]
        with pytest.raises(RuntimeError, match="exhausted"):
            exports.export_runtime("outer", tmp_path / "forbidden-retry.tar", expected, meter)
    assert len(children) == 1 and children[0].poll() is not None
