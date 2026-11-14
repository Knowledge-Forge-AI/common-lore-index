"""Fresh maintained evaluator membership plus explicit supplement source owners."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from repomap_test_support.w5_experiment_support import experiment_source_closure
from retrieval_public_text_sources import evaluator_closure
from retrieval_supplement_files import read_member
from retrieval_supplement_values import digest_bytes, digest_json

SUPPLEMENT_SOURCE_MEMBERS = tuple(sorted(
    [f"tools/retrieval_supplement_{name}.py" for name in (
        "values", "tasks", "catalog", "files", "corpus", "pin", "freeze", "applicability",
        "preparation", "baselines", "operation_values", "operation", "declaration", "capture", "report",
    )]
    + [f"src/test/support/python/repomap_test_support/retrieval_supplement_{name}.py" for name in (
        "fixtures", "publication_fixture", "report_fixture", "source", "prepare",
    )]
    + [f"src/test/unit/python/tools/retrieval_supplement_{name}.unit.test.py" for name in (
        "catalog", "freeze", "applicability", "preparation", "operation", "declaration", "report",
    )]
    + [f"src/test/int/python/tools/retrieval_supplement_{name}.int.test.py" for name in (
        "files", "publication", "evidence",
    )]
    + ["docs/adr/2026/11/0198-prospective-public-calibration-supplement.md",
       "docs/specs/cloe-w5-calibration-supplement-v1.md"]
))


@dataclass(frozen=True)
class SupplementSourceSnapshot:
    files: Mapping[str, bytes]
    builder_members: tuple[str, ...]
    explicit_members: tuple[str, ...]

    def manifest(self) -> dict[str, dict[str, object]]:
        return {path: {"bytes": len(raw), "sha256": digest_bytes(raw)} for path, raw in sorted(self.files.items())}

    @property
    def identity(self) -> str:
        return digest_json(self.manifest())


def supplement_source_snapshot(repo_root: Path) -> SupplementSourceSnapshot:
    """Rerun maintained builders on every invocation; cache no member selection."""
    if not repo_root.is_absolute() or repo_root.is_symlink() or repo_root.resolve() != repo_root:
        raise ValueError("supplement_source_root")
    base, _digests = experiment_source_closure(repo_root)
    maintained = evaluator_closure(repo_root, base)
    builder = tuple(sorted(maintained.files))
    if len(SUPPLEMENT_SOURCE_MEMBERS) != len(set(SUPPLEMENT_SOURCE_MEMBERS)):
        raise ValueError("supplement_duplicate_explicit_source")
    files = dict(maintained.files)
    for relative in SUPPLEMENT_SOURCE_MEMBERS:
        raw = read_member(repo_root, relative, max_bytes=4 * 1024 * 1024)
        if relative in files and files[relative] != raw:
            raise ValueError("supplement_source_overlap_drift")
        files[relative] = raw
    for relative, provided in files.items():
        if "fixtures" in Path(relative).parts:
            raise ValueError("supplement_fixture_in_source_closure")
        if read_member(repo_root, relative, max_bytes=4 * 1024 * 1024) != provided:
            raise ValueError("supplement_source_readback_drift")
    return SupplementSourceSnapshot(MappingProxyType(dict(sorted(files.items()))), builder, SUPPLEMENT_SOURCE_MEMBERS)
