"""Derive support-package dynamic proof successors from their scoped source bytes."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from ci.python_retention_operations import dynamic_operations

LEGACY_MODULE = "repomap_test_support"
CANONICAL_MODULE = "common_lore_index_test_support"
NATIVE_GUEST_TEST = "src/test/unit/python/tools/ci/python_retention_native_guest_import.unit.test.py"
GUARDED_DOMAIN = "src/test/support/python/repomap_test_support/test_cov5k_r2_fix2_dynamic_targets.py"
NATIVE_GUEST_READER = (
    (
        b"    records = [copy.deepcopy(item) for item in active_boundaries(inventory)\n",
        b"    from ci.python_retention_support_projection import verify_support_namespace\n\n"
        b"    support = verify_support_namespace(source_root)\n"
        b"    assert support is not None\n"
        b"    records = [copy.deepcopy(item) for item in active_boundaries(inventory, support=support)\n",
    ),
    (
        b"    assert len(records) == 2\n",
        b"    assert len(records) == 2\n"
        b"    for record in records:\n"
        b"        # The isolated fixture starts a separate synthetic authority history.\n"
        b"        record[\"id\"] = record[\"id\"].removesuffix(\".cloe-support\")\n"
        b"        record[\"supersedes\"] = None\n",
    ),
)
SUPPORTED_RECIPES = frozenset({
    "file_loader_spec", "literal_module_entry", "guarded_symbol_import",
    "generated_false_code_identity", "literal_coverage_fixture", "optional_import_substitution",
    "external_native_guest_import",
})
NAMESPACE_TOKEN = re.compile(rb"(?<!\w)repomap_test_support(?!\w)")
NATIVE_GUEST_IDS = frozenset({
    "adr0155.rust_pilot.native_guest_import.parse_helper",
    "adr0155.rust_pilot.native_guest_import.runtime_probe",
})
STABLE_TEST_ROOTS = (b"src/test/unit/python/", b"src/test/int/python/")
PGVECTOR_SELECTOR_MODULE = b"repomap_test_support.pgvector_prebuilt_runtime.int.test"
RECIPE_KEYS = {
    "file_loader_spec": [{"case", "chain", "runtime_names", "schema", "target_path", "target_sha256"}],
    "literal_module_entry": [{"module", "run_name"}],
    "guarded_symbol_import": [{"domain_module", "domain_path", "domain_sha256"}],
    "generated_false_code_identity": [{"chain", "generated_source_sha256"}],
    "literal_coverage_fixture": [
        {"case", "chain", "filename", "schema", "source_utf8", "symbol"},
        {"branch", "case", "chain", "executed_branches", "executed_lines", "filename", "missing_branches",
         "missing_lines", "namespace", "schema", "source_utf8", "suffix", "symbol", "temporary_root"},
    ],
    "optional_import_substitution": [
        {"alternate_names", "blocked_domain", "case", "chain", "schema", "target_module", "target_path", "target_sha256"},
    ],
    "external_native_guest_import": [
        {"component_lock_id", "component_lock_path", "component_lock_sha256", "component_lock_source_sha256",
         "guest_owners", "import_module", "loop", "modules", "schema"},
    ],
}


def _project_name(value: str) -> str:
    if value == LEGACY_MODULE or value.startswith(LEGACY_MODULE + "."):
        return CANONICAL_MODULE + value[len(LEGACY_MODULE):]
    return value


def project_source(raw: bytes) -> bytes:
    original, canonical = LEGACY_MODULE.encode(), CANONICAL_MODULE.encode()
    projected = NAMESPACE_TOKEN.sub(canonical, raw)
    for selector_root in STABLE_TEST_ROOTS:
        projected = projected.replace(selector_root + canonical + b"/", selector_root + original + b"/")
    return projected


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sort_guarded_domain(raw: bytes) -> bytes:
    declarations = [
        node.value for node in ast.parse(raw).body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "PYTHON_TARGETS"
    ]
    if len(declarations) != 1 or not isinstance(declarations[0], ast.Tuple):
        raise ValueError("test-support guarded domain must have one literal tuple")
    declared = declarations[0]
    assert declared.end_lineno is not None
    names = ast.literal_eval(declared)
    if (not names or any(not isinstance(pair, tuple) or len(pair) != 2
                        or any(not isinstance(value, str) for value in pair) for pair in names)
            or len(set(names)) != len(names)):
        raise ValueError("test-support guarded domain entries are malformed")
    lines = raw.splitlines(keepends=True)
    start, stop = declared.lineno, declared.end_lineno - 1
    rows = lines[start:stop]
    if len(rows) != len(names) or any(
        node.lineno != start + index or node.end_lineno != node.lineno
        for index, node in enumerate(declared.elts, start=1)
    ):
        raise ValueError("test-support guarded domain must use one literal per line")
    return b"".join([*lines[:start], *(row for _, row in sorted(zip(names, rows, strict=True))), *lines[stop:]])


def project_witness(path: str, raw: bytes) -> bytes:
    """Bind the one live witness that must read the projected authority."""
    raw = project_source(raw)
    if path == GUARDED_DOMAIN:
        return _sort_guarded_domain(raw)
    if path != NATIVE_GUEST_TEST:
        return raw
    for before, after in NATIVE_GUEST_READER:
        if raw.count(before) != 1:
            raise ValueError("test-support native guest authority reader differs")
        raw = raw.replace(before, after)
    return raw


def _identifies_moved_source(row: Mapping[str, Any], path_map: Mapping[str, str]) -> bool:
    parameters = row["parameters"]
    return (
        row["owner"] in path_map
        or row["test"] in path_map
        or any(target["path"] in path_map for target in row["targets"])
        or any(parameters.get(key) in path_map for key in ("target_path", "domain_path"))
        or any(
            _project_name(name) != name
            for name in [
                *(parameters.get(key, "") for key in ("module", "domain_module", "target_module")),
                *parameters.get("runtime_names", ()),
            ]
        )
    )


def _operation_fingerprints(before: bytes, after: bytes, symbol: str) -> dict[tuple[str, str], str]:
    old_ops = [op for op in dynamic_operations(ast.parse(before)) if op["symbol"] == symbol]
    new_ops = [op for op in dynamic_operations(ast.parse(after)) if op["symbol"] == symbol]
    if len(old_ops) != len(new_ops):
        raise ValueError("test-support projection alters dynamic operation count")
    mappings = {}
    for op in old_ops:
        expression = project_source(op["expression"].encode()).decode()
        found = [
            row for row in new_ops if row["kind"] == op["kind"]
            and row["lineno"] == op["lineno"] and row["expression"] == expression
        ]
        key = (op["kind"], op["fingerprint"])
        if len(found) != 1 or key in mappings:
            raise ValueError("test-support projected dynamic operation is ambiguous")
        mappings[key] = found[0]["fingerprint"]
    return mappings


def _require_canonical_record(record: Mapping[str, Any]) -> None:
    raw = json.dumps(record, sort_keys=True).encode()
    for root in STABLE_TEST_ROOTS:
        raw = raw.replace(root + LEGACY_MODULE.encode() + b"/", b"")
    if record["recipe"] == "file_loader_spec" and record["parameters"]["case"] == "pgvector_native":
        raw = raw.replace(b'"' + PGVECTOR_SELECTOR_MODULE + b'"', b"")
    if NAMESPACE_TOKEN.search(raw):
        raise ValueError("test-support dynamic replacement retains a legacy authority name")


def derive_support_dynamic_records(
    records: Sequence[dict[str, Any]],
    paths: Mapping[str, str],
    read_before: Callable[[str], bytes],
    read_after: Callable[[str], bytes],
) -> tuple[dict[str, Any], ...]:
    def witness(path: str, digest: str) -> tuple[str, str]:
        before = read_before(path)
        successor = paths.get(path, path)
        after = read_after(successor)
        if _digest(before) != digest or after != project_witness(path, before):
            raise ValueError("test-support dynamic witness bytes exceed the exact projection")
        return successor, _digest(after)

    projected = []
    for previous in records:
        sources = {previous["owner"], previous["test"], *(target["path"] for target in previous["targets"])}
        parameters = previous["parameters"]
        sources.update(parameters[key] for key in ("target_path", "domain_path") if key in parameters)
        affected_bytes = any(project_witness(path, read_before(path)) != read_before(path) for path in sources)
        if not affected_bytes and not _identifies_moved_source(previous, paths):
            continue
        recipe = previous["recipe"]
        if recipe not in SUPPORTED_RECIPES:
            raise ValueError("test-support dynamic recipe has no approved projection")
        if set(parameters) not in RECIPE_KEYS[recipe]:
            raise ValueError("test-support dynamic recipe parameters differ from the closed projection")
        if NATIVE_GUEST_TEST in sources and (
            recipe != "external_native_guest_import" or previous["id"] not in NATIVE_GUEST_IDS
        ):
            raise ValueError("test-support native guest declaration belongs to another witness")
        successor = deepcopy(previous)
        successor["id"] = f'{previous["id"]}.cloe-support'
        successor["supersedes"] = previous["id"]
        for key in ("owner", "test"):
            successor[key], successor[key + "_sha256"] = witness(previous[key], previous[key + "_sha256"])
        fingerprints = _operation_fingerprints(
            read_before(previous["owner"]), read_after(successor["owner"]), previous["symbol"],
        )
        successor["fingerprint"] = fingerprints[previous["kind"], previous["fingerprint"]]
        for target in successor["targets"]:
            old_path = target["path"]
            target["path"], target["sha256"] = witness(old_path, target["sha256"])
            if old_path in paths:
                target["module"] = _project_name(target["module"])
        parameters = successor["parameters"]
        for path_key, digest_key in (("target_path", "target_sha256"), ("domain_path", "domain_sha256")):
            if path_key in parameters:
                parameters[path_key], parameters[digest_key] = witness(parameters[path_key], parameters[digest_key])
        for name_key in ("module", "domain_module", "target_module"):
            if name_key in parameters:
                parameters[name_key] = _project_name(parameters[name_key])
        if "runtime_names" in parameters:
            parameters["runtime_names"] = [_project_name(name) for name in parameters["runtime_names"]]
        for chain in parameters.get("chain", ()):
            chain["fingerprint"] = fingerprints[chain["kind"], chain["fingerprint"]]
        _require_canonical_record(successor)
        projected.append(successor)
    projected_guest = {row["supersedes"] for row in projected if row["test"] == NATIVE_GUEST_TEST}
    if projected_guest and projected_guest != NATIVE_GUEST_IDS:
        raise ValueError("test-support native guest pair is incomplete")
    return tuple(projected)
