"""Reviewed host entrypoint for the one disposable PGDG materialization and plans.

This helper never starts Docker, tests or models. The standard runner owns both
separately allocated SDK lifetimes; no action here retries a failed allocation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from runner_pgvector_archive import derive_archive
from runner_pgvector_budget import Allocation, CAPS, private_file, refuse
from runner_pgvector_context import validate_plan, verify_checkout
from runner_pgvector_lineage import require_attempt
from runner_pgvector_declaration import (
    copy_bundle, load_declaration, plan_common, read_forecast, write_once,
)
from runner_pgvector_prebuilt import digest, json_bytes, read_regular, strict_object


def materialize(*, source: Path, declaration: Path, session: Path, base: Path,
                index: Path, package: Path) -> dict[str, Any]:
    """Exactly one new directory and write-once intent; failure retains all state."""
    document = load_declaration(declaration, source)
    selection = read_regular(Path(document["authority"]["corrective_selection_sha256"]["path"]), 1024**2)
    if selection.lstrip().startswith(b"{"):
        refuse(strict_object(selection).get("schema") != "cloe-pgdg-core-recovery-authority-v1",
               "recovery declaration cannot rematerialize")
    refuse(session.is_absolute() and not session.exists()
           and not session.is_relative_to(source) and not source.is_relative_to(session)
           and not any(p.is_symlink() for p in session.parents), "fresh session required")
    session.mkdir(mode=0o700)
    write_once(session / "declaration.json", json_bytes(document))
    write_once(session / "materialization-INTENT.json", json_bytes({
        "phase": "CLOE-W5-PGDG1", "declaration_sha256": digest(json_bytes(document)),
        "materialization_starts_reserved": 1, "failure_reopens_slot": False}))
    allocation = session / "allocation.json"
    write_once(allocation, json_bytes({"schema": "cloe-pgvector-allocation-v1",
        "caps": CAPS, "used": {key: 0 for key in CAPS}, "records": {}}))
    with Allocation(allocation).session() as meter:
        forecast = read_forecast(base, meter)
        write_once(session / "read-forecast.json", json_bytes(forecast))
        copy_bundle(index, package, session / "payload", meter)
        derived = derive_archive(base, session / "payload", session / "base-runtime.tar", meter)
        plan = {**plan_common(document), "role": "core", "core_attempt": 1, "derived": derived,
            "allocation_path": str(allocation), "export_path": str(session / "runtime-export.tar"),
            "archive": {"path": str(session / "base-runtime.tar"),
                        "bytes": derived["bytes"], "sha256": derived["sha256"]}}
        validate_plan(plan)
        verify_checkout(source, plan["candidate_snapshot"], plan["candidate_head"])
        write_once(session / "core.plan.json", json_bytes(plan))
    return {"core_plan": str(session / "core.plan.json"), "materializations": 1,
            "runtime_config": derived["runtime_config"], "forecast": forecast}


def native_plan(*, source: Path, declaration: Path, session: Path) -> dict[str, Any]:
    """Core cleanup and the validated export must already be recorded durably."""
    document = load_declaration(declaration, source)
    core_path = session / "core.plan.json"
    private_file(core_path)
    core = strict_object(read_regular(core_path, 2 * 1024**2))
    validate_plan(core)
    common = plan_common(document)
    refuse(all(core.get(key) == value for key, value in common.items()) and core["role"] == "core",
           "core/declaration correspondence")
    allocation = session / "allocation.json"
    refuse(core.get("allocation_path") == str(allocation)
           and core.get("export_path") == str(session / "runtime-export.tar")
           and core.get("archive", {}).get("path") == str(session / "base-runtime.tar"),
           "core session resource paths")
    with Allocation(allocation).session() as meter:
        attempt = require_attempt(meter, core)
        refuse(meter.used["materializations"] == 1
               and meter.used["sdk_core"] == meter.used["base_loads"] == meter.used["runtime_exports"] == attempt
               and meter.used["sdk_native"] == meter.used["native_runtime_loads"] ==
               meter.used["native_lifecycles"] == 0, "core/native allocation prerequisites")
        required = {"exit": 0, "cleanup_proven": True, "candidate_head": core["candidate_head"],
                    "candidate_snapshot_sha256": digest(json_bytes(core["candidate_snapshot"]))}
        refuse(meter.records.get("core_result") == required
               and meter.records.get("derived_archive") == core["derived"], "core proof prerequisite")
        exported = meter.records.get("core_export", {})
        refuse(exported.get("validated_ordered_layers") is True
               and exported.get("runtime_config") == core["derived"]["runtime_config"],
               "validated export prerequisite")
        export_path = session / "runtime-export.tar"
        private_file(export_path)
        refuse(export_path.stat().st_size == exported.get("bytes"), "runtime export size")
        plan = {**common, "role": "native", "derived": core["derived"],
            "allocation_path": str(allocation), "archive": {"path": str(export_path),
                "bytes": exported["bytes"], "sha256": exported["sha256"]}}
        for key in ("core_attempt", "recovery_parent_sha256"):
            if key in core:
                plan[key] = core[key]
        validate_plan(plan)
        verify_checkout(source, plan["candidate_snapshot"], plan["candidate_head"])
        write_once(session / "native.plan.json", json_bytes(plan))
    return {"native_plan": str(session / "native.plan.json"), "native_starts": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("materialize", "native-plan", "recover-core"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--declaration", required=True, type=Path)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "recover-core":
            from runner_pgvector_recovery import recover_core
            refuse(args.previous is not None
                   and all(value is None for value in (args.base, args.index, args.package)),
                   "recovery cannot select materialization inputs")
            result = recover_core(source=args.source, declaration=args.declaration,
                                  previous=args.previous, session=args.session)
        elif args.action == "materialize":
            refuse(args.previous is None, "materialization cannot select predecessor")
            refuse(all(value is not None for value in (args.base, args.index, args.package)),
                   "materialization inputs required")
            result = materialize(source=args.source, declaration=args.declaration, session=args.session,
                                 base=args.base, index=args.index, package=args.package)
        else:
            refuse(args.previous is None, "native-plan cannot select predecessor")
            refuse(all(value is None for value in (args.base, args.index, args.package)),
                   "native-plan cannot select new inputs")
            result = native_plan(source=args.source, declaration=args.declaration, session=args.session)
        print(json_bytes(result).decode(), end="")
        return 0
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        prefix = "pgvector prebuilt refused: "
        code = str(error)[len(prefix):len(prefix) + 160] if str(error).startswith(prefix) else "preparation_failed"
        print(json_bytes({"event": "cloe-pgdg-preparation-refusal-v1", "action": args.action,
                          "error_type": type(error).__name__, "code": code}).decode(),
              file=sys.stderr, end="")
        print("pgvector offline qualification preparation refused; retain owned state")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
