"""Ops, search, and summary tools for the RepoMap MCP server."""

from __future__ import annotations

from typing import Any

from common_lore_index.server.mcp_core import (
    RepoMapMcpError,
    public_storage_error_message,
)
from common_lore_index.server.memory_bridge import (
    server_memory_search_payload,
    server_memory_summary_payload,
)
from common_lore_index.server.ops import (
    McpOpsError,
    graph_status_payload,
    list_graphs_payload,
    neighborhood_payload,
    project_summary_payload,
    refresh_status_payload,
    search_payload,
    summary_payload,
)
from common_lore_index.storage import StorageSchemaError


def ops_payload(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except McpOpsError as error:
        raise RepoMapMcpError(str(error)) from error
    except StorageSchemaError as error:
        raise RepoMapMcpError(public_storage_error_message(error)) from error
    except ValueError as error:
        raise RepoMapMcpError(str(error)) from error


def cloe_list_graphs() -> dict[str, Any]:
    return ops_payload(list_graphs_payload)


def cloe_graph_status(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(graph_status_payload, graph_id)


def cloe_search_nodes(
    *,
    graph_id: str,
    query: str,
    kind: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return ops_payload(
        search_payload,
        graph_id,
        target="nodes",
        query=query,
        kind=kind,
        limit=limit,
        offset=offset,
    )


def cloe_search_observations(
    *,
    graph_id: str,
    query: str,
    kind: str | None = None,
    path: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_raw: bool = False,
) -> dict[str, Any]:
    return ops_payload(
        search_payload,
        graph_id,
        target="observations",
        query=query,
        kind=kind,
        path=path,
        limit=limit,
        offset=offset,
        include_raw=include_raw,
    )


def cloe_search_files(
    *,
    graph_id: str,
    query: str,
    path: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return ops_payload(
        search_payload,
        graph_id,
        target="files",
        query=query,
        path=path,
        limit=limit,
        offset=offset,
    )


def cloe_neighborhood(
    *,
    graph_id: str,
    node: str,
    direction: str = "both",
    depth: int = 1,
    node_limit: Any = ...,
    edge_limit: Any = ...,
) -> dict[str, Any]:
    return ops_payload(
        neighborhood_payload,
        graph_id,
        node=node,
        direction=direction,
        depth=depth,
        node_limit=node_limit,
        edge_limit=edge_limit,
    )


def cloe_project_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(project_summary_payload, graph_id)


def cloe_python_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(summary_payload, graph_id, summary_kind="python")


def cloe_terraform_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(summary_payload, graph_id, summary_kind="terraform")


def cloe_openapi_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(summary_payload, graph_id, summary_kind="openapi")


def cloe_js_framework_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(summary_payload, graph_id, summary_kind="js_framework")


def cloe_nix_summary(*, graph_id: str) -> dict[str, Any]:
    return ops_payload(summary_payload, graph_id, summary_kind="nix")


def cloe_refresh_status(graph_id: str | None = None) -> dict[str, Any]:
    return ops_payload(refresh_status_payload, graph_id=graph_id)


def cloe_server_memory_summary() -> dict[str, Any]:
    return ops_payload(server_memory_summary_payload)


def cloe_server_memory_search(
    *,
    query: str,
    kind: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return ops_payload(
        server_memory_search_payload,
        query=query,
        kind=kind,
        limit=limit,
        offset=offset,
    )
