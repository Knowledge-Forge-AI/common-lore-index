"""Read-only BODY evidence retention and reservation binding."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, cast

from retrieval_body_study_contracts import (
    BODY_FAILURE_BYTES as MAX_FAILURE_BYTES, BODY_FAILURE_EVIDENCE_MEMBER as FAILURE_EVIDENCE_MEMBER,
    BODY_FAILURE_MEMBER as FAILURE_MEMBER, BODY_OBSERVATION_TOTAL_BYTES as MAX_OBSERVATION_TOTAL,
    BODY_REPORT_BYTES as MAX_REPORT_BYTES, BODY_REPORT_MEMBER as REPORT_MEMBER, BODY_RESULT_MEMBERS_BYTES,
)
from retrieval_body_study_files import read_member
from retrieval_body_study_host_report import report_journal_matches, validate_actual_body_envelope
from retrieval_body_study_journal_io import MAX_EVENT_BYTES, MAX_EVENTS
from retrieval_body_study_observation_record import MAX_OBSERVATION_BYTES, MAX_SEALED_OBSERVATIONS
from retrieval_body_study_observation import (
    observation_hash, observation_identity, validate_body_failure_envelope,
)
from retrieval_body_study_plan import plan_digest
from retrieval_body_study_report import validate_actual_body_report

JOURNAL_DIR = "attempt-journal"
OBSERVATION_DIR = "attempt-journal-observations"
MAX_JOURNAL_BYTES = MAX_EVENTS * MAX_EVENT_BYTES
_EVENT = re.compile(r"[0-9]{5}\.json\Z")
_SEALED = re.compile(r"sealed-[0-9]{4}\.json\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")

class BodyEvidenceRefusal(ValueError):
    """Safe retention failure that never exposes a path or payload."""

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "body_evidence_refused"
        super().__init__(self.code)
def _require(condition: object, code: str) -> None:
    if not condition:
        raise BodyEvidenceRefusal(code)
def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise BodyEvidenceRefusal("body_evidence_noncanonical") from None

def _observation_canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

def _domain_digest(value: object) -> str:
    return _digest(_canonical(value))

def _json(raw: bytes, *, canonical: bool) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            _require(key not in value, "body_evidence_duplicate_member")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, ValueError, UnicodeError):
        raise BodyEvidenceRefusal("body_evidence_malformed") from None
    _require(isinstance(value, dict), "body_evidence_object")
    if canonical:
        _require(_canonical(value) + b"\n" == raw, "body_evidence_noncanonical")
    return cast(dict[str, Any], value)

def _root(root: Path, code: str, mode: int | tuple[int, ...] | None = None) -> None:
    _require(isinstance(root, Path) and root.is_absolute() and root.resolve() == root, code)
    try:
        info = root.lstat()
    except OSError:
        raise BodyEvidenceRefusal(code) from None
    allowed = None if mode is None else {mode} if isinstance(mode, int) else set(mode)
    _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
             and info.st_uid == os.getuid()
             and (allowed is None or stat.S_IMODE(info.st_mode) in allowed), code)

def _member(root: Path, name: str, maximum: int, charge: Callable[[int], None] | None) -> bytes:
    target = root / name
    try:
        info = target.lstat()
    except FileNotFoundError:
        raise BodyEvidenceRefusal("body_evidence_missing") from None
    except OSError:
        raise BodyEvidenceRefusal("body_evidence_unavailable") from None
    _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
             and info.st_uid == os.getuid() and info.st_nlink == 1
             and 0 < info.st_size <= maximum,
             "body_evidence_member_custody")
    if charge is not None:
        charge(info.st_size + 1)
    try:
        raw = read_member(root, name, max_bytes=info.st_size)
    except Exception:
        raise BodyEvidenceRefusal("body_evidence_read") from None
    _require(len(raw) == info.st_size, "body_evidence_member_size")
    return raw

def _optional(root: Path, name: str, maximum: int, charge: Callable[[int], None]) -> tuple[bytes | None, str | None]:
    try:
        raw = _member(root, name, maximum, charge)
    except BodyEvidenceRefusal as error:
        if error.code == "body_evidence_missing":
            return None, None
        raise
    return raw, _digest(raw)

def _names(root: Path, pattern: re.Pattern[str], maximum: int,
           mode: int | tuple[int, ...]) -> tuple[str, ...]:
    _root(root, "body_evidence_directory", mode)
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        raise BodyEvidenceRefusal("body_evidence_directory") from None
    names: list[str] = []
    for child in children:
        try:
            info = child.lstat()
        except OSError:
            raise BodyEvidenceRefusal("body_evidence_directory") from None
        if child.name == ".lock":
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                     and info.st_nlink == 1 and info.st_size == 0
                     and stat.S_IMODE(info.st_mode) == 0o640,
                     "body_evidence_lock")
            continue
        _require(pattern.fullmatch(child.name) is not None
                 and stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and info.st_uid == os.getuid()
                 and stat.S_IMODE(info.st_mode) == 0o640 and info.st_nlink == 1
                 and 0 < info.st_size <= maximum, "body_evidence_member_custody")
        names.append(child.name)
    return tuple(names)

def _read_observations(
    root: Path, charge: Callable[[int], None]
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], str | None, tuple[dict[str, Any], ...], int]:
    try:
        names = _names(root, _SEALED, MAX_OBSERVATION_BYTES, 0o750)
    except BodyEvidenceRefusal as error:
        if error.code == "body_evidence_directory" and not root.exists() and not root.is_symlink():
            return (), (), None, (), 0
        raise
    _require(len(names) <= MAX_SEALED_OBSERVATIONS and names == tuple(f"sealed-{i:04d}.json" for i in range(len(names))),
             "body_evidence_observation_history")
    charge(MAX_OBSERVATION_TOTAL + len(names))
    rows: list[dict[str, Any]] = []
    raw_hashes: list[bytes] = []
    total = 0
    for name in names:
        raw = _member(root, name, min(MAX_OBSERVATION_BYTES, MAX_OBSERVATION_TOTAL - total), None)
        total += len(raw)
        _require(total <= MAX_OBSERVATION_TOTAL, "body_evidence_observation_bytes")
        value = _json(raw, canonical=False)
        _require(_observation_canonical(value) + b"\n" == raw,
                 "body_evidence_noncanonical")
        rows.append(value)
        raw_hashes.append(raw)
    hashes = tuple(observation_hash(row) for row in rows)
    manifest = tuple({"index": index, "observation_sha256": hashes[index], "bytes": len(raw),
                      "raw_sha256": _digest(raw)} for index, raw in enumerate(raw_hashes))
    return tuple(rows), hashes, _digest(b"".join(raw_hashes)), manifest, total

def _replay(rows: Sequence[Mapping[str, Any]], tasks: tuple[str, ...], functional_only: bool,
            observations: Sequence[Mapping[str, Any]], failure: Mapping[str, Any] | None) -> None:
    try:
        from retrieval_body_study_attempt_journal import _validate_replay
        _validate_replay(rows, tasks, functional_only, observations, failure)
    except BodyEvidenceRefusal:
        raise
    except Exception:
        raise BodyEvidenceRefusal("body_evidence_journal_replay") from None

def _read_journal(root: Path, observation_root: Path, plan: Mapping[str, Any],
                  charge: Callable[[int], None], failure: Mapping[str, Any] | None) -> dict[str, Any]:
    names = _names(root, _EVENT, MAX_EVENT_BYTES, (0o700, 0o750))
    _require(names == tuple(f"{i:05d}.json" for i in range(len(names))) and names,
             "body_evidence_journal_history")
    _require(len(names) <= MAX_EVENTS, "body_evidence_journal_events")
    charge(MAX_JOURNAL_BYTES + len(names))
    rows: list[dict[str, Any]] = []
    raw_hashes: list[bytes] = []
    previous = "sha256:" + "0" * 64
    fields = {"schema", "index", "identity_sha256", "previous_sha256", "event", "payload", "event_sha256"}
    for index, name in enumerate(names):
        raw = _member(root, name, MAX_EVENT_BYTES, None)
        row = _json(raw, canonical=True)
        _require(set(row) == fields and row.get("schema") == "cloe-body-study-attempt-journal-v1"
                 and row.get("index") == index and row.get("previous_sha256") == previous,
                 "body_evidence_journal_chain")
        claimed = row.get("event_sha256")
        value = {key: item for key, item in row.items() if key != "event_sha256"}
        _require(claimed == _domain_digest(value), "body_evidence_journal_digest")
        _require(type(claimed) is str and _DIGEST.fullmatch(claimed) is not None,
                 "body_evidence_journal_digest")
        previous = cast(str, claimed)
        rows.append(row)
        raw_hashes.append(raw)
    first = rows[0]
    payload = first.get("payload")
    _require(first.get("event") == "attempt_created" and isinstance(payload, Mapping),
             "body_evidence_journal_creation")
    assert isinstance(payload, Mapping)
    identity = payload.get("identity")
    expected = {"attempt_id": plan["attempt_id"], "input_pin_sha256": plan["input_pin_sha256"],
                "source_closure_sha256": plan["source_closure_sha256"],
                "treatment_sha256": plan["treatment_sha256"], "protocol_sha256": plan["protocol_sha256"],
                "candidate_snapshot_sha256": plan["candidate_snapshot_sha256"],
                "plan_sha256": plan_digest(plan)}
    identity_sha = _domain_digest(expected)
    _require(set(payload) == {"identity", "task_ids", "functional_only"}
             and identity == expected and payload.get("functional_only") is False,
             "body_evidence_journal_identity")
    task_ids = payload.get("task_ids")
    _require(isinstance(task_ids, list) and all(type(item) is str for item in task_ids),
             "body_evidence_journal_tasks")
    _require(len(cast(list[str], task_ids)) == 24 and len(set(cast(list[str], task_ids))) == len(cast(list[str], task_ids))
             and task_ids == sorted({row["task_id"] for row in plan["declaration"]["slot_manifest"]}), "body_evidence_journal_tasks")
    _require(rows[-1].get("event") == "attempt_terminal"
             and all(row.get("event") != "attempt_terminal" for row in rows[:-1]),
             "body_evidence_journal_terminal")
    terminal = rows[-1].get("payload")
    _require(isinstance(terminal, Mapping) and type(terminal.get("report_sha256")) is str
             and _DIGEST.fullmatch(terminal["report_sha256"]) is not None,
             "body_evidence_journal_terminal")
    assert isinstance(terminal, Mapping)
    _require(all(row.get("identity_sha256") == identity_sha for row in rows),
             "body_evidence_journal_identity")
    common = [row.get("payload", {}).get("common_set_sha256") for row in rows
              if row.get("event") == "common_set_frozen" and isinstance(row.get("payload"), Mapping)]
    _require(len(common) <= 1 and all(type(item) is str and _DIGEST.fullmatch(item) is not None
                                      for item in common), "body_evidence_common_history")
    observed = [row.get("payload", {}).get("observation_sha256") for row in rows
                if row.get("event") == "slot_observed" and isinstance(row.get("payload"), Mapping)]
    obs_rows, obs_hashes, observations_raw_sha, observation_manifest, observations_bytes = _read_observations(observation_root, charge)
    _require(tuple(observed) == tuple(hash_value for hash_value, row in zip(obs_hashes, obs_rows, strict=True)
                     if row.get("record_kind", "observation") == "observation"),
             "body_evidence_observation_history")
    _replay(rows, tuple(cast(list[str], task_ids)), False, obs_rows, failure)
    if "sealed_observation_identity" in terminal:
        _require(terminal.get("sealed_observation_identity") == observation_identity(obs_rows),
                 "body_evidence_observation_identity")
    if failure is not None: _validate_failure_observations(failure, obs_rows, observation_manifest, observations_bytes)
    return {"raw_sha256": _digest(b"".join(raw_hashes)), "bytes": sum(map(len, raw_hashes)),
            "journal_sha256": previous,
            "terminal_report_sha256": terminal["report_sha256"],
            "common_identity": common[0] if common else None,
            "observations_raw_sha256": observations_raw_sha,
            "observations_bytes": observations_bytes,
            "terminal": dict(terminal)}
def _join_failure(value: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    for field in ("attempt_id", "plan_sha256", "source_closure_sha256", "treatment_sha256", "input_pin_sha256",
                  "worker_closure_sha256", "evaluator_closure_sha256", "protocol_sha256", "operator_decision_sha256"):
        expected = plan["attempt_id"] if field == "attempt_id" else plan_digest(plan) if field == "plan_sha256" else plan[field]
        _require(value.get(field) == expected, "body_evidence_failure_join")
def _validate_failure_observations(value: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                                  manifest: Sequence[Mapping[str, Any]], total: int) -> None:
    identity = observation_identity(rows)
    _require(value.get("sealed_observation_manifest") == list(manifest)
             and value.get("sealed_observation_count") == len(manifest)
             and value.get("sealed_observation_bytes") == total
             and value.get("sealed_observation_identity") == identity
             and value.get("journal_observation_identity") == identity,
             "body_evidence_failure_observation_join")
def _artifact(path: str, raw_sha256: str | None, size: int | None, valid: bool,
              error: str | None = None) -> dict[str, Any]:
    return {"path": path, "raw_sha256": raw_sha256, "bytes": size, "valid": valid,
            "error": error}

def retain_body_evidence(report_root: Path, *, plan: Mapping[str, Any], cleanup: Mapping[str, Any],
                         charge_bytes: Callable[[int], None]) -> dict[str, Any]:
    """Retain raw pointers and validate every available BODY result artifact."""
    _root(report_root, "body_evidence_report_root", (0o700, 0o750))
    plan_sha256 = plan_digest(plan)
    artifacts: dict[str, Any] = {}
    errors: list[str] = []
    report_value: dict[str, Any] | None = None
    report_raw_sha: str | None = None
    failures: dict[str, dict[str, Any]] = {}
    failure_pointer_sha: str | None = None
    remaining = BODY_RESULT_MEMBERS_BYTES

    def charge_result(amount: int) -> None:
        nonlocal remaining
        _require(amount - 1 <= remaining, "body_evidence_result_members")
        remaining -= amount - 1
        charge_bytes(amount)

    try:
        raw, report_raw_sha = _optional(report_root, REPORT_MEMBER, MAX_REPORT_BYTES, charge_result)
        if raw is None:
            artifacts["report"] = _artifact(REPORT_MEMBER, None, None, False, "missing")
        else:
            try:
                value = _json(raw, canonical=False)
                checked = validate_actual_body_report(value)
                joined = {**checked, "inner_cleanup": checked.get("owned_cleanup"),
                          "cleanup": dict(cleanup), "owned_cleanup": dict(cleanup)}
                report_value = validate_actual_body_envelope(joined, plan=plan, cleanup=cleanup)
                artifacts["report"] = _artifact(REPORT_MEMBER, report_raw_sha, len(raw), True)
            except Exception:
                artifacts["report"] = _artifact(REPORT_MEMBER, report_raw_sha, len(raw), False, "invalid")
                errors.append("report_invalid")
    except Exception:
        artifacts["report"] = _artifact(REPORT_MEMBER, None, None, False, "unavailable")
        errors.append("report_unavailable")
    for key, name in (("failure", FAILURE_MEMBER), ("failure_evidence", FAILURE_EVIDENCE_MEMBER)):
        try:
            raw, raw_sha = _optional(report_root, name, MAX_FAILURE_BYTES, charge_result)
            if raw is None:
                artifacts[key] = _artifact(name, None, None, False, "missing")
                continue
            if raw_sha is not None and failure_pointer_sha is None:
                failure_pointer_sha = raw_sha
            try:
                value = validate_body_failure_envelope(_json(raw, canonical=False))
                _join_failure(value, plan)
                artifacts[key] = _artifact(name, raw_sha, len(raw), True)
                failures[key] = value
            except Exception:
                artifacts[key] = _artifact(name, raw_sha, len(raw), False, "invalid")
                errors.append(key + "_invalid")
        except Exception:
            artifacts[key] = _artifact(name, None, None, False, "unavailable")
            errors.append(key + "_unavailable")
    failure_value = failures.get("failure")
    precleanup = failures.get("failure_evidence")
    if failure_value is not None and failure_value.get("precleanup_envelope_sha256") != (
        precleanup["report_identity"] if precleanup is not None else None
    ):
        errors.append("failure_precleanup_join")
    journal_value: dict[str, Any] | None = None
    try:
        journal_value = _read_journal(report_root / JOURNAL_DIR, report_root / OBSERVATION_DIR,
                                      plan, charge_bytes, failure_value)
        artifacts["journal"] = _artifact(JOURNAL_DIR, journal_value["raw_sha256"],
                                          journal_value["bytes"], True)
        empty_observations = journal_value["terminal"].get("sealed_observation_count") == 0
        artifacts["observations"] = _artifact(
            OBSERVATION_DIR, journal_value["observations_raw_sha256"],
            journal_value["observations_bytes"],
            journal_value["observations_raw_sha256"] is not None or empty_observations,
            None if journal_value["observations_raw_sha256"] is not None or empty_observations
            else "missing",
        )
    except Exception:
        artifacts["journal"] = _artifact(JOURNAL_DIR, None, None, False, "invalid_or_unavailable")
        artifacts["observations"] = _artifact(OBSERVATION_DIR, None, None, False,
                                                "invalid_or_unavailable")
        errors.append("journal_invalid_or_unavailable")
    journal_sha = journal_value["journal_sha256"] if journal_value else None
    if journal_value:
        terminal = journal_value["terminal"]
        if report_value is not None and not report_journal_matches(report_value, journal_value):
            errors.append("report_journal_join")
        if failure_value and terminal.get("failure_envelope_sha256") != failure_value["report_identity"]:
            errors.append("failure_journal_join")
        if errors:
            journal_sha = journal_value["raw_sha256"]
    if report_value is not None and failures:
        outcome = report_value.get("quality_outcome", report_value.get("outcome"))
        if outcome not in {"incomplete_technical", "inadmissible"} or failure_value is None:
            errors.append("unexpected_failure_artifact")
    present = any(item.get("raw_sha256") is not None for item in artifacts.values())
    retained = report_value is not None and report_raw_sha is not None and journal_sha is not None and not errors
    state = "retained" if retained else "partial" if present else "unavailable"
    binding = {"schema": "cloe-body-study-attempt-evidence-binding-v1", "plan_sha256": plan_sha256,
               "attempt_id": plan["attempt_id"], "state": state,
               "report_sha256": report_raw_sha,
               "journal_sha256": journal_sha, "failure_sha256": failure_pointer_sha}
    return {"state": state, "binding": binding, "artifacts": artifacts, "errors": tuple(errors),
            "report": report_value}

def capture_body_evidence(report_root: Path, *, plan: Mapping[str, Any],
                          cleanup: Mapping[str, Any],
                          charge_bytes: Callable[[int], None]) -> dict[str, Any]:
    """Keep settlement possible if the bounded retention wrapper itself fails."""
    try:
        return retain_body_evidence(report_root, plan=plan, cleanup=cleanup,
                                    charge_bytes=charge_bytes)
    except BaseException as error:
        code = getattr(error, "code", "body_evidence_retention")
        if not isinstance(code, str) or re.fullmatch(r"[a-z0-9_]+", code) is None:
            code = "body_evidence_retention"
        binding = {"schema": "cloe-body-study-attempt-evidence-binding-v1",
                   "plan_sha256": plan_digest(plan), "attempt_id": plan["attempt_id"],
                   "state": "unavailable", "report_sha256": None,
                   "journal_sha256": None, "failure_sha256": None}
        return {"state": "unavailable", "binding": binding, "artifacts": {},
                "errors": (code,), "report": None}
__all__ = ["BodyEvidenceRefusal", "capture_body_evidence", "retain_body_evidence"]
