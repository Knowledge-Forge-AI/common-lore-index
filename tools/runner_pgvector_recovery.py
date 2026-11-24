"""Stage the single approved core retry from an immutable counted predecessor."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any, cast

from runner_pgvector_budget import Allocation, CAPS, RECOVERY2_CAPS, MAX_ARCHIVE, private_file, refuse
from runner_pgvector_context import validate_plan, verify_checkout
from runner_pgvector_declaration import load_declaration, plan_common, write_once
from runner_pgvector_lineage import PARENT_SCHEMA, previous_used, require_attempt, validate_history
from runner_pgvector_prebuilt import digest, json_bytes, read_regular, strict_object

AUTHORITY_SCHEMA = "cloe-pgdg-core-recovery-authority-v1"
BLOCK = 1024 * 1024


def bound_file(path: Path, row: dict[str, Any]) -> bytes:
    private_file(path)
    refuse(row.get("path") == str(path) and type(row.get("bytes")) is int
           and 0 < row["bytes"] <= 2 * 1024**2, "previous metadata descriptor")
    raw = read_regular(path, row["bytes"])
    refuse(len(raw) == row["bytes"] and digest(raw) == row.get("sha256"),
           "previous metadata identity")
    return raw


def recovery_inputs(document: dict[str, Any], previous: Path,
                    session: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    row = document["authority"]["corrective_selection_sha256"]
    authority = strict_object(bound_file(Path(row["path"]), row))
    refuse(authority.get("schema") == AUTHORITY_SCHEMA
           and authority.get("phase") == "CLOE-W5-PGDG1"
           and authority.get("caps") == CAPS and authority.get("previous_caps") == RECOVERY2_CAPS
           and authority.get("core_attempt") == 3
           and authority.get("previous_session") == str(previous)
           and authority.get("recovery_session") == str(session), "recovery authority")
    for key in ("operator_selection", "decision", "annex"):
        record = authority[key]
        bound_file(Path(record["path"]), record)
    refuse(not any(p.is_symlink() for p in (previous, *previous.parents))
           and previous.stat().st_uid == os.getuid()
           and previous.stat().st_mode & 0o777 == 0o700
           and not previous.joinpath("allocation.next").exists()
           and not previous.joinpath("allocation.next").is_symlink(), "previous session custody")
    files = authority["previous_files"]
    refuse(set(files) == {"allocation.json", "core.plan.json", "result"}, "previous metadata inventory")
    old = strict_object(bound_file(previous / "allocation.json", files["allocation.json"]))
    core = strict_object(bound_file(previous / "core.plan.json", files["core.plan.json"]))
    result = strict_object(bound_file(Path(files["result"]["path"]), files["result"]))
    refuse(old.get("schema") == "cloe-pgvector-allocation-v1" and old.get("caps") == RECOVERY2_CAPS
           and all(type(old["caps"][k]) is int for k in RECOVERY2_CAPS), "previous allocation caps")
    used = previous_used(old.get("used"), attempt=2)
    refuse(used == authority.get("expected_previous_used"), "previous counters changed")
    validate_plan(core, expected_caps=RECOVERY2_CAPS)
    records = old.get("records")
    refuse(isinstance(records, dict)
           and set(records) == {"derived_archive", "core_export", "core_result", "recovery_parent"}, "previous records")
    records = cast(dict[str, Any], records)
    failed = records["core_result"]
    refuse(core["role"] == "core" and core.get("core_attempt") == 2
           and core.get("allocation_path") == str(previous / "allocation.json")
           and core.get("export_path") == str(previous / "runtime-export.tar")
           and core["derived"] == records["derived_archive"], "previous core binding")
    refuse(validate_history(core, used, records) == 2, "previous recovery history")
    expected_failure = {"exit": 1, "cleanup_proven": False,
        "candidate_head": core["candidate_head"],
        "candidate_snapshot_sha256": digest(json_bytes(core["candidate_snapshot"]))}
    refuse(failed == expected_failure and result.get("phase") == "CLOE-W5-PGDG1"
           and result.get("role") == "core" and result.get("exit") == 1
           and result.get("pytest_summaries") == [] and result.get("source_preserved") is True
           and result.get("host_resources_preserved") is True
           and result.get("owned_process_cleanup_proven") is True
           and result.get("remaining_process_count") == 0
           and result.get("offline_operations_zero") is True and result.get("phase_passed") is False
           and result.get("runtime_allocation_state") == old, "previous failure/cleanup receipt")
    refuse(core["candidate_head"] == document["payload"]["candidate_head"], "recovery HEAD changed")
    exported = records["core_export"]
    refuse(exported.get("validated_ordered_layers") is True
           and exported.get("runtime_config") == core["derived"]["runtime_config"], "previous export proof")
    descriptor = core["archive"]
    refuse(descriptor.get("path") == str(previous / "base-runtime.tar")
           and type(descriptor.get("bytes")) is int and 0 < descriptor["bytes"] <= MAX_ARCHIVE
           and descriptor["bytes"] == core["derived"]["bytes"]
           and descriptor["sha256"] == core["derived"]["sha256"], "retained materialization descriptor")
    private_file(Path(descriptor["path"]))
    refuse(Path(descriptor["path"]).stat().st_size == descriptor["bytes"], "retained archive size")
    return authority, old, core


def copy_archive(source: Path, destination: Path, descriptor: dict[str, Any],
                 meter: Allocation) -> None:
    private_file(source)
    size = descriptor["bytes"]
    refuse(type(size) is int and 0 < size <= MAX_ARCHIVE and source.stat().st_size == size,
           "recovery archive size")
    deadline = time.monotonic() + 300
    input_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except BaseException:
        os.close(input_fd)
        raise
    with os.fdopen(input_fd, "rb") as reader, os.fdopen(output_fd, "wb") as writer:
        before = os.fstat(reader.fileno())
        count, hasher = 0, hashlib.sha256()
        while True:
            refuse(time.monotonic() < deadline, "recovery copy timeout")
            raw = meter.read(reader.read, min(BLOCK, size + 1 - count), transfer=True)
            if not raw:
                break
            count += len(raw)
            refuse(count <= size, "recovery archive overlength")
            hasher.update(raw)
            writer.write(raw)
        after = os.fstat(reader.fileno())
        refuse(count == size and hasher.hexdigest() == descriptor["sha256"]
               and (before.st_ino, before.st_size, before.st_mtime_ns) ==
                   (after.st_ino, after.st_size, after.st_mtime_ns), "recovery archive custody/hash")
        writer.flush()
        os.fsync(writer.fileno())
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def recover_core(*, source: Path, declaration: Path, previous: Path,
                 session: Path) -> dict[str, Any]:
    document = load_declaration(declaration, source)
    authority, old, core = recovery_inputs(document, previous, session)
    refuse(session.is_absolute() and not session.exists() and session != previous
           and not session.is_relative_to(previous) and not previous.is_relative_to(session)
           and not session.is_relative_to(source) and not source.is_relative_to(session)
           and not any(p.is_symlink() for p in session.parents), "fresh recovery session required")
    session.mkdir(mode=0o700)
    write_once(session / "recovery-INTENT.json", json_bytes({
        "phase": "CLOE-W5-PGDG1", "declaration_sha256": digest(json_bytes(document)),
        "previous_allocation_sha256": authority["previous_files"]["allocation.json"]["sha256"],
        "core_attempt": 3, "materialization_starts": 0, "failure_reopens_slot": False}))
    parent = {"schema": PARENT_SCHEMA, "phase": "CLOE-W5-PGDG1",
        "previous_allocation_sha256": authority["previous_files"]["allocation.json"]["sha256"],
        "previous_plan_sha256": authority["previous_files"]["core.plan.json"]["sha256"],
        "previous_result_sha256": authority["previous_files"]["result"]["sha256"],
        "original_caps": old["caps"], "original_used": dict(old["used"]),
        "original_records": old["records"]}
    state = {"schema": "cloe-pgvector-allocation-v1", "caps": CAPS,
             "used": dict(old["used"]), "records": {
                 "derived_archive": old["records"]["derived_archive"], "recovery_parent": parent}}
    allocation = session / "allocation.json"
    write_once(allocation, json_bytes(state))
    write_once(session / "declaration.json", json_bytes(document))
    with Allocation(allocation).session() as meter:
        size = core["archive"]["bytes"]
        read_upper = meter.used["read_bytes"] + 4 * size + 4 * MAX_ARCHIVE + 16 * 1024**2
        transfer_upper = meter.used["transfer_bytes"] + 2 * size + 2 * MAX_ARCHIVE + 16 * 1024**2
        refuse(read_upper <= CAPS["read_bytes"] and transfer_upper <= CAPS["transfer_bytes"],
               "recovery forecast exceeds cumulative envelope")
        write_once(session / "read-forecast.json", json_bytes({
            "cumulative_read_upper_bytes": read_upper, "cumulative_transfer_upper_bytes": transfer_upper,
            "previous_used": old["used"], "new_materializations": 0}))
        copy_archive(Path(core["archive"]["path"]), session / "base-runtime.tar", core["archive"], meter)
        plan = {**plan_common(document), "role": "core", "core_attempt": 3,
            "recovery_parent_sha256": digest(json_bytes(parent)), "derived": core["derived"],
            "allocation_path": str(allocation), "export_path": str(session / "runtime-export.tar"),
            "archive": {**core["archive"], "path": str(session / "base-runtime.tar")}}
        validate_plan(plan)
        require_attempt(meter, plan)
        verify_checkout(source, plan["candidate_snapshot"], plan["candidate_head"])
        # Revalidate immutable metadata after copying; never write to the predecessor.
        recovery_inputs(document, previous, session)
        write_once(session / "core.plan.json", json_bytes(plan))
    return {"core_plan": str(session / "core.plan.json"), "core_attempt": 3,
            "new_materializations": 0, "cumulative_materializations": 1}
