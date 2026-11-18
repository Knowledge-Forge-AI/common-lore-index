"""Optional vector readback preserves ordinary PostgreSQL publications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common_lore_index.storage import (
    apply_migrations,
    default_rdbms_root,
    query_canonical_edge_records,
    query_canonical_node_records,
)
from common_lore_index.storage.semantic_candidate_readback import (
    query_embedding_profiles,
)
from common_lore_index.storage.semantic_candidates import EmbeddingProfilesQuery
from common_lore_index.storage.semantic_seed import SimilarNodesQuery
from common_lore_index.storage.semantic_seed_readback import query_similar_nodes
from repomap_test_support.postgres_harness import (
    require_postgres_binaries,
    temporary_postgres,
)
from repomap_test_support.semantic_candidate_fixtures import PROFILE
from repomap_test_support.semantic_seed_publication import (
    SEED_KEY,
    publish_portable_seed_graph,
)


GRAPH_ID = "seed-graph"
REPOSITORY_IDENTITY = f"repo1:{GRAPH_ID}"
REBUILD_ID = "rebuild-seed-20261008-01"


def _canonical_state(postgres: Any) -> tuple[object, object]:
    """Read the accepted graph through the public canonical readback APIs."""
    return (
        query_canonical_node_records(
            postgres.psql_args,
            root_path=f"graph:{GRAPH_ID}",
            repository_identity=REPOSITORY_IDENTITY,
            psql_command=postgres.psql_command,
        ),
        query_canonical_edge_records(
            postgres.psql_args,
            root_path=f"graph:{GRAPH_ID}",
            repository_identity=REPOSITORY_IDENTITY,
            psql_command=postgres.psql_command,
        ),
    )


def test_core_public_reads_refuse_absent_extension_without_graph_mutation(
    tmp_path: Path,
) -> None:
    """Profile and seed readers remain bounded when pgvector is not installed."""
    require_postgres_binaries()
    with temporary_postgres() as postgres:
        apply_migrations(
            default_rdbms_root(),
            postgres.psql_args,
            psql_command=postgres.psql_command,
        )
        context = publish_portable_seed_graph(
            postgres,
            tmp_path / "publication",
        )
        before_nodes, before_edges = _canonical_state(postgres)
        assert before_nodes
        assert postgres.psql_scalar(
            "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
        ) == "0"

        profiles = query_embedding_profiles(
            postgres.psql_args,
            query=EmbeddingProfilesQuery(selected=True),
            repository_identity=context.repository_identity,
            psql_command=postgres.psql_command,
        )
        assert (profiles.status, profiles.reason, profiles.profiles) == (
            "capability_unavailable",
            "extension_absent",
            (),
        )

        disabled = query_similar_nodes(
            postgres.psql_args,
            query=SimilarNodesQuery(
                SEED_KEY,
                REBUILD_ID,
                PROFILE.identity,
                PROFILE.dimensions,
                selected=False,
            ),
            repository_identity=context.repository_identity,
            psql_command=postgres.psql_command,
        )
        assert (disabled.status, disabled.reason, disabled.candidates) == (
            "capability_unavailable",
            "disabled",
            (),
        )

        absent = query_similar_nodes(
            postgres.psql_args,
            query=SimilarNodesQuery(
                SEED_KEY,
                REBUILD_ID,
                PROFILE.identity,
                PROFILE.dimensions,
                selected=True,
            ),
            repository_identity=context.repository_identity,
            psql_command=postgres.psql_command,
        )
        assert (absent.status, absent.reason, absent.candidates) == (
            "capability_unavailable",
            "extension_absent",
            (),
        )

        after_nodes, after_edges = _canonical_state(postgres)
        assert after_nodes == before_nodes
        assert after_edges == before_edges
