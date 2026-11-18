"""Owned body-study PostgreSQL daemon and registered publication boundary."""
from __future__ import annotations
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import time
from typing import Any, cast
from common_lore_index.ops.config_local import LocalSqliteConfig, load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.local_refresh import initialize_local_graph, refresh_local_graph
from common_lore_index.ops.local_state_layout import attempts_namespace
from common_lore_index.ops.portable_refresh import execute_portable_refresh, portable_attempts_parent
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from common_lore_index.storage import default_rdbms_root
from common_lore_index.storage.authority import GraphRunId
from common_lore_index.storage.main import apply_migrations
from common_lore_index.storage.publication_readback import RunPublicationRecord
from common_lore_index.storage.semantic_candidates import CandidatePublication
from repomap_test_support.postgres_container import PostgresContainerDatabase
from repomap_test_support.retrieval_body_publication_fixture import BodyStoreFixture
from repomap_test_support.retrieval_evaluation_publication import (
    PublishedCorpusState, _load_and_validate_retained_attempt,
    read_postgres_accepted_publication, read_sqlite_accepted_publication,
)
from retrieval_body_corpus import materialize_body_corpus
from repomap_test_support.retrieval_body_study_pg_lifecycle import (
    BodyProcessError, BodyProcessResult, COMMAND_RESERVATION_BYTES,
    MAX_BODY_LIFECYCLE_COMMANDS, MAX_READINESS_PROBES, MAX_COMMAND_OUTPUT_BYTES,
    body_create_command, body_socket_dir_identity, cleanup_body_socket_dir,
    parse_body_container_id, run_bounded_body_command, validate_body_container_info,
)
from retrieval_body_values import REGISTERED_IDENTITY
from retrieval_supplement_files import publish_member
from retrieval_body_study_values import require
@dataclass(frozen=True, slots=True)
class BodySocketPostgresHandle:
    database: PostgresContainerDatabase
    container_id: str
    psql_command: str
    cleanup: dict[str, Any] | None = None
    @property
    def psql_args(self) -> tuple[str, ...]:
        return tuple(self.database.psql_args)
    def psql_scalar(self, sql: str) -> str: return self.database.psql_scalar(sql)
@dataclass(frozen=True, slots=True)
class BodyStudyStores:
    sqlite_store: Any
    postgres_store: Any
    publication_identity: Mapping[str, Any]
    cleanup: Mapping[str, Any]
    ops_config: Any = None
    native_view: Any = None
    candidate_pub: Any = None
    postgres_handle: BodySocketPostgresHandle | None = None
    working_copy: Path | None = None
def _inspect(
    container_id: str, *,
    run_command: Callable[..., BodyProcessResult] = run_bounded_body_command,
    byte_charger: Callable[[int], None] | None = None,
) -> Mapping[str, Any] | None:
    try:
        observed = run_command(["docker", "container", "inspect", container_id], timeout=30,
                               byte_charger=byte_charger)
    except BodyProcessError as error:
        raise RuntimeError(error.code) from error
    if observed.returncode != 0:
        if "no such" in observed.stderr.lower() or "not found" in observed.stderr.lower(): return None
        raise RuntimeError("body.pg:inspect_failed")
    try:
        rows = json.loads(observed.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("body.pg:inspect_json") from exc
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping), "body.pg:inspect_shape")
    return rows[0]
def _recover_created_container(
    container_name: str, *, runtime_image_id: str, socket_dir: Path,
    expected_labels: Mapping[str, str] | None,
    run_command: Callable[..., BodyProcessResult],
    byte_charger: Callable[[int], None] | None,
) -> str:
    try:
        listed = run_command(["docker", "container", "ls", "--all", "--no-trunc", "--filter",
                              f"name=^/{container_name}$", "--format", "{{.ID}}"], timeout=30,
                             byte_charger=byte_charger)
    except BodyProcessError as error:
        raise RuntimeError(error.code) from error
    require(listed.returncode == 0, "body.pg:create_recovery_list")
    candidate_ids = tuple(line.strip() for line in listed.stdout.splitlines() if line.strip())
    require(len(candidate_ids) == 1, "body.pg:create_recovery_ambiguous")
    container_id = parse_body_container_id(candidate_ids[0])
    info = _inspect(container_id, run_command=run_command, byte_charger=byte_charger)
    require(info is not None, "body.pg:create_recovery_readback")
    assert info is not None
    state = info.get("State")
    require(isinstance(state, Mapping), "body.pg:create_recovery_state")
    validate_body_container_info(info, container_id=container_id, runtime_image_id=runtime_image_id,
                                 socket_dir=socket_dir, running=bool(cast(Mapping[str, Any], state).get("Running")),
                                 container_name=container_name, expected_labels=expected_labels)
    return container_id
