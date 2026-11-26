"""Real disposable publication helpers for unrelated supplement functional data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from common_lore_index.ops.config_local import LocalSqliteConfig, load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.local_refresh import initialize_local_graph, refresh_local_graph
from common_lore_index.ops.local_state_layout import attempts_namespace
from common_lore_index.ops.portable_refresh import execute_portable_refresh, portable_attempts_parent
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.storage.main import apply_migrations
from common_lore_index_test_support.postgres_container import PostgresContainerDatabase
from common_lore_index_test_support.retrieval_evaluation_publication import (
    PublishedCorpusState, _load_and_validate_retained_attempt,
    read_postgres_accepted_publication, read_sqlite_accepted_publication,
)
from common_lore_index_test_support.w5_public_text_readback import load_postgres_span_rows, load_sqlite_span_rows
from common_lore_index_test_support.w5_semantic_store_support import (
    load_postgres_accepted_nodes_and_evidence, load_sqlite_accepted_nodes_and_evidence,
    make_postgres_readback_adapter, make_sqlite_readback_adapter,
)
from retrieval_public_text_contracts import PrivateMarkerScope
from retrieval_public_text_descriptors import PublicTextPreparation, prepare_public_text_generation
from retrieval_semantic_provenance import VerifiedPublicationPin, verify_publication_provenance_from_adapter
from retrieval_semantic_representation import RepresentationValidationReport, validate_canonical_node_representations
from retrieval_supplement_files import publish_member
from retrieval_supplement_values import SupplementIdentity


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_supplement_home(
    home: Path, working_copy: Path, *, identity: SupplementIdentity,
    bindings: dict[str, str], policy: BodyProjectionPolicy,
    postgres: PostgresContainerDatabase | None = None,
) -> Path:
    """Write one new isolated CLOE home; never use a predecessor policy builder."""
    if type(identity) is not SupplementIdentity:
        raise ValueError("supplement_fixture_identity")
    policy.validate()
    if policy.graph_id != identity.graph_id or set(bindings.values()) != set(policy.bindings):
        raise ValueError("supplement_fixture_policy_scope")
    if home.exists() or home.is_symlink():
        raise ValueError("supplement_fixture_home_exists")
    for alias, binding_id in bindings.items():
        resolved = policy.bindings[binding_id]
        if resolved.alias != alias or resolved.source_kind != "folder":
            raise ValueError("supplement_fixture_source_kind")
        path = working_copy / alias
        if path.is_symlink() or not path.is_dir():
            raise ValueError("supplement_fixture_source_root")
    home.mkdir(mode=0o700)
    (home / "state").mkdir(mode=0o700)
    (home / "memory").mkdir(mode=0o700)
    backend = "sqlite" if postgres is None else "postgresql"
    sections = [
        f'schema_version = 1\n[storage]\nbackend = "{backend}"\n',
        '[service]\nmode = "local"\nmcp_transport = "stdio"\nlog_level = "info"\n',
        f'[server_memory]\nenabled = false\npath = {_quoted(str((home / "memory").resolve()))}\nmode = "read_only"\n',
    ]
    if postgres is not None:
        password_line = ""
        if postgres.password is not None:
            publish_member(home, "pgpass.secret", postgres.password.encode("utf-8"))
            (home / "pgpass.secret").chmod(0o600)
            password_line = f'password_file = {_quoted(str((home / "pgpass.secret").resolve()))}\n'
        sections.extend([
            '[postgres]\n' + f'host = {_quoted(postgres.host)}\nport = {postgres.port}\n'
            + f'database = {_quoted(postgres.database)}\nuser = {_quoted(postgres.user)}\n'
            + password_line,
            '[runtime]\ncoordinator_mode = "native"\nbind_host = "127.0.0.1"\n'
            + f'[runtime.postgres]\ndirect_host_port_enabled = true\nhost_port = {postgres.port}\n'
            + f'bind_host = {_quoted(postgres.host)}\n',
        ])
    sections.append('[[graphs]]\n' + f'id = {_quoted(identity.graph_id)}\nname = "Supplement functional fixture"\n'
                    + (f'database = {_quoted(postgres.database)}\n' if postgres is not None else '')
                    + 'enabled = true\nmcp_visible = true\nrefresh_policy = "manual"\n')
    for alias, binding_id in sorted(bindings.items()):
        resolved = policy.bindings[binding_id]
        sections.append(
            '[[graphs.source_bindings]]\nschema_version = 1\n'
            + f'binding_id = {_quoted(binding_id)}\nsource_definition_id = {_quoted("src1:unit-supplement-" + alias)}\n'
            + f'alias = {_quoted(alias)}\nrevision = 1\nkind = "folder"\n'
            + f'root_path = {_quoted(str((working_copy / alias).resolve()))}\n'
            + f'repository_name = {_quoted("unit-supplement-" + alias)}\nlogical_root = {_quoted(alias)}\n'
            + f'privacy = {_quoted(resolved.resolved_binding_privacy)}\n'
            + f'evidence_retention = {_quoted(resolved.resolved_evidence_retention)}\n'
            + 'extractor_profile = "default"\nresolution_policy = "isolated"\n'
            + f'role = {_quoted(resolved.role)}\ninput_name = {_quoted(alias)}\nenabled = true\n'
        )
    publish_member(home, "supplement.cloe.toml", "\n".join(sections).encode("utf-8"))
    return home


def publish_supplement_fixture(
    home: Path, *, identity: SupplementIdentity, policy: BodyProjectionPolicy,
    psql_command: str = "psql",
) -> PublishedCorpusState:
    """Publish via real maintained refresh and verified retained-attempt readers."""
    config = load_graph_registry_config_home(home)
    graph = next(graph for graph in config.graphs if graph.id == identity.graph_id)
    namespace: str | None
    if isinstance(config, LocalSqliteConfig):
        initialize_local_graph(config, identity.graph_id)
        refresh_local_graph(config, identity.graph_id)
        accepted, receipt = read_sqlite_accepted_publication(config, identity.graph_id)
        namespace = attempts_namespace(identity.graph_id)
        backend = "sqlite"
    elif isinstance(config, OpsConfig):
        database = graph.database or config.postgres.database
        apply_migrations(None, config.postgres.psql_args_for_database(database), psql_command=psql_command)
        execute_portable_refresh(config, graph, database, authority=None)
        accepted, receipt = read_postgres_accepted_publication(config, identity.graph_id, psql_command=psql_command)
        namespace = None
        backend = "postgresql"
    else:
        raise ValueError("supplement_fixture_backend")
    attempt = hashlib.sha256(f"{receipt.attempt.job_id}\0{receipt.attempt.attempt}".encode("utf-8")).hexdigest()
    root = portable_attempts_parent(config, namespace) / attempt
    store, manifest, manifest_ref, extraction, receipt_ref, bundle, bundle_ref = (
        _load_and_validate_retained_attempt(root, receipt, identity.graph_id)
    )
    return PublishedCorpusState(
        backend=backend, graph_id=identity.graph_id, home_root=home, attempt_root=root,
        accepted_reference=accepted, receipt=receipt, extraction_receipt=extraction,
        manifest=manifest, manifest_reference=manifest_ref, bundle=bundle, bundle_reference=bundle_ref,
        receipt_reference=receipt_ref, store=store, policy=policy,
    )


class SupplementStoreFixture:
    """Real accepted store reads; no model worker or manufactured publication pin."""

    def __init__(self, published: PublishedCorpusState, *, psql_command: str = "psql") -> None:
        self.published = published
        self.psql_command = psql_command
        self.policy = published.policy
        if published.backend == "sqlite":
            self.adapter = make_sqlite_readback_adapter(published)
        else:
            self.adapter = make_postgres_readback_adapter(published, psql_command=psql_command)
        self.pin: VerifiedPublicationPin = verify_publication_provenance_from_adapter(self.adapter, policy=self.policy)

    def nodes(self) -> Any:
        if self.published.backend == "sqlite":
            return load_sqlite_accepted_nodes_and_evidence(self.published)
        return load_postgres_accepted_nodes_and_evidence(self.published, psql_command=self.psql_command)

    def spans(self) -> Any:
        if self.published.backend == "sqlite":
            return load_sqlite_span_rows(self.published)
        return load_postgres_span_rows(self.published, psql_command=self.psql_command)

    def representations(self) -> RepresentationValidationReport:
        _publication, nodes, evidence = self.nodes()
        return validate_canonical_node_representations(self.pin, accepted_nodes=nodes, accepted_evidence=evidence)

    def public_text(self, scope: PrivateMarkerScope) -> PublicTextPreparation:
        return prepare_public_text_generation(
            publication_pin=self.pin, readback_adapter=self.adapter, policy=self.policy, scope=scope,
            read_representations=self.representations, read_v1_evidence=lambda: self.nodes()[2],
            read_span_rows=self.spans,
        )

    def refresh(self) -> object:
        config = load_graph_registry_config_home(self.published.home_root)
        if isinstance(config, LocalSqliteConfig):
            return refresh_local_graph(config, self.published.graph_id)
        if not isinstance(config, OpsConfig):
            raise ValueError("supplement_fixture_backend")
        graph = next(graph for graph in config.graphs if graph.id == self.published.graph_id)
        return execute_portable_refresh(config, graph, graph.database or config.postgres.database, authority=None)
