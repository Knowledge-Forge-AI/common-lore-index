"""Scope and group a complete bounded candidate scan after public projection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from common_lore_index.server._ops_sanitization import _public_location, _relative_path, sanitize_jsonable
from common_lore_index.storage.navigation_search import DOC_KINDS, HINT_LIMIT, NavigationSearchQuery


def _withheld(value: Any) -> bool:
    return isinstance(value, str) and any(marker in value for marker in (
        "[REDACTED]", "[private-path]", "[private-root]", "...[truncated]",
    ))


def _destination(location: dict[str, Any], markers: tuple[str, ...]) -> dict[str, Any] | None:
    source = location["source"]
    path = _relative_path(source.get("source_relative_path"), markers)
    if (location.get("source_identity_status") != "verified_vector" or not location["path"] or not path
            or not isinstance(source.get("binding_id"), str)
            or not isinstance(source.get("snapshot_id"), str)
            or type(source.get("binding_revision")) is not int
            or any(_withheld(value) for value in (*source.values(), location["path"]))):
        return None
    return {"source_binding_id": source["binding_id"], "binding_revision": source["binding_revision"],
            "snapshot_id": source["snapshot_id"], "source_relative_path": path,
            "run_id": location["run_id"], "publication_relation": location["publication_relation"],
            "path_namespace": "source_relative", "graph_path": location["path"]}


def project_navigation_member(
    node: dict[str, Any], markers: tuple[str, ...]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    locations = [_public_navigation_location(row, markers) for row in node["locations"]]
    result = sanitize_jsonable({key: value for key, value in node.items() if key != "locations"},
                               private_markers=markers)
    result.update(source_hints=[_hint(row) for row in locations[:HINT_LIMIT]],
                  source_hints_has_more=len(locations) > HINT_LIMIT,
                  source_hint_count=len(locations), matched=True,
                  identity_status="available" if result["canonical_key"] == node["canonical_key"]
                  and not _withheld(result["canonical_key"]) else "withheld")
    return result, locations


_member = project_navigation_member


def _public_navigation_location(row: dict[str, Any], markers: tuple[str, ...]) -> dict[str, Any]:
    public = _public_location(row, markers)
    # Redaction must never manufacture an identity shared by unrelated subjects.
    identity_fields = ("binding_id", "binding_revision", "snapshot_id", "source_relative_path")
    if any(row["source"].get(key) != public["source"].get(key) for key in identity_fields):
        public["source_identity_status"] = "withheld"
    return public


def location_in_scope(row: dict[str, Any], query: NavigationSearchQuery,
                      markers: tuple[str, ...]) -> bool:
    """Same definitive public admission for SQL predicates and presentation."""
    return _in_scope([_public_navigation_location(row, markers)], query, markers)


def source_scope_admission(query: NavigationSearchQuery, markers: tuple[str, ...]) -> Callable[[dict[str, Any]], bool]:
    """Bind public policy once per read, outside SQLite's predicate evaluations."""
    return lambda row: location_in_scope(row, query, markers)


def _hint(row: dict[str, Any]) -> dict[str, Any]:
    """Keep navigation facts after sanitization; full evidence uses the locator."""
    result = {key: row[key] for key in (
        "path", "path_namespace", "path_status", "start_line", "end_line", "range_status",
        "run_id", "publication_relation", "evidence_role", "source_identity_status", "source",
    )}
    result["raw_observation"] = {key: row["raw_observation"][key]
                                 for key in ("status", "ordinal", "kind")}
    return result


def _matches_source(
    destination: dict[str, Any] | None, binding: str | None, prefix: str | None,
) -> bool:
    if destination is None or destination["source_binding_id"] != binding:
        return False
    path = destination["source_relative_path"]
    return prefix is None or path == prefix or path.startswith(prefix + "/")


def _in_scope(locations: list[dict[str, Any]], query: NavigationSearchQuery,
              markers: tuple[str, ...]) -> bool:
    if query.source_binding_id is None:
        return True
    prefix = None if query.source_path_prefix is None else query.source_path_prefix.rstrip("/")
    return any(_matches_source(_destination(row, markers), query.source_binding_id, prefix) for row in locations)


def _scope_and_identity(
    locations: list[dict[str, Any]], query: NavigationSearchQuery, markers: tuple[str, ...],
) -> tuple[bool, dict[str, Any] | None, str | None]:
    included = query.source_binding_id is None
    if not locations:
        return included, None, "no_direct_evidence"
    prefix = None if query.source_path_prefix is None else query.source_path_prefix.rstrip("/")
    first = None
    conflict = missing = False
    for row in locations:
        destination = _destination(row, markers)
        if not included:
            included = _matches_source(destination, query.source_binding_id, prefix)
        if destination is None:
            missing = True
        elif first is None:
            first = destination
        elif destination != first:
            conflict = True
    if missing:
        return included, None, "missing_withheld_or_unbound_identity"
    if conflict:
        return included, None, "conflicting_direct_identities"
    return included, first, None


def group_page(scan: dict[str, Any], query: NavigationSearchQuery,
               markers: tuple[str, ...]) -> dict[str, Any]:
    if scan["status"] != "ok":
        return {"results": [], "result_count": 0, "has_more": scan["status"] == "incomplete",
                "page_end": query.offset, "scope_excluded_count": None}
    documents: dict[tuple[Any, ...], dict[str, Any] | None] = {}
    page: list[dict[str, Any]] = []
    page_limit = query.offset + query.limit
    total = excluded = 0
    for node in scan["nodes"]:
        member, locations = _member(node, markers)
        included, destination, reason = _scope_and_identity(locations, query, markers)
        if not included:
            excluded += 1
            continue
        if member["identity_status"] == "withheld":
            reason = "canonical_identity_withheld"
        is_document = node["kind"] in DOC_KINDS
        grouped = (query.grouping == "source_document" and is_document and destination is not None
                   and member["identity_status"] == "available")
        identity = tuple(destination.values()) if grouped and destination is not None else None
        if identity is not None and identity in documents:
            group = documents[identity]
        else:
            group = ({
                "basis": "direct_source_document" if grouped else "singleton",
                "unresolved_reason": reason, "destination": destination, "members": [], "context": [],
                "matched_member_count": 0, "member_has_more": False,
            } if query.offset <= total < page_limit else None)
            total += 1
            if identity is not None:
                documents[identity] = group
            if group is not None:
                page.append(group)
        if group is not None:
            group["matched_member_count"] += 1
            if len(group["members"]) < query.member_limit:
                group["members"].append(member)
            else:
                group["member_has_more"] = True
    result = {"results": page, "result_count": len(page), "has_more": total > page_limit,
              "page_end": query.offset + len(page), "scope_excluded_count": excluded}
    if "admission" in scan["scan"]:
        result.update(scope_excluded_count=None, scope_excluded_count_basis="not_counted_before_admission",
                      post_admission_scope_excluded_count=excluded)
    return result
