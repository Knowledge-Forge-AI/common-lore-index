"""Registered body publication helpers shared by study and lexical replay."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from common_lore_index.ops.config_local import LocalSqliteConfig, load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from common_lore_index.ops.local_refresh import initialize_local_graph, refresh_local_graph
from common_lore_index.ops.local_state_layout import attempts_namespace
from common_lore_index.ops.portable_refresh import execute_portable_refresh, portable_attempts_parent
from common_lore_index.storage import default_rdbms_root
from common_lore_index.storage.main import apply_migrations
from common_lore_index_test_support.postgres_container import PostgresContainerDatabase
from common_lore_index_test_support.retrieval_body_publication_fixture import BodyStoreFixture
from common_lore_index_test_support.retrieval_evaluation_publication import (
    PublishedCorpusState, _load_and_validate_retained_attempt,
    read_postgres_accepted_publication, read_sqlite_accepted_publication,
)
from retrieval_body_corpus import materialize_body_corpus
from retrieval_body_pin import RegisteredBodyInputs
from retrieval_body_study_values import require
from retrieval_body_values import REGISTERED_IDENTITY
from retrieval_supplement_files import publish_member


@dataclass(frozen=True, slots=True)
class RegisteredLexicalStore:
    store: BodyStoreFixture
    ops_config: OpsConfig
    generation_id: str
    lexical_policy_digest: str


def prepare_registered_workspace(
    workspace: Path, *, inputs: RegisteredBodyInputs, policy: Any,
) -> tuple[Path, Any]:
    """Validate the registered snapshot before creating the owned working copy."""
    require(RegisteredBodyInputs.is_factory_issued(inputs), "body.pg:registered_factory")
    current = inputs._snapshot()
    require(current.catalog.identity == REGISTERED_IDENTITY and current.catalog.registered,
            "body.pg:registered_identity")
    require(policy.to_dict() == current.policy.to_dict(), "body.pg:policy_current")
    _public_body_binding_pairs(current)
    require(not workspace.exists() and not workspace.is_symlink(), "body.pg:workspace_exists")
    workspace.mkdir(mode=0o700, parents=True)
    working = workspace / "body-working-copy"
    working.mkdir(mode=0o700)
    materialize_body_corpus(current.corpus, working)
    return working, current


def publish_registered_postgres(
    workspace: Path, working: Path, current: Any, *, policy: Any,
    postgres: PostgresContainerDatabase, psql_command: str,
) -> RegisteredLexicalStore:
    """Publish and build lexical state only from the verified workspace."""
    body_home = _write_registered_home(workspace / "postgres-home", working, current, policy, postgres)
    publication = _publish_registered_fixture(
        body_home, identity=REGISTERED_IDENTITY, policy=policy, psql_command=psql_command,
    )
    store = BodyStoreFixture(publication, psql_command=psql_command)
    loaded = load_graph_registry_config_home(publication.home_root)
    require(isinstance(loaded, OpsConfig), "body.pg:ops_config")
    assert isinstance(loaded, OpsConfig)
    public_bindings = [binding_id for _, binding_id in _public_body_binding_pairs(current)]
    lexical = build_graph_lexical_generation(
        loaded, REGISTERED_IDENTITY.graph_id, selected_binding_ids=public_bindings,
        authorize_body=True,
    )
    require(lexical.result == "published" and lexical.published is not None, "body.pg:lexical_publication")
    assert lexical.published is not None
    return RegisteredLexicalStore(store, loaded, lexical.published.lexical_generation_id, lexical.policy_digest)


def publish_registered_lexical_store(
    workspace: Path, *, inputs: RegisteredBodyInputs, policy: Any,
    postgres: PostgresContainerDatabase, psql_command: str,
) -> RegisteredLexicalStore:
    """Prepare the PostgreSQL-only publication for the lexical replay."""
    working, current = prepare_registered_workspace(workspace, inputs=inputs, policy=policy)
    return publish_registered_postgres(
        workspace, working, current, policy=policy, postgres=postgres, psql_command=psql_command,
    )


def _write_registered_home(
    home: Path, working: Path, snapshot: Any, policy: Any,
    postgres: PostgresContainerDatabase | None,
) -> Path:
    """Write an isolated config bound to the validated registered identity."""
    require(snapshot.catalog.identity == REGISTERED_IDENTITY, "body.pg:home_identity")
    current_policy = snapshot.policy
    current_policy.validate()
    policy.validate()
    require(policy.to_dict() == current_policy.to_dict(), "body.pg:home_policy_snapshot")
    require(policy.graph_id == REGISTERED_IDENTITY.graph_id
            and set(snapshot.corpus.bindings.values()) == set(policy.bindings), "body.pg:home_policy")
    public_pairs = _public_body_binding_pairs(snapshot)
    require(not home.exists() and not home.is_symlink() and working.is_dir(), "body.pg:home_scope")
    for alias, binding_id in snapshot.corpus.bindings.items():
        resolved = policy.bindings[binding_id]
        require(resolved.alias == alias and resolved.source_kind == "folder"
                and (working / alias).is_dir(), "body.pg:home_binding")
    home.mkdir(mode=0o700); (home / "state").mkdir(mode=0o700); (home / "memory").mkdir(mode=0o700)
    backend = "sqlite" if postgres is None else "postgresql"
    sections = [
        f'schema_version = 1\n[storage]\nbackend = "{backend}"\n',
        '[service]\nmode = "local"\nmcp_transport = "stdio"\nlog_level = "info"\n',
        f'[server_memory]\nenabled = false\npath = {json.dumps(str((home / "memory").resolve()))}\nmode = "read_only"\n',
    ]
    if postgres is not None:
        sections.extend([
            '[postgres]\n' + f'host = {json.dumps(postgres.host)}\nport = {postgres.port}\n'
            + f'database = {json.dumps(postgres.database)}\nuser = {json.dumps(postgres.user)}\n',
            '[runtime]\ncoordinator_mode = "native"\nbind_host = "127.0.0.1"\n'
            + f'[runtime.postgres]\ndirect_host_port_enabled = false\nhost_port = {postgres.port}\n'
            + 'bind_host = "127.0.0.1"\n',
        ])
    sections.append('[[graphs]]\n' + f'id = {json.dumps(REGISTERED_IDENTITY.graph_id)}\n'
                    + 'name = "Body W5 calibration"\n'
                    + (f'database = {json.dumps(postgres.database)}\n' if postgres is not None else '')
                    + 'enabled = true\nmcp_visible = true\nrefresh_policy = "manual"\n')
    for alias, binding_id in public_pairs:
        resolved = policy.bindings[binding_id]
        sections.append(
            '[[graphs.source_bindings]]\nschema_version = 1\n'
            + f'binding_id = {json.dumps(binding_id)}\nsource_definition_id = {json.dumps("src1:body1-" + alias)}\n'
            + f'alias = {json.dumps(alias)}\nrevision = 1\nkind = "folder"\n'
            + f'root_path = {json.dumps(str((working / alias).resolve()))}\n'
            + f'repository_name = {json.dumps("body1-" + alias)}\nlogical_root = {json.dumps(alias)}\n'
            + f'privacy = {json.dumps(resolved.resolved_binding_privacy)}\n'
            + f'evidence_retention = {json.dumps(resolved.resolved_evidence_retention)}\n'
            + 'extractor_profile = "default"\nresolution_policy = "isolated"\n'
            + f'role = {json.dumps(resolved.role)}\ninput_name = {json.dumps(alias)}\nenabled = true\n'
        )
    publish_member(home, "body.cloe.toml", "\n".join(sections).encode("utf-8"))
    return home


def _public_body_binding_pairs(snapshot: Any) -> tuple[tuple[str, str], ...]:
    """Derive configured aliases from the full frozen BODY policy/corpus."""
    policy = snapshot.policy
    pairs = tuple(
        (alias, binding_id)
        for alias, binding_id in sorted(snapshot.corpus.bindings.items())
        if policy.bindings[binding_id].resolved_binding_privacy == "public-dev"
    )
    require(len(pairs) > 0, "body.pg:public_binding_scope")
    expected = {
        binding_id for binding_id, resolved in policy.bindings.items()
        if resolved.resolved_binding_privacy == "public-dev"
    }
    require({binding_id for _, binding_id in pairs} == expected,
            "body.pg:public_binding_scope_complete")
    require(all(policy.bindings[binding_id].alias == alias for alias, binding_id in pairs),
            "body.pg:public_binding_alias")
    return pairs


def _publish_registered_fixture(
    home: Path, *, identity: Any, policy: Any, psql_command: str = "psql",
) -> PublishedCorpusState:
    config = load_graph_registry_config_home(home)
    graph = next(graph for graph in config.graphs if graph.id == identity.graph_id)
    namespace: str | None
    if isinstance(config, LocalSqliteConfig):
        initialize_local_graph(config, identity.graph_id); refresh_local_graph(config, identity.graph_id)
        accepted, receipt = read_sqlite_accepted_publication(config, identity.graph_id)
        namespace, backend = attempts_namespace(identity.graph_id), "sqlite"
    elif isinstance(config, OpsConfig):
        database = graph.database or config.postgres.database
        apply_migrations(default_rdbms_root(), config.postgres.psql_args_for_database(database), psql_command=psql_command)
        execute_portable_refresh(config, graph, database, authority=None)
        accepted, receipt = read_postgres_accepted_publication(config, identity.graph_id, psql_command=psql_command)
        namespace, backend = None, "postgresql"
    else:
        raise RuntimeError("body.pg:config_backend")
    attempt = hashlib.sha256(f"{receipt.attempt.job_id}\0{receipt.attempt.attempt}".encode()).hexdigest()
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
