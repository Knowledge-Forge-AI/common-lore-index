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
from common_lore_index.storage import default_rdbms_root
from common_lore_index.storage.authority import GraphRunId
from common_lore_index.storage.main import apply_migrations
from common_lore_index.storage.publication_readback import RunPublicationRecord
from common_lore_index.storage.semantic_candidates import CandidatePublication
from repomap_test_support.postgres_container import PostgresContainerDatabase
from repomap_test_support.retrieval_body_publication_fixture import BodyStoreFixture
from repomap_test_support.retrieval_body_study_pg_publication import (
    _public_body_binding_pairs, _publish_registered_fixture,
    _write_registered_home as _write_registered_home,
    prepare_registered_workspace, publish_registered_postgres,
)
from repomap_test_support.retrieval_body_study_pg_lifecycle import (
    BodyProcessError, BodyProcessResult, COMMAND_RESERVATION_BYTES,
    MAX_BODY_LIFECYCLE_COMMANDS, MAX_READINESS_PROBES, MAX_COMMAND_OUTPUT_BYTES,
    body_create_command, body_socket_dir_identity, cleanup_body_socket_dir,
    parse_body_container_id, run_bounded_body_command, validate_body_container_info,
)
from retrieval_body_values import REGISTERED_IDENTITY
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
    working, current = prepare_registered_workspace(workspace, inputs=inputs, policy=policy)
    lifecycle_cleanup = postgres.cleanup
    if lifecycle_cleanup is None:
        lifecycle_cleanup = {"relations_clean": False, "container_absent": None}
    public_pairs = _public_body_binding_pairs(current)
    sqlite_pub = _publish_registered_fixture(
        _write_registered_home(workspace / "sqlite-home", working, current, policy, None),
        identity=REGISTERED_IDENTITY, policy=policy,
    )
    sqlite_store = BodyStoreFixture(sqlite_pub)
    prepared = publish_registered_postgres(
        workspace, working, current, policy=policy, postgres=postgres.database,
        psql_command=postgres.psql_command,
    )
    pg_store = prepared.store
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
        prepared.ops_config, native_view, candidate_pub, postgres, working,
    )
__all__ = [
    "BodySocketPostgresHandle", "BodyStudyStores", "disposable_socket_postgres",
    "publish_registered_body_stores",
]
