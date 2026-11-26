"""Pure source-freeze record and custody-boundary checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval_body_freeze import parse_body_source_freeze
from common_lore_index_test_support.retrieval_body_source import _read_members, _relative_members, _root
from retrieval_body_input_records import INPUT_NAMES, PIN_SCHEMA
from retrieval_body_values import BodyError, canonical_bytes, digest_bytes, digest_json


def _pin() -> bytes:
    files = {
        name: {"bytes": 1, "sha256": "sha256:" + "0" * 64}
        for name in INPUT_NAMES
    }
    tasks = {
        f"unit-body-{index:02d}": {
            "retained_task_sha256": "sha256:" + "1" * 64,
            "task_sha256": "sha256:" + "2" * 64,
        }
        for index in range(1, 25)
    }
    return canonical_bytes({
        "schema": PIN_SCHEMA, "class_id": "unit-body-catalog", "corpus_id": "unit-body-corpus",
        "phase": "CLOE-W5-BODY1-UNIT", "purpose": "calibration", "input_files": files,
        "policy_digest": "sha256:" + "3" * 64,
        "predecessor_manifest_sha256": "sha256:" + "4" * 64,
        "task_digests": tasks, "source_closure_claimed": False,
        "execution_authority_granted": False,
    })


def _record() -> dict[str, object]:
    closure = {
        "schema": "cloe-body-source-closure-v1",
        "builder_members": [],
        "explicit_members": ["tools/example.py"],
        "files": {"tools/example.py": {"bytes": 1, "sha256": "sha256:" + "0" * 64}},
        "member_count": 1,
        "total_bytes": 1,
        "identity": "sha256:" + "1" * 64,
    }
    closure["identity"] = digest_json({key: closure[key] for key in ("schema", "builder_members", "explicit_members", "files")})
    pin = _pin()
    return {
        "schema": "cloe-body-source-freeze-v1",
        "class_id": "unit-body-catalog",
        "corpus_id": "unit-body-corpus",
        "phase": "CLOE-W5-BODY1-UNIT",
        "input_pin_utf8": pin.decode(),
        "input_pin_sha256": digest_bytes(pin),
        "predecessor_manifest_sha256": "sha256:" + "4" * 64,
        "predecessor_file_count": 1,
        "predecessor_commit": "a" * 40,
        "predecessor_catalog_hashes": {},
        "source_closure": closure,
        "source_closure_sha256": closure["identity"],
        "source_closure_claimed": True,
        "execution_authority_granted": False,
    }


def test_parse_freeze_binds_exact_pin_and_closure_bytes():
    record = _record()
    assert parse_body_source_freeze(json.dumps(record).encode()) == record


def test_parse_freeze_refuses_pin_byte_mutation():
    record = _record()
    pin_text = record["input_pin_utf8"]
    assert isinstance(pin_text, str)
    record["input_pin_utf8"] = pin_text + " "
    with pytest.raises(BodyError, match="pin_digest"):
        parse_body_source_freeze(json.dumps(record).encode())


def test_parse_freeze_refuses_authority_or_closure_claim_changes():
    record = _record()
    record["execution_authority_granted"] = True
    with pytest.raises(BodyError, match="execution_authority"):
        parse_body_source_freeze(json.dumps(record).encode())
    record = _record()
    closure = record["source_closure"]
    assert isinstance(closure, dict)
    closure["total_bytes"] = 2
    with pytest.raises(BodyError, match="closure_counts"):
        parse_body_source_freeze(json.dumps(record).encode())


def test_parse_freeze_refuses_duplicate_members_and_fake_counts():
    record = _record()
    closure = record["source_closure"]
    assert isinstance(closure, dict)
    closure["member_count"] = 2
    with pytest.raises(BodyError, match="closure_counts"):
        parse_body_source_freeze(json.dumps(record).encode())
    with pytest.raises(BodyError):
        parse_body_source_freeze(b'{"schema":"cloe-body-source-freeze-v1","schema":"again"}')


def test_source_helpers_refuse_symlink_root_and_duplicate_members(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "tools").mkdir()
    assert _root(real) == real
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(BodyError, match="root_custody"):
        _root(alias)
    with pytest.raises(BodyError, match="members_duplicate"):
        _relative_members(["tools/a.py", "tools/a.py"], "members")


def test_source_helper_enforces_aggregate_limit(monkeypatch):
    monkeypatch.setattr(
        "common_lore_index_test_support.retrieval_body_source.read_member",
        lambda _root, _name, max_bytes: b"x" * max_bytes,
    )
    members = [f"tools/{index}.py" for index in range(9)]
    with pytest.raises(BodyError, match="aggregate_bytes"):
        _read_members(Path("/tmp/body-source"), members)