@contextmanager
def disposable_socket_postgres(
    runtime_image_id: str, *, scratch_root: Path = Path("/sandbox-scratch"),
    runtime_labels: Mapping[str, str] | None = None,
    command_runner: Callable[..., BodyProcessResult] | None = None,
    byte_charger: Callable[[int], None] | None = None,
    install_schema: Callable[[Any], None] | None = None,
) -> Iterator[tuple[BodySocketPostgresHandle, dict[str, Any]]]:
    """Run one retained-runtime, socket-only body PG lifecycle with exact cleanup."""
    psql = shutil.which("psql")
    require(isinstance(psql, str) and Path(psql).is_absolute(), "body.pg:psql_unavailable")
    assert isinstance(psql, str); psql_command = psql
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket_dir = scratch_root / f"body-pgsock-{secrets.token_hex(8)}"
    socket_dir.mkdir(mode=0o700)
    socket_identity = body_socket_dir_identity(socket_dir)
    container_name = f"repomap-body-study-{secrets.token_hex(8)}"
    base_runner = command_runner if command_runner is not None else run_bounded_body_command
    cleanup: dict[str, Any] = {"container_id_digest": None, "container_name": container_name,
        "absent_after_rm": False, "relations_clean": False, "container_absent": None,
        "socket_absent": False, "socket_cleanup_unknown": False, "create_recovery": "not_attempted",
        "command_reservation_bytes": COMMAND_RESERVATION_BYTES, "command_count": 0,
        "command_reserved_bytes": 0, "command_measured_bytes": 0, "readiness_probe_count": 0,
        "readiness_reserved_bytes": 0, "readiness_measured_bytes": 0}
    def runner(command: list[str], *, timeout: float,
               byte_charger: Callable[[int], None] | None = None) -> BodyProcessResult:
        require(cleanup["command_count"] < MAX_BODY_LIFECYCLE_COMMANDS, "body.pg:command_count")
        cleanup["command_count"] += 1
        try:
            result = base_runner(command, timeout=timeout, byte_charger=byte_charger)
        except BodyProcessError as error:
            cleanup["command_reserved_bytes"] += error.reserved_bytes; cleanup["command_measured_bytes"] += error.measured_bytes
            raise
        except BaseException:
            cleanup["command_accounting_unknown"] = True; raise
        cleanup["command_reserved_bytes"] += result.reserved_bytes; cleanup["command_measured_bytes"] += result.measured_bytes
        return result
    container_id: str | None = None; primary: BaseException | None = None
    try:
        os.chown(socket_dir, 999, 999)
    except OSError as exc:
        primary = RuntimeError("body.pg:socket_custody"); primary.__cause__ = exc
    if primary is None:
        try:
            socket_identity = body_socket_dir_identity(socket_dir)
            command = body_create_command(runtime_image_id, socket_dir, labels=runtime_labels,
                                          container_name=container_name)
            created: BodyProcessResult | None = None
            try:
                created = runner(command, timeout=60, byte_charger=byte_charger)
            except BodyProcessError as error:
                cleanup["create_error"] = error.code; cleanup["create_measured_bytes"] = error.measured_bytes
            if created is not None and created.returncode == 0:
                try: container_id = parse_body_container_id(created.stdout); cleanup["create_recovery"] = "direct"
                except BaseException: cleanup["create_recovery"] = "malformed_id"
            elif created is not None: cleanup["create_recovery"] = "nonzero"
            if container_id is None:
                container_id = _recover_created_container(container_name, runtime_image_id=runtime_image_id,
                    socket_dir=socket_dir, expected_labels=runtime_labels, run_command=runner,
                    byte_charger=byte_charger)
                cleanup["create_recovery"] = "recovered"
            cleanup["container_id_digest"] = "sha256:" + hashlib.sha256(container_id.encode()).hexdigest(); before = _inspect(container_id, run_command=runner, byte_charger=byte_charger)
            require(before is not None, "body.pg:create_readback")
            validate_body_container_info(cast(Mapping[str, Any], before), container_id=container_id, runtime_image_id=runtime_image_id,
                socket_dir=socket_dir, running=False, container_name=container_name, expected_labels=runtime_labels)
            started = runner(["docker", "container", "start", container_id], timeout=60,
                             byte_charger=byte_charger)
            require(started.returncode == 0, "body.pg:start_failed")
            running = _inspect(container_id, run_command=runner, byte_charger=byte_charger)
            require(running is not None, "body.pg:start_readback")
            validate_body_container_info(cast(Mapping[str, Any], running), container_id=container_id, runtime_image_id=runtime_image_id,
                socket_dir=socket_dir, running=True, container_name=container_name, expected_labels=runtime_labels)
            db = PostgresContainerDatabase(host=str(socket_dir), port=55433, database="repomap_body_study",
                user="postgres", psql_command=psql_command, password=None)
            handle = BodySocketPostgresHandle(db, container_id, psql_command, cleanup)
            deadline = time.monotonic() + 120
            ready = False
            while cleanup["readiness_probe_count"] < MAX_READINESS_PROBES and time.monotonic() < deadline:
                cleanup["readiness_probe_count"] += 1; reserved_before = cleanup["command_reserved_bytes"]; measured_before = cleanup["command_measured_bytes"]
                try:
                    probe = runner([psql_command, *db.psql_args, "-X", "-qAt", "-c",
                        "SELECT current_setting('jit') || ':' || current_setting('listen_addresses');"], timeout=5,
                        byte_charger=byte_charger)
                except BodyProcessError as error:
                    cleanup["readiness_error"] = error.code
                    cleanup["readiness_reserved_bytes"] += cleanup["command_reserved_bytes"] - reserved_before; cleanup["readiness_measured_bytes"] += cleanup["command_measured_bytes"] - measured_before
                    if error.code != "body.pg:command_timeout":
                        raise RuntimeError(error.code) from error
                    time.sleep(1.0)
                    continue
                cleanup["readiness_reserved_bytes"] += cleanup["command_reserved_bytes"] - reserved_before; cleanup["readiness_measured_bytes"] += cleanup["command_measured_bytes"] - measured_before
                require(probe.measured_bytes <= MAX_COMMAND_OUTPUT_BYTES
                    and len(probe.stdout.encode("utf-8")) <= MAX_COMMAND_OUTPUT_BYTES
                    and len(probe.stderr.encode("utf-8")) <= MAX_COMMAND_OUTPUT_BYTES,
                    "body.pg:readiness_output_bound")
                if probe.returncode == 0 and probe.stdout.strip() == "off:":
                    ready = True
                    break
                time.sleep(1.0)
            require(ready, "body.pg:readiness_or_jit")
            apply_migrations(default_rdbms_root(), db.psql_args, psql_command=psql_command)
            if install_schema is None:
                from repomap_test_support.w5_experiment_postgres import PostgresHandle, install_candidate_schema
                install_candidate_schema(cast(PostgresHandle, handle))
            else:
                install_schema(handle)
            yield handle, cleanup
        except BaseException as exc:
            primary = exc
    try:
        remove_attempted = False; observed = (_inspect(container_id, run_command=runner, byte_charger=byte_charger)
                                              if container_id is not None else None)
        if observed is not None and container_id is not None:
            state = observed.get("State")
            require(isinstance(state, Mapping), "body.pg:cleanup_state")
            validate_body_container_info(observed, container_id=container_id, runtime_image_id=runtime_image_id,
                socket_dir=socket_dir, running=bool(cast(Mapping[str, Any], state).get("Running")), container_name=container_name,
                expected_labels=runtime_labels)
            removed = runner(["docker", "container", "rm", "--force", container_id], timeout=60,
                             byte_charger=byte_charger); require(removed.returncode == 0, "body.pg:remove_failed"); remove_attempted = True
        if container_id is not None:
            absent = _inspect(container_id, run_command=runner, byte_charger=byte_charger) is None
            cleanup["absent_after_rm"] = remove_attempted and absent; cleanup["container_absent"] = absent
    except BaseException as exc:
        cleanup["cleanup_error"] = type(exc).__name__; cleanup["container_cleanup_unknown"] = True
        if primary is None: primary = exc
    try:
        cleanup_body_socket_dir(socket_dir, identity=socket_identity, cleanup=cleanup)
    except BaseException as exc:
        cleanup["cleanup_error"] = type(exc).__name__; cleanup["socket_cleanup_unknown"] = True
        if primary is None: primary = exc
    if primary is not None:
        raise primary
