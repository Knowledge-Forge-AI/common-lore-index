"""Closed retention recipe for the three W5 offline guest imports.

The model bootstrap is the only owner admitted by this recipe.  The imports
are guest operations after the existing hash-locked wheel installation; no
project dependency edge or generic dynamic-import exemption is created.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from ci.python_retention_dynamic_recipes import fingerprint

SCHEMA = "supplement-native-guest-import-v1"
OWNER = "tools/retrieval_supplement_worker_entry.py"
SYMBOL = "_run_worker"
MODULES = ("numpy", "onnxruntime", "tokenizers")
IMPORT_MODULE = "importlib.import_module"
ARTIFACT_SOURCE_IDENTITY = "033cf41ffc5ca108b6e26638573554bac64b84be744903809904ef3364722180"
ARTIFACT_PIN_DOCUMENT = "f0237e38018f3524b87f85f7fff2bef41eda125b8f954d575aaee233541896b6"
ARTIFACTS_SOURCE_PATH = "tools/retrieval_semantic_generator_artifacts.py"
KERNEL_SOURCE_PATH = "tools/retrieval_semantic_generator_kernel_contracts.py"
ARTIFACTS_SOURCE_SHA256 = "50d8bfb7113d102c4aa44c030a590040e39c5484c6a5b185ba6dbefcf180a27f"
KERNEL_SOURCE_SHA256 = "5e48544da3c729f22a701c4b522f65c1acbc1859a7ac646eb29239ccacad05d8"
VERSIONS = {"numpy": "2.5.4", "onnxruntime": "1.31.1", "tokenizers": "0.23.3"}
TEST = "src/test/unit/python/tools/retrieval_supplement_worker_entry.unit.test.py"


def _refuse(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _source(root: Path, path: str, expected: str) -> bytes:
    candidate = root / path
    _refuse(not candidate.is_symlink() and candidate.is_file(), "guest source absent or aliased")
    _refuse(candidate.resolve() == root.resolve() / path, "guest source escaped candidate")
    source = candidate.read_bytes()
    _refuse(hashlib.sha256(source).hexdigest() == expected, "guest source identity drift")
    return source


def _function(tree: ast.Module) -> ast.FunctionDef:
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == SYMBOL]
    _refuse(len(functions) == 1, "guest owner function is not unique")
    return functions[0]


def _validate_import_binding(tree: ast.Module) -> None:
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "importlib" and node.level == 0]
    _refuse(len(imports) == 1 and len(imports[0].names) == 1
            and imports[0].names[0].name == "import_module"
            and imports[0].names[0].asname is None,
            "guest import selector binding is not closed")
    _refuse(not any(isinstance(node, ast.Import) and any(alias.name == "importlib"
                       for alias in node.names) for node in tree.body),
            "guest owner must not add an alternate importlib binding")
    sites = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "import_module"]
    _refuse(len(sites) == len(MODULES), "guest owner must contain only the three import sites")
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg == "import_module":
            raise ValueError("guest import selector is shadowed")
        if isinstance(node, ast.Name) and node.id == "import_module" and isinstance(node.ctx, (ast.Store, ast.Del)):
            raise ValueError("guest import selector is rebound")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"}:
            raise ValueError("guest import selector mutator is forbidden")


def _calls(owner: ast.FunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(owner):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "import_module":
            continue
        _refuse(len(node.args) == 1 and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str), "guest import call shape")
        calls.append(node)
    calls.sort(key=lambda node: node.lineno)
    _refuse(tuple(cast(ast.Constant, node.args[0]).value for node in calls) == MODULES,
            "guest import module order or count")
    return calls


def _parameters(parameters: Mapping[str, Any]) -> None:
    expected = {"schema", "modules", "import_module", "loop", "artifact_source_identity_sha256",
                "artifact_pin_document_sha256", "artifacts_source_path", "artifacts_source_sha256",
                "kernel_source_path", "kernel_source_sha256", "versions", "owner"}
    _refuse(set(parameters) == expected, "guest declaration parameters are not closed")
    _refuse(parameters["schema"] == SCHEMA and tuple(parameters["modules"]) == MODULES
            and parameters["import_module"] == IMPORT_MODULE
            and parameters["loop"] == "literal-tuple-once-v1"
            and parameters["artifact_source_identity_sha256"] == ARTIFACT_SOURCE_IDENTITY
            and parameters["artifact_pin_document_sha256"] == ARTIFACT_PIN_DOCUMENT
            and parameters["artifacts_source_path"] == ARTIFACTS_SOURCE_PATH
            and parameters["artifacts_source_sha256"] == ARTIFACTS_SOURCE_SHA256
            and parameters["kernel_source_path"] == KERNEL_SOURCE_PATH
            and parameters["kernel_source_sha256"] == KERNEL_SOURCE_SHA256
            and parameters["versions"] == VERSIONS and parameters["owner"] == OWNER,
            "guest declaration identity differs from locked artifact")


def supplement_native_guest_import(
    call: ast.Call, tree: ast.AST, parameters: Mapping[str, Any], repo_root: Path,
    module_paths: Mapping[str, str], *, record: Mapping[str, Any],
    active_records: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    """Close exactly the three maintained guest imports and no project edges."""
    _parameters(parameters)
    _refuse(record.get("owner") == OWNER and record.get("symbol") == SYMBOL
            and record.get("targets") == [] and record.get("kind") == "import_module",
            "guest record identity")
    _source(repo_root, OWNER, str(record.get("owner_sha256")))
    _source(repo_root, TEST, str(record.get("test_sha256")))
    _source(repo_root, ARTIFACTS_SOURCE_PATH, ARTIFACTS_SOURCE_SHA256)
    _source(repo_root, KERNEL_SOURCE_PATH, KERNEL_SOURCE_SHA256)
    module_tree = ast.parse((repo_root / OWNER).read_bytes())
    _validate_import_binding(module_tree)
    owner = _function(module_tree)
    calls = _calls(owner)
    expected = {fingerprint(item): module for item, module in zip(calls, MODULES)}
    _refuse(fingerprint(call) in expected and record.get("fingerprint") in expected
            and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
            and expected[str(record["fingerprint"])] == call.args[0].value,
            "guest record fingerprint or module differs")
    peers = [item for item in active_records
             if item.get("recipe") == "supplement_native_guest_import"
             and item.get("owner") == OWNER and item.get("symbol") == SYMBOL]
    _refuse(len(peers) == len(MODULES) and len({item.get("fingerprint") for item in peers}) == len(MODULES),
            "guest import declaration must contain all three peers")
    for peer in peers:
        _refuse(peer.get("targets") == [] and peer.get("parameters") == dict(parameters)
                and peer.get("kind") == "import_module", "guest peer declaration differs")
        _refuse(peer.get("fingerprint") in expected, "guest peer fingerprint is not an owner call")
    _refuse({expected[str(item["fingerprint"])] for item in peers} == set(MODULES),
            "guest import peer set is incomplete")
    return set()


__all__ = ["SCHEMA", "supplement_native_guest_import"]
