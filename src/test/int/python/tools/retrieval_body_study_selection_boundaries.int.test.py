"""Real SQLite and PostgreSQL source-selection revalidation."""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast

import pytest

from common_lore_index.retrieval.context_assembly import (
    ContextReadPort,
    build_compact_context,
    select_context_sources,
)
from common_lore_index.retrieval.context_contracts import ContextRequest
from repomap_test_support.context_assertions import (
    assert_valid_context_packet,
    build_postgres_context_fixture,
)
from repomap_test_support.context_fixture import (
    BINDING_CORE,
    CTX_GRAPH_ID,
    build_sqlite_context_fixture,
)
from repomap_test_support.postgres_harness import (
    require_postgres_binaries,
    temporary_postgres,
)


_MUTATIONS = ("remove", "revision", "policy", "definition")


def _assert_live_map_revalidation(
    adapter: ContextReadPort,
    selection_path: Path,
    mutation: str,
) -> None:
    """Persist once, then mutate only the adapter's live configured map."""
    record = select_context_sources(
        port=adapter,
        bindings=(BINDING_CORE,),
        path_prefixes={BINDING_CORE: ("src",)},
        pin_publication=True,
        output_path=selection_path,
    )
    assert record.bindings[0].binding_id == BINDING_CORE
    assert selection_path.exists()
    assert selection_path.stat().st_mode & 0o777 == 0o600

    configured = cast(
        MutableMapping[str, MutableMapping[str, Any]],
        adapter.configured_bindings_by_id(),
    )
    assert BINDING_CORE in configured
    if mutation == "remove":
        configured.pop(BINDING_CORE)
        expected_reason = "selection_binding_missing"
    else:
        live_binding = configured[BINDING_CORE]
        if mutation == "revision":
            live_binding["revision"] = int(live_binding["revision"]) + 1
        elif mutation == "policy":
            live_binding["selection_policy_id"] = "select1:changed-for-study"
        elif mutation == "definition":
            live_binding["source_definition_id"] = "src1:changed-for-study"
        else:
            raise AssertionError(f"unknown mutation: {mutation}")
        expected_reason = "selection_binding_changed"

    packet = build_compact_context(
        ContextRequest(
            graph_id=CTX_GRAPH_ID,
            mode="ranked_discovery",
            query="InvoiceService",
            intent="definition",
            selection_path=selection_path,
        ),
        adapter,
    )
    assert_valid_context_packet(packet)
    assert packet["status"] == "refused"
    assert packet["reason"] == expected_reason
    assert packet["scope"]["selection_source"] == "selection_file"
    assert packet["scope"]["selected_binding_ids"] == [BINDING_CORE]
    assert packet["items"] == packet["neighbors"] == packet["relations"] == []


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
@pytest.mark.parametrize("mutation", _MUTATIONS)
def test_selection_file_revalidates_live_binding_map(
    tmp_path: Path,
    backend: str,
    mutation: str,
) -> None:
    """A persisted selection refuses missing or changed live binding identity."""
    if backend == "sqlite":
        _db, _config, _graph, _binding, adapter = build_sqlite_context_fixture(
            tmp_path / "sqlite"
        )
        with adapter:
            _assert_live_map_revalidation(
                adapter, tmp_path / "sqlite-selection.json", mutation
            )
        return

    require_postgres_binaries()
    with temporary_postgres() as postgres:
        _home, adapter = build_postgres_context_fixture(
            tmp_path / "postgres", postgres
        )
        with adapter:
            _assert_live_map_revalidation(
                adapter, tmp_path / "postgres-selection.json", mutation
            )
