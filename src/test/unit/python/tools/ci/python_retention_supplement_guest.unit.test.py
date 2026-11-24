"""Static witness for the closed W5 guest import retention recipe."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ci.python_retention_dependencies import build_dependency_graph
from ci.python_retention_dynamic import apply_dynamic_boundaries
from ci.python_retention_dynamic_history import SCHEMA
from ci.python_retention_dynamic_recipes import fingerprint

if TYPE_CHECKING:
    import retrieval_supplement_worker_entry as owner
    assert callable(owner._run_worker)


OWNER = "tools/retrieval_supplement_worker_entry.py"
TEST = "src/test/unit/python/tools/retrieval_supplement_worker_entry.unit.test.py"
ARTIFACTS = "tools/retrieval_semantic_generator_artifacts.py"
KERNEL = "tools/retrieval_semantic_generator_kernel_contracts.py"
MODULES = ("numpy", "onnxruntime", "tokenizers")
PATHS = [OWNER, TEST, ARTIFACTS, KERNEL]


def _record(source_root: Path, operation: tuple[str, int, str]) -> dict:
    module, line, operation_fingerprint = operation
    digest = lambda path: hashlib.sha256((source_root / path).read_bytes()).hexdigest()
    parameters = {
        "schema": "supplement-native-guest-import-v1",
        "modules": list(MODULES),
        "import_module": "importlib.import_module",
        "loop": "literal-tuple-once-v1",
        "artifact_source_identity_sha256": "033cf41ffc5ca108b6e26638573554bac64b84be744903809904ef3364722180",
        "artifact_pin_document_sha256": "f0237e38018f3524b87f85f7fff2bef41eda125b8f954d575aaee233541896b6",
        "artifacts_source_path": ARTIFACTS,
        "artifacts_source_sha256": digest(ARTIFACTS),
        "kernel_source_path": KERNEL,
        "kernel_source_sha256": digest(KERNEL),
        "versions": {"numpy": "2.5.4", "onnxruntime": "1.31.1", "tokenizers": "0.23.3"},
        "owner": OWNER,
    }
    return {
        "id": "cloe.w5-supplement-study1.guest." + module,
        "owner": OWNER,
        "owner_sha256": digest(OWNER),
        "test": TEST,
        "test_sha256": digest(TEST),
        "kind": "import_module",
        "symbol": "_run_worker",
        "fingerprint": operation_fingerprint,
        "recipe": "supplement_native_guest_import",
        "targets": [],
        "parameters": parameters,
        "supersedes": None,
    }


def _fixture(tmp_path: Path) -> tuple[list[str], list[dict]]:
    source_root = Path(__file__).resolve().parents[6]
    for relative in PATHS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative).read_bytes())
    import ast
    tree = ast.parse((tmp_path / OWNER).read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_run_worker")
    calls = sorted((node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "import_module"), key=lambda node: node.lineno)
    records = [_record(tmp_path, (module, node.lineno, fingerprint(node)))
               for module, node in zip(MODULES, calls)]
    return PATHS, records


def _write_inventory(root: Path, records: list[dict]) -> None:
    path = root / "tools/ci/python_retention_inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"dynamic_schema": SCHEMA, "dynamic_boundaries": records}))


def test_three_guest_imports_close_without_project_edges(tmp_path: Path) -> None:
    paths, records = _fixture(tmp_path)
    graph = build_dependency_graph(tmp_path, paths)
    before = {OWNER: list(graph["dependencies"].get(OWNER, ())) }
    _write_inventory(tmp_path, records)
    graph = apply_dynamic_boundaries(tmp_path, paths, graph)
    assert graph["resolved_dynamic_dependencies"] == {record["id"]: [] for record in records}
    assert OWNER not in graph["dynamic_uncertainty"]
    assert graph["dependencies"].get(OWNER, []) == before[OWNER]


@pytest.mark.parametrize("fault", ["wrong-module", "alias", "extra-site", "stale-source",
                                    "incomplete", "target", "symbol"])
def test_guest_recipe_refuses_drift_or_partial_declarations(tmp_path: Path, fault: str) -> None:
    paths, records = _fixture(tmp_path)
    if fault == "wrong-module":
        records[1]["parameters"]["modules"][1] = "other"
    elif fault == "incomplete":
        records = records[:2]
    elif fault == "target":
        records[0]["targets"] = [{"module": "foreign", "symbol": "", "path": OWNER,
                                  "sha256": records[0]["owner_sha256"]}]
    elif fault == "symbol":
        records[0]["symbol"] = "main"
    elif fault == "stale-source":
        records[0]["parameters"]["artifacts_source_sha256"] = "0" * 64
    else:
        path = tmp_path / OWNER
        suffix = (b"\nimport_module = object()\n" if fault == "alias" else
                  b"\n\n# extra dynamic site\ndef _extra():\n    return import_module('scipy')\n")
        path.write_bytes(path.read_bytes() + suffix)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for record in records:
            record["owner_sha256"] = digest
    _write_inventory(tmp_path, records)
    graph = apply_dynamic_boundaries(tmp_path, paths,
                                     build_dependency_graph(tmp_path, paths))
    assert OWNER in graph["dynamic_uncertainty"]
    assert not set(record["id"] for record in records) & set(
        graph.get("resolved_dynamic_dependencies", {}))