def publish_registered_body_stores(
    workspace: Path,
    *,
    inputs: Any,
    policy: Any,
    postgres: BodySocketPostgresHandle,
) -> BodyStudyStores:
    """Materialize and publish the registered body-v2 tree through real stores."""
    from retrieval_body_pin import RegisteredBodyInputs
    require(RegisteredBodyInputs.is_factory_issued(inputs), "body.pg:registered_factory")
    current = inputs._snapshot()
    require(current.catalog.identity == REGISTERED_IDENTITY and current.catalog.registered,
            "body.pg:registered_identity")
    require(policy.to_dict() == current.policy.to_dict(), "body.pg:policy_current")
    lifecycle_cleanup = postgres.cleanup
    if lifecycle_cleanup is None:
        lifecycle_cleanup = {"relations_clean": False, "container_absent": None}
    public_pairs = _public_body_binding_pairs(current)
    require(not workspace.exists() and not workspace.is_symlink(), "body.pg:workspace_exists")
    workspace.mkdir(mode=0o700, parents=True)
    working = workspace / "body-working-copy"
    working.mkdir(mode=0o700)
    materialize_body_corpus(current.corpus, working)
    sqlite_pub = _publish_registered_fixture(
        _write_registered_home(workspace / "sqlite-home", working, current, policy, None),
        identity=REGISTERED_IDENTITY, policy=policy,
    )
    sqlite_store = BodyStoreFixture(sqlite_pub)
    pg_pub = _publish_registered_fixture(
        _write_registered_home(workspace / "postgres-home", working, current, policy, postgres.database),
        identity=REGISTERED_IDENTITY, policy=policy, psql_command=postgres.psql_command,
    )
    pg_store = BodyStoreFixture(pg_pub, psql_command=postgres.psql_command)
    loaded = load_graph_registry_config_home(pg_pub.home_root)
    require(isinstance(loaded, OpsConfig), "body.pg:ops_config")
    public_bindings = [binding_id for _, binding_id in public_pairs]
    lexical = build_graph_lexical_generation(
        cast(OpsConfig, loaded), REGISTERED_IDENTITY.graph_id, selected_binding_ids=public_bindings,
        authorize_body=True,
    )
    require(lexical.result == "published" and lexical.published is not None,
            "body.pg:lexical_publication")
    from retrieval_evaluation_views import materialize_native_search_view
    aliases = tuple(alias for alias, _ in public_pairs)
    native_view = materialize_native_search_view(
        workspace / "native-view", manifest=sqlite_pub.manifest, bundle=sqlite_pub.bundle,
        store=sqlite_pub.store, policy=policy, selected_aliases=aliases,
    )
    candidate_pub = CandidatePublication(
        pg_store.pin.repository_identity,
        RunPublicationRecord(GraphRunId(pg_store.pin.run_id), pg_store.pin.receipt),
    ).validate()
    return BodyStudyStores(
        sqlite_store, pg_store,
        {"sqlite": sqlite_store.pin.to_dict(), "postgresql": pg_store.pin.to_dict()},
        lifecycle_cleanup,
        loaded, native_view, candidate_pub, postgres, working,
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
__all__ = [
    "BodySocketPostgresHandle", "BodyStudyStores", "disposable_socket_postgres",
    "publish_registered_body_stores",
]
