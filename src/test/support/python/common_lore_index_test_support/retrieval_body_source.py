"""Fresh, bounded source closure for the W5 body-class preparation slice.

This module binds source bytes and relative names only.  It never imports or
executes a candidate member, and its returned records contain no absolute
paths, host identifiers, credentials, or runtime observations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval_public_text_sources import evaluator_closure
from retrieval_supplement_files import read_member
from retrieval_supplement_primitives import digest_bytes, digest_json, require, safe_relative_path
from common_lore_index_test_support.w5_experiment_support import experiment_source_closure

MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_AGGREGATE_BYTES = 32 * 1024 * 1024

# These are public source owners for the body-class implementation and its
# proportional proof.  Fixture bytes are intentionally excluded.
BODY_SOURCE_OWNERS: tuple[str, ...] = tuple(sorted({
    "tools/retrieval_body_values.py",
    "tools/retrieval_body_catalog.py",
    "tools/retrieval_body_corpus.py",
    "tools/retrieval_body_input_records.py",
    "tools/retrieval_body_tasks.py",
    "tools/retrieval_body_predecessors.py",
    "tools/retrieval_body_input_metadata.py",
    "tools/retrieval_body_witness_records.py",
    "tools/retrieval_body_witnesses.py",
    "tools/retrieval_body_capabilities.py",
    "tools/retrieval_body_pin.py",
    "tools/retrieval_body_revision.py",
    "tools/ci/cloe-w5-body1-input-revision-v2.json",
    "tools/retrieval_body_admission_rules.py",
    "tools/retrieval_body_admission.py",
    "tools/retrieval_body_records.py",
    "tools/retrieval_body_record_types.py",
    "tools/retrieval_body_estimate.py",
    "tools/retrieval_body_report.py",
    "tools/retrieval_body_freeze.py",
    "tools/retrieval_body_check.py",
    "src/test/support/python/common_lore_index_test_support/retrieval_body_publication_fixture.py",
    "src/test/support/python/common_lore_index_test_support/retrieval_body_fixtures.py",
    "src/test/support/python/common_lore_index_test_support/retrieval_body_pin_fixtures.py",
    "src/test/support/python/common_lore_index_test_support/retrieval_body_admission_fixtures.py",
    "src/test/unit/python/tools/retrieval_body_catalog.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_corpus.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_witnesses.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_pin.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_revision.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_input_records.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_admission.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_preparation.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_report.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_freeze.unit.test.py",
    "src/test/unit/python/tools/retrieval_body_check.unit.test.py",
    "src/test/int/python/tools/retrieval_body_capture.int.test.py",
    "src/test/int/python/tools/retrieval_body_publication.int.test.py",
    "src/test/int/python/tools/retrieval_body_files.int.test.py",
    "docs/specs/cloe-w5-captured-text-foundation-v1.md",
    "docs/specs/cloe-w5-representative-workload-decision-v1.md",
    "docs/adr/2026/11/0205-proposed-w5-representative-workload-disposition.md",
    "src/test/support/python/common_lore_index_test_support/retrieval_body_source.py",
}))


@dataclass(frozen=True)
class BodySourceSnapshot:
    """Hash-bound source closure; raw bytes are retained only for re-read checks."""

    builder_members: tuple[str, ...]
    explicit_members: tuple[str, ...]
    files: Mapping[str, bytes]
    digests: Mapping[str, str]
    identity: str
    total_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cloe-body-source-closure-v1",
            "builder_members": list(self.builder_members),
            "explicit_members": list(self.explicit_members),
            "files": {
                name: {"bytes": len(self.files[name]), "sha256": self.digests[name]}
                for name in sorted(self.files)
            },
            "member_count": len(self.files),
            "total_bytes": self.total_bytes,
            "identity": self.identity,
        }


def _root(repo_root: Path) -> Path:
    require(isinstance(repo_root, Path) and repo_root.is_absolute(), "body.source:root")
    require(not repo_root.is_symlink() and repo_root.resolve() == repo_root, "body.source:root_custody")
    require((repo_root / "tools").is_dir(), "body.source:tools_root")
    return repo_root


def _relative_members(values: Sequence[str], label: str) -> tuple[str, ...]:
    require(not isinstance(values, (str, bytes)), f"body.source:{label}_sequence")
    members = tuple(sorted({safe_relative_path(value) for value in values}))
    require(len(members) == len(values), f"body.source:{label}_duplicate")
    require(len(members) <= MAX_MEMBERS, f"body.source:{label}_count")
    require(all("fixtures" not in Path(value).parts for value in members),
            f"body.source:{label}_fixtures")
    return members


def _read_members(repo_root: Path, members: Sequence[str]) -> tuple[dict[str, bytes], int]:
    files: dict[str, bytes] = {}
    total = 0
    for relative in _relative_members(members, "members"):
        raw = read_member(repo_root, relative, max_bytes=MAX_MEMBER_BYTES)
        total += len(raw)
        require(total <= MAX_AGGREGATE_BYTES, "body.source:aggregate_bytes")
        files[relative] = raw
    return files, total


def body_source_snapshot(repo_root: Path) -> BodySourceSnapshot:
    """Rerun maintained builders and fresh-read the complete body closure."""
    root = _root(repo_root)
    base, _base_digests = experiment_source_closure(root)
    maintained = evaluator_closure(root, base)
    builder_members = _relative_members(tuple(maintained.files), "builder")
    explicit_members = _relative_members(BODY_SOURCE_OWNERS, "explicit")
    overlap = set(builder_members) & set(explicit_members)
    require(not overlap, "body.source:builder_explicit_overlap")
    files, total = _read_members(root, (*builder_members, *explicit_members))
    # The maintained builders supplied bytes, but every byte is freshly read
    # through the directory-fd reader before it enters this snapshot.
    for name, supplied in maintained.files.items():
        require(files.get(name) == supplied, "body.source:builder_drift")
    digests = {name: digest_bytes(raw) for name, raw in sorted(files.items())}
    identity = digest_json({
        "schema": "cloe-body-source-closure-v1",
        "builder_members": list(builder_members),
        "explicit_members": list(explicit_members),
        "files": {name: {"bytes": len(files[name]), "sha256": digests[name]}
                  for name in sorted(files)},
    })
    return BodySourceSnapshot(builder_members, explicit_members, files, digests, identity, total)


def verify_body_source_snapshot(repo_root: Path, expected: BodySourceSnapshot) -> None:
    """Rebuild and compare a source snapshot; source drift refuses."""
    fresh = body_source_snapshot(repo_root)
    require(fresh.to_dict() == expected.to_dict(), "body.source:snapshot_drift")


__all__ = ["BODY_SOURCE_OWNERS", "BodySourceSnapshot", "body_source_snapshot",
           "verify_body_source_snapshot"]
