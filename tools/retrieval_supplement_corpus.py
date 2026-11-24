"""Strict ``cloe-supplement-corpus-v1`` bundle parsing and data-only materialization.

Corpus sources are inert data: they are validated, hashed and written as bytes into a
new owned directory. Nothing here imports, compiles or runs target code or test
sources, and no extra input files are discovered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_files import (
    file_inventory,
    publish_member,
    read_member,
    require_empty_owned_dir,
)
from retrieval_supplement_values import (
    CORPUS_SCHEMA,
    PURPOSE,
    SupplementIdentity,
    check_keys,
    digest_bytes,
    fail,
    mapping_copy,
    require,
    safe_id,
    safe_relative_path,
    strict_json,
)

ROOT_FIELDS: tuple[str, ...] = ("schema", "corpus_id", "phase", "purpose", "files")
FILE_FIELDS: tuple[str, ...] = ("binding_alias", "source_relative_path", "role", "content_utf8")
ROLES = frozenset({"code", "test_source", "design", "denied"})
MAX_FILES = 24
MAX_FILE_BYTES = 4096


@dataclass(frozen=True)
class SupplementFile:
    """One corpus member; ``content`` is the exact UTF-8 encoding of ``content_utf8``."""

    alias: str
    binding_id: str
    source_relative_path: str
    role: str
    content: bytes

    @property
    def member_path(self) -> str:
        """Materialized ``alias/source_relative_path`` (the alias is never in the source path)."""
        return f"{self.alias}/{self.source_relative_path}"

    @property
    def sha256(self) -> str:
        return digest_bytes(self.content)


class SupplementCorpus:
    """Validated corpus bundle with defensive accessors."""

    __slots__ = ("_bindings", "_files", "_identity", "_raw")
    _bindings: tuple[tuple[str, str], ...]
    _files: tuple[SupplementFile, ...]
    _identity: SupplementIdentity
    _raw: bytes

    def __init__(
        self,
        raw: bytes,
        identity: SupplementIdentity,
        bindings: Mapping[str, str],
        files: tuple[SupplementFile, ...],
    ) -> None:
        object.__setattr__(self, "_raw", bytes(raw))
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_bindings", tuple(sorted(bindings.items())))
        object.__setattr__(self, "_files", tuple(files))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SupplementCorpus is immutable")

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def identity(self) -> SupplementIdentity:
        return self._identity

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    @property
    def bindings(self) -> dict[str, str]:
        """Fresh alias -> full binding ID mapping."""
        return dict(self._bindings)

    @property
    def files(self) -> tuple[SupplementFile, ...]:
        return tuple(self._files)

    @property
    def denied_binding_ids(self) -> frozenset[str]:
        return frozenset(f.binding_id for f in self._files if f.role == "denied")

    def file_keys(self) -> frozenset[tuple[str, str]]:
        """``(binding_id, source_relative_path)`` identities of every member."""
        return frozenset((f.binding_id, f.source_relative_path) for f in self._files)

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def _role_path_rules(role: str, path: str) -> None:
    pure = PurePosixPath(path)
    require(pure.suffix in {".py", ".md"}, "corpus.file:extension_unsupported")
    if role == "design":
        require(pure.suffix == ".md", "corpus.file:design_requires_markdown")
        return
    require(pure.suffix == ".py", f"corpus.file:{role}_requires_python")
    is_test_path = (
        len(pure.parts) == 2 and pure.parts[0] == "tests" and pure.name.startswith("test_")
    )
    if role == "test_source":
        require(is_test_path, "corpus.file:test_source_path")
    else:
        require(not is_test_path, "corpus.file:test_path_requires_test_source_role")


def _file(record: object, bindings: Mapping[str, str]) -> SupplementFile:
    item = check_keys(record, FILE_FIELDS, "corpus.file")
    alias = safe_id(item["binding_alias"], "corpus.file.binding_alias")
    require(alias in bindings, "corpus.file.binding_alias:unknown")
    path = safe_relative_path(item["source_relative_path"])
    require(path.split("/")[0] != alias, "corpus.file:alias_prefixed_path")
    role = item["role"]
    require(type(role) is str and role in ROLES, "corpus.file.role:unsupported")
    _role_path_rules(role, path)
    content_text = item["content_utf8"]
    require(type(content_text) is str, "corpus.file.content_utf8:string_required")
    require("\x00" not in content_text, "corpus.file.content_utf8:nul")
    try:
        content = content_text.encode("utf-8")
    except UnicodeEncodeError:
        fail("corpus.file.content_utf8:invalid_unicode")
    require(len(content) <= MAX_FILE_BYTES, "corpus.file.content_utf8:too_large")
    return SupplementFile(alias, bindings[alias], path, role, content)


def parse_corpus(
    raw: bytes, *, identity: SupplementIdentity, bindings: Mapping[str, str]
) -> SupplementCorpus:
    """Parse corpus bytes against explicit alias -> full binding ID ``bindings``."""
    require(isinstance(identity, SupplementIdentity), "identity:required")
    known = mapping_copy(bindings, "bindings")
    require(known, "bindings:empty")
    for alias, binding_id in known.items():
        safe_id(alias, "bindings.alias")
        require(binding_id.strip() != "", "bindings.binding_id:empty")
    require(len(set(known.values())) == len(known), "bindings:duplicate_binding_id")
    root = check_keys(strict_json(raw), ROOT_FIELDS, "corpus")
    require(root["schema"] == CORPUS_SCHEMA, "corpus.schema:unsupported")
    require(root["corpus_id"] == identity.corpus_id, "corpus.corpus_id:mismatch")
    require(root["phase"] == identity.phase, "corpus.phase:mismatch")
    require(root["purpose"] == PURPOSE, "corpus.purpose:unsupported")
    entries = root["files"]
    require(type(entries) is list and 1 <= len(entries) <= MAX_FILES, "corpus.files:bad_length")
    files = tuple(_file(entry, known) for entry in entries)
    keys = [(f.alias, f.source_relative_path) for f in files]
    require(len(set(keys)) == len(keys), "corpus.files:duplicate_member")
    denied_aliases = {f.alias for f in files if f.role == "denied"}
    for member in files:
        require(
            (member.role == "denied") == (member.alias in denied_aliases),
            "corpus.files:denied_alias_mixed",
        )
    return SupplementCorpus(raw, identity, known, files)


def check_catalog_corpus(catalog: SupplementCatalog, corpus: SupplementCorpus) -> None:
    """Cross-check identities, gold membership and denied-binding separation."""
    require(catalog.identity == corpus.identity, "corpus:identity_mismatch")
    roles = {(f.binding_id, f.source_relative_path): f.role for f in corpus.files}
    denied = corpus.denied_binding_ids
    binding_ids = set(corpus.bindings.values())
    for case in catalog.cases:
        task = case.task
        selected = set(task.selected_bindings)
        require(selected <= binding_ids, "corpus:task_binding_unknown")
        keys = [gold.locator.file_identity_key() for gold in task.gold_targets]
        require(all(key in roles for key in keys), "corpus:gold_file_missing")
        require(all(key[0] not in denied for key in keys), "corpus:gold_in_denied_binding")
        if case.kind == "privacy":
            require(selected & denied, "corpus:privacy_requires_denied_binding")
        else:
            require(not (selected & denied), "corpus:denied_binding_selected")
        if case.kind != "positive":
            continue
        gold_roles = {roles[key] for key in keys}
        if task.family == "relevant_tests_for_behavior":
            require("test_source" in gold_roles, "corpus:tests_family_requires_test_gold")
        if task.family == "code_plus_design_or_message_context":
            require("design" in gold_roles, "corpus:design_family_requires_design_gold")


def materialize_corpus(corpus: SupplementCorpus, root: Path) -> dict[str, str]:
    """Write every member as data into a new empty owned ``root``; return path -> digest."""
    require(isinstance(corpus, SupplementCorpus), "corpus:required")
    require_empty_owned_dir(root)
    expected: dict[str, str] = {}
    for member in corpus.files:
        publish_member(root, member.member_path, member.content)
        expected[member.member_path] = member.sha256
    require(file_inventory(root) == tuple(sorted(expected)), "corpus:materialized_inventory_drift")
    for path, digest in expected.items():
        read_member(root, path, max_bytes=MAX_FILE_BYTES, expected_sha256=digest)
    return dict(sorted(expected.items()))


__all__ = [
    "SupplementCorpus", "SupplementFile", "check_catalog_corpus", "materialize_corpus",
    "parse_corpus",
]
