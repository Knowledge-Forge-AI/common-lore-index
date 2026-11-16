"""Conservative source binding for the analyzer's flat, standard-library tooling.

Parse every local import, including function-local imports, without executing
project code. Package imports and dynamic loading are outside this contract and
refuse rather than silently producing an incomplete source identity.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

from retrieval_supplement_analysis_values import fail, refuse
from retrieval_supplement_files import read_member
from retrieval_supplement_primitives import SupplementError, digest_bytes, digest_json


def source_closure_sha256(repo_root: Path, owners: tuple[str, ...]) -> str:
    """Hash all supported local dependencies and their names with finite limits."""
    pending = list(owners)
    files: dict[str, dict[str, str | int]] = {}
    total = 0
    while pending:
        member = pending.pop()
        if member in files:
            continue
        refuse(len(files) < 64, "analysis:source_closure_too_large")
        refuse(member.startswith("tools/") and member.count("/") == 1
               and member.endswith(".py"), "analysis:source_owner_form")
        try:
            raw = read_member(repo_root, member, max_bytes=512 * 1024)
            tree = ast.parse(raw)
        except (SupplementError, SyntaxError, ValueError):
            fail("analysis:analyzer_source_custody")
        total += len(raw)
        refuse(total <= 4 * 1024 * 1024, "analysis:source_closure_too_large")
        files[member] = {"bytes": len(raw), "sha256": digest_bytes(raw)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                refuse(name not in {"__import__", "import_module", "exec", "eval",
                                    "exec_module", "spec_from_file_location"},
                       "analysis:source_dynamic_import_unsupported")
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level != 0 or node.module is None:
                    fail("analysis:source_relative_import_unsupported")
                modules = [node.module]
            for module in modules:
                if module.partition(".")[0] in sys.stdlib_module_names:
                    continue
                refuse(module.isidentifier(), "analysis:source_package_import_unsupported")
                pending.append("tools/" + module + ".py")
    return digest_json({
        "schema": "cloe-supplement-analyzer-source-closure-v1",
        "members": sorted(files),
        "files": files,
    })
