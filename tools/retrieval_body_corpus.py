"""Inert captured-text corpus parsing, role checks and exclusive byte materialization.

This class reuses identity-free file and custody leaves. It does not relabel a
SupplementIdentity, execute fixture source, issue admission, or select a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from retrieval_body_catalog import BodyCatalog
from retrieval_body_values import (
    BodyIdentity, check_keys, digest_bytes, require, safe_id,
    safe_relative_path, strict_json, text,
)
from retrieval_supplement_corpus import SupplementFile
from retrieval_supplement_files import (
    file_inventory, publish_member, read_member, require_empty_owned_dir,
)

CORPUS_FIELDS = ("schema", "corpus_id", "phase", "purpose", "files")
MEMBER_FIELDS = ("binding_alias", "source_relative_path", "role", "content_utf8")
ROLES = frozenset({"code", "test_source", "design", "denied"})
MAX_FILES = 24
MAX_FILE_BYTES = 4096
_TOKEN = object()


class BodyCorpus:
    """Factory-validated immutable source bytes; parsing alone is not admission."""

    __slots__ = ("_raw", "_identity", "_bindings", "_files")
    _raw: bytes
    _identity: BodyIdentity
    _bindings: tuple[tuple[str, str], ...]
    _files: tuple[SupplementFile, ...]

    def __init__(
        self, raw: bytes, identity: BodyIdentity, bindings: Mapping[str, str],
        files: tuple[SupplementFile, ...], *, _token: object = None,
    ) -> None:
        require(_token is _TOKEN, "body.corpus:factory_required")
        for name, value in (("_raw", raw), ("_identity", identity),
                            ("_bindings", tuple(sorted(bindings.items()))), ("_files", files)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BodyCorpus is immutable")

    @property
    def identity(self) -> BodyIdentity:
        return self._identity

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    @property
    def bindings(self) -> dict[str, str]:
        return dict(self._bindings)

    @property
    def files(self) -> tuple[SupplementFile, ...]:
        return self._files

    @property
    def denied_binding_ids(self) -> frozenset[str]:
        return frozenset(member.binding_id for member in self._files if member.role == "denied")

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def _bindings(value: Mapping[str, str]) -> dict[str, str]:
    require(isinstance(value, Mapping) and 1 <= len(value) <= 16, "body.corpus:binding_count")
    result = {}
    for alias, binding in value.items():
        safe_id(alias, "body.corpus.alias")
        result[alias] = text(binding, "body.corpus.binding", 256)
    require(len(set(result.values())) == len(result), "body.corpus:duplicate_binding_id")
    return result


def _member(value: object, bindings: Mapping[str, str]) -> SupplementFile:
    row = check_keys(value, MEMBER_FIELDS, "body.corpus.file")
    alias = safe_id(row["binding_alias"], "body.corpus.file.alias")
    require(alias in bindings, "body.corpus.file:unknown_binding")
    path = safe_relative_path(row["source_relative_path"])
    require(path.split("/")[0] != alias, "body.corpus.file:alias_prefixed_path")
    role = row["role"]
    require(type(role) is str and role in ROLES, "body.corpus.file:role")
    pure = PurePosixPath(path)
    require(pure.suffix == (".md" if role == "design" else ".py"), "body.corpus.file:extension")
    test_path = len(pure.parts) == 2 and pure.parts[0] == "tests" and pure.name.startswith("test_")
    require((role == "test_source") == test_path, "body.corpus.file:test_role_path")
    content = text(row["content_utf8"], "body.corpus.file.content", MAX_FILE_BYTES).encode("utf-8")
    return SupplementFile(alias, bindings[alias], path, role, content)


def parse_body_corpus(
    raw: bytes, *, identity: BodyIdentity, bindings: Mapping[str, str],
) -> BodyCorpus:
    require(type(identity) is BodyIdentity, "body.corpus:identity")
    known = _bindings(bindings)
    root = check_keys(strict_json(raw), CORPUS_FIELDS, "body.corpus")
    require(root["schema"] == "cloe-supplement-corpus-v1", "body.corpus:schema")
    require(root["purpose"] == "calibration", "body.corpus:purpose")
    require(root["corpus_id"] == identity.corpus_id and root["phase"] == identity.phase, "body.corpus:identity_drift")
    rows = root["files"]
    require(type(rows) is list and 1 <= len(rows) <= MAX_FILES, "body.corpus:file_cap")
    members = tuple(_member(row, known) for row in rows)
    keys = {(member.alias, member.source_relative_path) for member in members}
    require(len(keys) == len(members), "body.corpus:duplicate_member")
    require({member.alias for member in members} == set(known), "body.corpus:empty_binding")
    denied = {member.binding_id for member in members if member.role == "denied"}
    require(all((member.binding_id in denied) == (member.role == "denied") for member in members), "body.corpus:mixed_denied_binding")
    return BodyCorpus(raw, identity, known, members, _token=_TOKEN)


def check_body_catalog_corpus(catalog: BodyCatalog, corpus: BodyCorpus) -> None:
    """Require real corpus membership, family roles and denied-binding separation.

    Exact static source/node/fact correspondence is the separate witness gate.
    These checks do not assert tokenization, retrieval or scientific usefulness.
    """
    require(BodyCatalog.is_factory_issued(catalog) and type(corpus) is BodyCorpus, "body.corpus:validated_inputs_required")
    require(catalog.identity == corpus.identity, "body.corpus:catalog_identity")
    roles = {(member.binding_id, member.source_relative_path): member.role for member in corpus.files}
    denied, declared = corpus.denied_binding_ids, set(corpus.bindings.values())
    for case in catalog.cases:
        selected = set(case.task.selected_bindings)
        require(selected <= declared, "body.corpus:task_binding")
        keys = [gold.locator.file_identity_key() for gold in case.task.gold_targets]
        require(all(key in roles for key in keys), "body.corpus:gold_missing")
        require(all(key[0] not in denied for key in keys), "body.corpus:denied_gold")
        require(bool(selected & denied) == (case.kind == "privacy"), "body.corpus:privacy_selection")
        if case.kind != "body_positive":
            continue
        expected_role = "test_source" if case.task.family == "relevant_tests_for_behavior" else "code"
        for witness in case.to_dict()["witnesses"] + [item["witness"] for item in case.to_dict()["distractors"]]:
            key = (witness["binding_id"], witness["source_relative_path"])
            require(key in roles and roles[key] == expected_role, "body.corpus:witness_role_or_membership")


def materialize_body_corpus(corpus: BodyCorpus, root: Path) -> dict[str, str]:
    """Exclusively write inert data into a caller-owned empty root; never execute it."""
    require(type(corpus) is BodyCorpus, "body.corpus:validated_input_required")
    require_empty_owned_dir(root)
    expected = {}
    for member in corpus.files:
        publish_member(root, member.member_path, member.content)
        expected[member.member_path] = member.sha256
    require(file_inventory(root) == tuple(sorted(expected)), "body.corpus:materialized_inventory")
    for path, digest in expected.items():
        read_member(root, path, max_bytes=MAX_FILE_BYTES, expected_sha256=digest)
    return dict(sorted(expected.items()))
