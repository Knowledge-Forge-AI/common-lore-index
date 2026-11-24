"""Owned PostgreSQL configuration and captured-text readbacks for matched W5."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path

from common_lore_index.ops.config_local import local_graph_binding
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_publication import lexical_connection_parameters, resolve_ops_graph
from common_lore_index.ops.lexical_service import _fresh_ops_config, _query_graph_lexical_generation
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.lexical_policy import build_lexical_generation_policy
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.retrieval.query_contracts import QueryEnvelope
from common_lore_index.storage.lexical_driver import (
    DEFAULT_QUERY_STATEMENT_TIMEOUT_MS, LIMITED_LEXICAL_ROLES, LexicalStorageError,
    execute_fetchall, lexical_connection_scope, open_psycopg_lexical_connection,
)
from common_lore_index.storage.lexical_lifecycle import read_accepted_graph_publication
from common_lore_index.storage.lexical_query_sql import DEFAULT_MAX_OCCURRENCES_PER_FILE
from retrieval_body_study_files import read_member
from retrieval_semantic_fusion import FileIdentityTuple
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_primitives import digest_json
from retrieval_w5_matched_inputs import require_match
from retrieval_w5_matched_publications import CaptureReadback, ProjectedFileWitness, full_policy_digest

_CAPTURE_SQL = """
SELECT g.lexical_generation_id, g.run_id, g.publication_bundle_id, g.snapshot_manifest_id,
       g.extraction_receipt_id, g.graph_candidate_id, g.snapshot_vector_json, g.policy_digest,
       g.binding_policy_digests_json, g.admitted_binding_ids_json, g.projected_document_count, g.state,
       d.binding_id, d.snapshot_id, d.source_relative_path, d.file_content_sha256,
       CASE WHEN octet_length(d.normalized_text) <= 4096 THEN d.normalized_text END,
       d.chunk_count, (SELECT count(*) FROM cloe_lexical.chunks c
                       WHERE (c.repository_id, c.lexical_generation_id, c.binding_id, c.source_relative_path)
                           = (d.repository_id, d.lexical_generation_id, d.binding_id, d.source_relative_path))
