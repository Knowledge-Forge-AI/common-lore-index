"""Optional canonical evidence and node-evidence link joining for projected files (S1)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Any

from common_lore_index.artifacts.bundle import PublicationBundle


@dataclass(frozen=True)
class ProjectedEvidenceLink:
    """Optional evidence link joined from canonical_evidence and canonical_node_evidence."""

    evidence_key: str
    canonical_node_key: str | None
    link_kind: str | None
    evidence_kind: str
    binding_id: str
    source_relative_path: str
    start_line: int | None
    end_line: int | None
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "canonical_node_key": self.canonical_node_key,
            "link_kind": self.link_kind,
            "evidence_kind": self.evidence_kind,
            "binding_id": self.binding_id,
            "source_relative_path": self.source_relative_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "confidence": self.confidence,
        }


def _evidence_file_key(row: Mapping[str, Any]) -> tuple[str, str] | None:
    metadata = row.get("metadata_json")
    if not isinstance(metadata, Mapping):
        return None
    binding, path = metadata.get("binding_id"), metadata.get("source_relative_path")
    if isinstance(binding, str) and binding and isinstance(path, str) and path:
        return binding, path
    return None


def index_bundle_evidence(
    bundle: PublicationBundle,
    *,
    file_keys: Set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], tuple[ProjectedEvidenceLink, ...]]:
    """Index same-bundle evidence records by (binding_id, source_relative_path).

    Joins ``canonical_node_evidence`` (`canonical_key`, `evidence_key`,
    `link_kind`) with ``canonical_evidence`` (`evidence_key`, `evidence_kind`,
    `start_line`, `end_line`, `confidence`, `metadata_json`). Never manufactures
    links when `binding_id` or `source_relative_path` is absent or ambiguous.
    """
    if file_keys is not None and not file_keys:
        return {}
    evidence_rows = bundle.families.get("canonical_evidence", ())
    selected_ids: set[str] | None = None
    if file_keys is not None:
        evidence_rows = tuple(row for row in evidence_rows if _evidence_file_key(row) in file_keys)
        selected_ids = {
            key for row in evidence_rows if isinstance(key := row.get("evidence_key"), str)
        }

    node_links_by_evidence: dict[str, list[tuple[str, str]]] = {}
    for row in bundle.families.get("canonical_node_evidence", ()):
        ev_key = row.get("evidence_key")
        node_key = row.get("canonical_key")
        link_kind = row.get("link_kind")
        if (
            isinstance(ev_key, str)
            and ev_key
            and (selected_ids is None or ev_key in selected_ids)
            and isinstance(node_key, str)
            and node_key
            and isinstance(link_kind, str)
            and link_kind
        ):
            node_links_by_evidence.setdefault(ev_key, []).append((node_key, link_kind))

    by_file: dict[tuple[str, str], list[ProjectedEvidenceLink]] = {}
    for row in evidence_rows:
        ev_key = row.get("evidence_key")
        ev_kind = row.get("raw_kind")
        confidence = row.get("confidence", "extracted")
        if not isinstance(ev_key, str) or not ev_key or not isinstance(ev_kind, str) or not ev_kind:
            continue
        file_key = _evidence_file_key(row)
        if file_key is None:
            continue
        binding_id, rel_path = file_key

        raw_start = row.get("start_line")
        raw_end = row.get("end_line")
        start_line = int(raw_start) if isinstance(raw_start, int) and not isinstance(raw_start, bool) else None
        end_line = int(raw_end) if isinstance(raw_end, int) and not isinstance(raw_end, bool) else None

        joined = node_links_by_evidence.get(ev_key)
        target_list = by_file.setdefault((binding_id, rel_path), [])
        if not joined:
            target_list.append(
                ProjectedEvidenceLink(
                    evidence_key=ev_key,
                    canonical_node_key=None,
                    link_kind=None,
                    evidence_kind=ev_kind,
                    binding_id=binding_id,
                    source_relative_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    confidence=str(confidence),
                )
            )
        else:
            for node_key, link_kind in sorted(joined):
                target_list.append(
                    ProjectedEvidenceLink(
                        evidence_key=ev_key,
                        canonical_node_key=node_key,
                        link_kind=link_kind,
                        evidence_kind=ev_kind,
                        binding_id=binding_id,
                        source_relative_path=rel_path,
                        start_line=start_line,
                        end_line=end_line,
                        confidence=str(confidence),
                    )
                )

    return {
        key: tuple(
            sorted(
                links,
                key=lambda item: (
                    item.start_line or 0,
                    item.end_line or 0,
                    item.evidence_key,
                    item.canonical_node_key or "",
                    item.link_kind or "",
                ),
            )
        )
        for key, links in sorted(by_file.items())
    }


def _iter_links_for_line_span(
    file_links: Iterable[ProjectedEvidenceLink],
    start_line: int,
    end_line: int,
) -> Iterator[ProjectedEvidenceLink]:
    for link in file_links:
        if link.start_line is None:
            continue
        link_end = link.end_line if link.end_line is not None else link.start_line
        if link.start_line <= end_line and link_end >= start_line:
            yield link


def links_for_line_span(
    file_links: Sequence[ProjectedEvidenceLink],
    start_line: int,
    end_line: int,
) -> tuple[ProjectedEvidenceLink, ...]:
    """Return evidence links whose line span overlaps [start_line, end_line]."""
    return tuple(_iter_links_for_line_span(file_links, start_line, end_line))


def first_link_for_line_span(
    file_links: Iterable[ProjectedEvidenceLink],
    start_line: int,
    end_line: int,
) -> ProjectedEvidenceLink | None:
    """Return the first overlapping link in the supplied order."""
    return next(_iter_links_for_line_span(file_links, start_line, end_line), None)
