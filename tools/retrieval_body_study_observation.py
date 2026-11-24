"""Bounded sealed BODY observations retained for rank-blind defect review."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from retrieval_body_study_contracts import (
    BODY_FAILURE_BYTES as MAX_FAILURE_BYTES, BODY_FAILURE_EVIDENCE_MEMBER as FAILURE_EVIDENCE_NAME,
    BODY_FAILURE_MEMBER as FAILURE_REPORT_NAME, BODY_OBSERVATION_TOTAL_BYTES as MAX_SEALED_BYTES,
)
from retrieval_body_study_files import publish_member, read_member
from retrieval_body_study_observation_record import (
    MAX_OBSERVATION_BYTES, MAX_SEALED_OBSERVATIONS, SCHEMA, _canonical, _record_body,
)
from retrieval_body_study_values import (
    SLOT_COUNT, STUDY_PHASE, digest_value, is_digest, require, thaw,
)
_PREFIX = "sealed-"
FAILURE_REPORT_SCHEMA = "cloe-body-study-failure-report-v1"
def raw_sha256(raw: bytes) -> str:
    require(type(raw) is bytes, "body.observation:raw_bytes")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
def observation_hash(record: Mapping[str, Any]) -> str:
    require(isinstance(record, Mapping) and record.get("schema") == SCHEMA,
            "body.observation:schema")
    require(record.get("record_kind") in {"observation", "failure"}
            and record.get("scientific_acceptance") is False
            and type(record.get("origin_proven")) is bool
            and (record.get("record_kind") != "failure" or record.get("origin_proven") is False),
            "body.observation:acceptance")
    claimed = record.get("observation_sha256")
    body = {key: value for key, value in record.items() if key != "observation_sha256"}
    require(claimed == digest_value(body), "body.observation:identity")
    require(type(claimed) is str and claimed.startswith("sha256:"),
            "body.observation:hash")
    assert isinstance(claimed, str)
    return claimed


def observation_record(observation: object, *, index: int,
                       record_kind: str = "observation") -> dict[str, Any]:
    """Return a complete, JSON-safe record without exposing object repr/raw payloads."""
    return _record_body(observation, index=index, record_kind=record_kind)
def observation_bytes(record: Mapping[str, Any]) -> int:
    observation_hash(record)
    return len(_canonical(record) + b"\n")


def _members(root: Path, *, allow_missing: bool = False) -> tuple[str, ...]:
    require(isinstance(root, Path) and root.is_absolute() and root.resolve() == root,
            "body.observation:root")
    if not root.exists():
        require(allow_missing, "body.observation:root")
        return ()
    require(root.exists() and root.is_dir() and not root.is_symlink(),
            "body.observation:root")
    names = tuple(sorted(item.name for item in root.iterdir()))
    require(all(name.startswith(_PREFIX) and name.endswith(".json") for name in names),
            "body.observation:member")
    expected = tuple(f"{_PREFIX}{index:04d}.json" for index in range(len(names)))
    require(names == expected and len(names) <= MAX_SEALED_OBSERVATIONS,
            "body.observation:history")
    return names


def publish_observation(root: Path, observation: object, *, index: int,
                        record_kind: str = "observation",
                        aggregate_bytes: int | None = None,
                        functional_only: bool = False) -> dict[str, Any]:
    """Persist one complete sealed record before its journal hash event."""
    require(isinstance(root, Path) and root.is_absolute() and root.resolve() == root,
            "body.observation:root")
    names = _members(root, allow_missing=True)
    require(index == len(names), "body.observation:order")
    record = _record_body(observation, index=index, record_kind=record_kind,
                          functional_only=functional_only)
    raw = _canonical(record) + b"\n"
    existing_bytes = aggregate_bytes
    if existing_bytes is None:
        existing_bytes = 0
        for name in names:
            existing_bytes += len(read_member(root, name, max_bytes=MAX_OBSERVATION_BYTES))
            require(existing_bytes <= MAX_SEALED_BYTES, "body.observation:aggregate_byte_bound")
    require(type(existing_bytes) is int and 0 <= existing_bytes <= MAX_SEALED_BYTES,
            "body.observation:aggregate_byte_bound")
    require(existing_bytes + len(raw) <= MAX_SEALED_BYTES,
            "body.observation:aggregate_byte_bound")
    publish_member(root.parent, f"{root.name}/{_PREFIX}{index:04d}.json", raw)
    return record


def read_observation(root: Path, index: int) -> dict[str, Any]:
    """Revalidate one retained record without rescanning prior payloads."""
    require(type(index) is int and 0 <= index < MAX_SEALED_OBSERVATIONS,
            "body.observation:index")
    names = _members(root)
    require(index < len(names), "body.observation:missing")
    raw = read_member(root, names[index], max_bytes=MAX_OBSERVATION_BYTES)
    try:
        value = json.loads(raw, object_pairs_hook=dict)
    except (TypeError, ValueError, UnicodeError):
        require(False, "body.observation:malformed")
        return {}
    require(isinstance(value, dict) and value.get("index") == index,
            "body.observation:index")
    assert isinstance(value, dict)
    observation_hash(value)
    require(_canonical(value) + b"\n" == raw, "body.observation:canonical")
    return value


def read_observations(root: Path) -> tuple[dict[str, Any], ...]:
    """Read and revalidate all complete original records retained by a journal."""
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for index, name in enumerate(_members(root)):
        raw = read_member(root, name, max_bytes=MAX_OBSERVATION_BYTES)
        total_bytes += len(raw)
        require(total_bytes <= MAX_SEALED_BYTES, "body.observation:aggregate_byte_bound")
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                require(key not in result, "body.observation:duplicate_member")
                result[key] = item
            return result
        try:
            value = json.loads(raw, object_pairs_hook=unique)
        except (TypeError, ValueError, UnicodeError):
            require(False, "body.observation:malformed")
            continue
        require(isinstance(value, dict) and value.get("index") == index,
                "body.observation:index")
        assert isinstance(value, dict)
        observation_hash(value)
        require(_canonical(value) + b"\n" == raw, "body.observation:canonical")
        rows.append(value)
    return tuple(rows)


def observation_artifact_digests(root: Path) -> tuple[str, ...]:
    """Return raw-file digests in sealed index order, independent of report hashes."""
    result = []
    total_bytes = 0
    for name in _members(root):
        raw = read_member(root, name, max_bytes=MAX_OBSERVATION_BYTES)
        total_bytes += len(raw)
        require(total_bytes <= MAX_SEALED_BYTES, "body.observation:aggregate_byte_bound")
        result.append(raw_sha256(raw))
    return tuple(result)


def observation_identity(records: Sequence[Mapping[str, Any]]) -> str:
    hashes = [observation_hash(record) for record in records]
    return digest_value(hashes)


def _failure_cleanup(cleanup: Mapping[str, Any]) -> dict[str, Any]:
    return {key: cleanup.get(key) if type(cleanup.get(key)) is bool else None
            for key in ("cleanup_ok", "relations_clean", "container_absent",
                        "ownership_ok", "integrity_ok")}


def _failure_slots(slots: Any) -> tuple[list[dict[str, Any]], dict[str, int], int | None]:
    rows: list[dict[str, Any]] = []
    slot_bytes = 0
    for item in getattr(slots, "actual_slots", ()) if slots is not None else ():
        row = dict(item)
        slot_bytes += len(_canonical(row))
        require(slot_bytes <= MAX_FAILURE_BYTES, "body.failure:slot_byte_bound")
        rows.append(row)
    counts = {state: sum(row.get("execution_state") == state for row in rows)
              for state in sorted({"completed", "refused", "unavailable", "failed",
                                   "not_run", "unstarted", "missing", "timed_out"})}
    raw_unstarted = getattr(slots, "unstarted_slots", None)
    unstarted = raw_unstarted if type(raw_unstarted) is int else None
    return rows, counts, unstarted if unstarted is not None else (
        counts.get("unstarted", 0) if rows else None
    )


def _failure_workers(receipts: Sequence[Any]) -> list[dict[str, Any]]:
    return [{key: getattr(item, key, None) for key in
             ("identity", "representation", "response_identity", "admission_identity",
              "reachability_identity")} for item in receipts]


def build_body_failure_envelope(
    *, attempt_id: str | None, failure_code: str, failure_stage: str,
    cleanup: Mapping[str, Any], journal: Any = None, worker_receipts: Sequence[Any] = (),
    slots: Any = None, functional_only: bool = False,
    precleanup_envelope_sha256: str | None = None,
) -> dict[str, Any]:
    """Build an explicitly non-passing envelope without inventing work or origin."""
    require(type(failure_code) is str and bool(failure_code), "body.failure:code")
    require(type(failure_stage) is str and bool(failure_stage), "body.failure:stage")
    require(isinstance(cleanup, Mapping), "body.failure:cleanup")
    observations: tuple[dict[str, Any], ...] = ()
    retention_error: str | None = None
    retained = getattr(journal, "retained_observations", None)
    if callable(retained):
        try:
            observations = tuple(dict(row) for row in retained())
        except Exception as error:
            retention_error = type(error).__name__
    rows, counts, unstarted = _failure_slots(slots)
    journal_identity = observation_identity(observations)
    sealed_bytes = 0
    for row in observations:
        sealed_bytes += len(_canonical(row) + b"\n")
        require(sealed_bytes <= MAX_SEALED_BYTES, "body.failure:aggregate_byte_bound")
    envelope: dict[str, Any] = {
        "schema": FAILURE_REPORT_SCHEMA, "phase": STUDY_PHASE, "role": "body-study",
        "attempt_id": attempt_id if type(attempt_id) is str else None,
        "status": ("inadmissible" if "inadmissible" in failure_code
                   else "incomplete" if "incomplete" in failure_code or rows or observations else "failed"),
        "failure_code": failure_code[:128], "failure_stage": failure_stage[:128],
        "functional_only": functional_only, "origin_proven": False,
        "execution_authority_granted": False, "scientific_acceptance": False,
        "scientific_acceptance_claimed": False, "primary": None,
        "worker_count": len(tuple(worker_receipts)),
        "workers": _failure_workers(worker_receipts),
        "observed_slot_count": sum(row.get("execution_state") != "unstarted" for row in rows),
        "slot_record_count": len(rows), "unstarted_slot_count": unstarted,
        "slot_state_counts": counts, "slots": rows,
        "sealed_observation_count": len(observations),
        "sealed_observation_bytes": sealed_bytes,
        "sealed_observation_identity": observation_identity(observations),
        "journal_observation_identity": journal_identity,
        "sealed_observation_manifest": [
            {"index": row["index"], "observation_sha256": row["observation_sha256"],
             "bytes": observation_bytes(row), "raw_sha256": raw_sha256(_canonical(row) + b"\n")}
            for row in observations
        ],
        "retention_error": retention_error,
        "cleanup": _failure_cleanup(cleanup),
        "precleanup_envelope_sha256": precleanup_envelope_sha256,
    }
    for field in ("attempt_id", "input_pin_sha256", "plan_sha256",
                  "source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
                  "evaluator_closure_sha256", "protocol_sha256", "operator_decision_sha256"):
        value = cleanup.get(field)
        if type(value) is str and (field == "attempt_id" or is_digest(value)):
            envelope[field] = value
    envelope["report_identity"] = digest_value(envelope)
    return envelope


def validate_body_failure_envelope(value: object) -> dict[str, Any]:
    require(isinstance(value, Mapping), "body.failure:mapping")
    assert isinstance(value, Mapping)
    require(value.get("schema") == FAILURE_REPORT_SCHEMA
            and value.get("phase") == STUDY_PHASE
            and value.get("role") == "body-study", "body.failure:identity")
    require(value.get("primary") is None and value.get("origin_proven") is False
            and value.get("scientific_acceptance") is False
            and value.get("scientific_acceptance_claimed") is False,
            "body.failure:acceptance")
    require(type(value.get("failure_code")) is str and value["failure_code"]
            and value.get("status") in {"failed", "incomplete", "inadmissible"}
            and type(value.get("failure_stage")) is str and value["failure_stage"],
            "body.failure:status")
    require(type(value.get("worker_count")) is int and 0 <= value["worker_count"] <= 2,
            "body.failure:workers")
    workers = value.get("workers")
    require(isinstance(workers, Sequence) and not isinstance(workers, (str, bytes))
            and len(workers) == value["worker_count"], "body.failure:worker_records")
    require(type(value.get("observed_slot_count")) is int
            and 0 <= value["observed_slot_count"] <= SLOT_COUNT, "body.failure:observed_slots")
    require(value.get("unstarted_slot_count") is None
            or (type(value["unstarted_slot_count"]) is int
                and 0 <= value["unstarted_slot_count"] <= SLOT_COUNT),
            "body.failure:unstarted_slots")
    slots = value.get("slots")
    require(isinstance(slots, Sequence) and not isinstance(slots, (str, bytes))
            and len(slots) <= SLOT_COUNT
            and value.get("slot_record_count") == len(slots), "body.failure:slot_records")
    assert isinstance(slots, Sequence) and not isinstance(slots, (str, bytes))
    slot_counts = {state: sum(isinstance(row, Mapping)
                              and row.get("execution_state") == state for row in slots)
                   for state in {"completed", "refused", "unavailable", "failed",
                                 "not_run", "unstarted", "missing", "timed_out"}}
    require(all(isinstance(row, Mapping) for row in slots)
            and all(row.get("execution_state") in slot_counts for row in slots)
            and value.get("slot_state_counts") == slot_counts
            and value.get("observed_slot_count") == len(slots) - slot_counts["unstarted"]
            and (value.get("unstarted_slot_count") is None
                 or value["unstarted_slot_count"] == slot_counts["unstarted"]),
            "body.failure:slot_counts")
    manifest = value.get("sealed_observation_manifest")
    fields = {"index", "observation_sha256", "bytes", "raw_sha256"}
    require(isinstance(manifest, Sequence) and not isinstance(manifest, (str, bytes))
            and len(manifest) <= SLOT_COUNT and all(isinstance(item, Mapping) for item in manifest),
            "body.failure:observations")
    assert isinstance(manifest, Sequence)
    require(all(set(item) == fields and type(item["index"]) is int and item["index"] == index
                and is_digest(item["observation_sha256"]) and is_digest(item["raw_sha256"])
                and type(item["bytes"]) is int and 0 < item["bytes"] <= MAX_OBSERVATION_BYTES
                for index, item in enumerate(manifest)), "body.failure:observation_manifest")
    sealed_bytes = sum(item["bytes"] for item in manifest)
    require(value.get("sealed_observation_count") == len(manifest)
            and type(value.get("sealed_observation_bytes")) is int
            and 0 <= value["sealed_observation_bytes"] <= MAX_SEALED_BYTES
            and value["sealed_observation_bytes"] == sealed_bytes <= MAX_SEALED_BYTES
            and value.get("sealed_observation_identity") == digest_value(
                [item["observation_sha256"] for item in manifest]),
            "body.failure:observation_identity")
    require(is_digest(value.get("journal_observation_identity")),
            "body.failure:journal_observation_identity")
    require(value.get("report_identity") == digest_value({
        key: item for key, item in value.items() if key != "report_identity"
    }), "body.failure:report_identity")
    return dict(value)


def write_body_failure_envelope(
    report_root: Path, envelope: Mapping[str, Any], *,
    filename: str = FAILURE_REPORT_NAME,
) -> str:
    """Publish one immutable failure envelope; callers bind its identity in the journal."""
    checked = validate_body_failure_envelope(envelope)
    require(isinstance(report_root, Path) and report_root.is_absolute(), "body.failure:root")
    require(filename in {FAILURE_REPORT_NAME, FAILURE_EVIDENCE_NAME}, "body.failure:filename")
    raw = json.dumps(thaw(checked), sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    require(len(raw) <= MAX_FAILURE_BYTES, "body.failure:byte_bound")
    publish_member(report_root, filename, raw)
    return str(checked["report_identity"])


def read_body_failure_envelope(report_root: Path) -> dict[str, Any]:
    """Read one bounded, custody-checked final envelope without reopening work."""
    raw = read_member(report_root, FAILURE_REPORT_NAME, max_bytes=MAX_FAILURE_BYTES)
    try:
        value = json.loads(raw, object_pairs_hook=dict)
    except (TypeError, ValueError, UnicodeError):
        require(False, "body.failure:malformed")
        return {}
    require(isinstance(value, dict) and _canonical(value) + b"\n" == raw,
            "body.failure:canonical")
    return validate_body_failure_envelope(value)


__all__ = [
    "FAILURE_EVIDENCE_NAME", "FAILURE_REPORT_NAME", "FAILURE_REPORT_SCHEMA",
    "MAX_FAILURE_BYTES", "MAX_OBSERVATION_BYTES", "MAX_SEALED_BYTES",
    "MAX_SEALED_OBSERVATIONS", "SCHEMA",
    "build_body_failure_envelope",
    "observation_hash", "observation_identity", "observation_record",
    "observation_artifact_digests", "observation_bytes", "raw_sha256",
    "publish_observation", "read_observation", "read_observations",
    "read_body_failure_envelope",
    "validate_body_failure_envelope", "write_body_failure_envelope",
]