FROM cloe_lexical.authority a
JOIN cloe_lexical.generations g USING (repository_id, lexical_generation_id)
LEFT JOIN cloe_lexical.documents d USING (repository_id, lexical_generation_id)
WHERE a.repository_id = %s
ORDER BY d.binding_id, d.source_relative_path
LIMIT 25
"""


@contextmanager
def _postgres_io() -> Iterator[None]:
    try:
        yield
    except LexicalStorageError as error:
        if error.code in {"lexical_permission_denied", "lexical_role_denied"}:
            raise ValueError("matched_input:postgres_role_refused") from None
        raise OSError("matched_input:postgres_operation_unavailable") from None


def _config_bytes(config: OpsConfig) -> str:
    home = Path(config.config_home) if config.config_home is not None else Path(config.config_path).parent
    names = config.config_files
    require_match(home.is_absolute() and 0 < len(names) <= 8 and len(set(names)) == len(names),
                  "postgres_config_files")
    identity = []
    for name in names:
        raw = read_member(home, name, max_bytes=256 * 1024)
        identity.append((name, sha256(raw).hexdigest()))
    return digest_json(identity)


@dataclass(frozen=True, slots=True)
class MatchedPostgres:
    """Reuse one reviewed config snapshot for reads, guarding its source before and after use."""

    config: OpsConfig = field(repr=False)
    publication: VerifiedPublicationPin
    evaluator_policy: BodyProjectionPolicy
    read_vector_generation: Callable[[], str | None] = field(repr=False)
    config_digest: str
    _issued_identity: int | None = field(default=None, init=False, repr=False, compare=False)

    def _guard_config(self) -> None:
        require_match(self._issued_identity == id(self) and _config_bytes(self.config) == self.config_digest,
                      "postgres_config_drift")

    def query(self, request: LexicalQueryRequest) -> QueryEnvelope:
        self._guard_config()
        try:
            with _postgres_io():
                envelope, _ = _query_graph_lexical_generation(
                    self.config, self.publication.graph_id, request, authorize_body=True,
                    connection_factory=open_psycopg_lexical_connection, publication_bundle_id=None, snapshot_manifest_id=None,
                    max_occurrences_per_file=DEFAULT_MAX_OCCURRENCES_PER_FILE,
                    statement_timeout_ms=DEFAULT_QUERY_STATEMENT_TIMEOUT_MS, use_bound_config=True,
                )
        finally:
            self._guard_config()
        return envelope

    def __call__(self) -> CaptureReadback:
        self._guard_config()
        pin = self.publication
        graph = resolve_ops_graph(self.config, pin.graph_id)
        lexical_policy = build_lexical_generation_policy(graph)
        store_policy = lexical_policy.body_policy
        require_match(store_policy.selected_binding_ids == pin.admitted_public_bindings
                      and all(self.evaluator_policy.bindings.get(key) == value
                              for key, value in store_policy.bindings.items())
                      and full_policy_digest(self.evaluator_policy) ==
                      full_policy_digest(_unified_policy(store_policy, self.evaluator_policy)), "postgres_policy_scope")
        params = lexical_connection_parameters(self.config, graph, read_only=True)
        require_match(params.user not in LIMITED_LEXICAL_ROLES, "postgres_capture_role")
        with _postgres_io(), lexical_connection_scope(params, read_only=True) as connection:
            accepted = read_accepted_graph_publication(
                connection, repository_identity=local_graph_binding(graph).repository_identity,
                repository_name=graph.repository_name, root_path=f"graph:{graph.id}",
            )
            rows = [] if accepted is None else execute_fetchall(
                connection, _CAPTURE_SQL, (accepted.repository_id,), operation="read_matched_capture",
            )
            connection.rollback()
        self._guard_config()
        require_match(accepted is not None, "postgres_accepted_missing")
        assert accepted is not None
        require_match(
            (accepted.repository_identity, accepted.run_id, accepted.publication_bundle_id, accepted.snapshot_manifest_id,
             accepted.extraction_receipt_id, accepted.graph_candidate_id, accepted.snapshot_vector)
            == (pin.repository_identity, pin.run_id, pin.publication_bundle_id, pin.snapshot_manifest_id,
                pin.extraction_receipt_id, pin.graph_candidate_id, pin.snapshot_vector), "postgres_accepted_drift",
        )
        require_match(bool(rows), "postgres_authority_missing")
        generation = rows[0][0]
        files = []
        for row in rows:
            (gen, run, bundle, manifest, extraction, candidate, vector, policy, binding_digests, admitted, count, state,
             binding, snapshot, path, digest, body, chunks, actual_chunks) = row
            require_match(
                (gen, run, bundle, manifest, extraction, candidate, vector, policy, count, state) ==
                (generation, accepted.run_id, accepted.publication_bundle_id, accepted.snapshot_manifest_id,
                 accepted.extraction_receipt_id, accepted.graph_candidate_id, accepted.snapshot_vector_json,
                 lexical_policy.policy_digest, 24, "published")
                and json.loads(binding_digests) == lexical_policy.binding_policy_digests
                and tuple(json.loads(admitted)) == lexical_policy.admitted_binding_ids, "postgres_capture_identity",
            )
            require_match(type(binding) is str and type(body) is str and type(chunks) is int and chunks == actual_chunks, "postgres_capture_content")
            body_digest = sha256(body.encode()).hexdigest()
            require_match(digest == "sha256:" + body_digest, "postgres_capture_readback")
            files.append(ProjectedFileWitness(FileIdentityTuple(binding, snapshot, path), body_digest, chunks))
        require_match(len(rows) == 24, "postgres_capture_census")
        vector_generation = self.read_vector_generation()
        self._guard_config()
        return CaptureReadback(
            digest_json(pin.to_dict()).removeprefix("sha256:"), full_policy_digest(self.evaluator_policy),
            tuple(files), generation, vector_generation, lexical_policy.policy_digest, self.config_digest,
        )


def _unified_policy(store: BodyProjectionPolicy, evaluator: BodyProjectionPolicy) -> BodyProjectionPolicy:
    """Compare live store limits while using the evaluator's complete deny map."""
    return replace(store, bindings=evaluator.bindings)


def bind_matched_postgres(
    config: OpsConfig, *, pin: VerifiedPublicationPin, policy: BodyProjectionPolicy,
    read_vector_generation: Callable[[], str | None],
) -> MatchedPostgres:
    require_match(pin.is_factory_issued() and pin.backend == "postgresql", "postgres_capture_pin")
    initial = _config_bytes(config)
    require_match(_fresh_ops_config(config) == config and _config_bytes(config) == initial, "postgres_config_drift")
    result = MatchedPostgres(config, pin, policy, read_vector_generation, initial)
    object.__setattr__(result, "_issued_identity", id(result))
    return result
