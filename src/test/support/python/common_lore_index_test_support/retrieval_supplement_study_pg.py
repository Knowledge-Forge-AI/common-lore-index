"""Disposable socket-only PostgreSQL lifecycle and dual store publication for the study.

Test support only. Inside the canonical study SDK, ``disposable_socket_postgres``
starts one ``--network none --pull=never --read-only --cap-drop ALL`` PostgreSQL
container with ``listen_addresses=''``, ``jit=off``, 2 GiB memory, 2 CPUs,
256 PIDs, 64 MiB shm, a 256 MiB PGDATA tmpfs and a separate 32 MiB ``/tmp``
tmpfs, exposing only a run-owned Unix socket directory with zero live/source DB
mounts.
Cleanup stops and removes only that exact container ID and proves absence via
``docker container inspect``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.storage import default_rdbms_root
from common_lore_index.storage.authority import GraphRunId
from common_lore_index.storage.main import apply_migrations
from common_lore_index.storage.psql import run_psql
from common_lore_index.storage.publication_readback import RunPublicationRecord
from common_lore_index.storage.semantic_candidates import CandidatePublication
from common_lore_index_test_support.postgres_container import PostgresContainerDatabase
from common_lore_index_test_support.retrieval_supplement_publication_fixture import (
    SupplementStoreFixture,
    publish_supplement_fixture,
    write_supplement_home,
)
from common_lore_index_test_support.w5_experiment_postgres import install_candidate_schema
from common_lore_index_test_support.w5_public_text_postgres import cleanup_study
from retrieval_evaluation_views import (
    MaterializedNativeView,
    materialize_native_search_view,
)
from retrieval_supplement_study_contracts import (
    RUNTIME_CONFIG_SHA256,
    refuse,
)
from common_lore_index_test_support.retrieval_supplement_study_pg_lifecycle import (
    create_command as _create_command,
    find_psql as _find_psql,
    inspect_owned_container as _inspect_owned_container,
    inspect_runtime_image as _inspect_runtime_image,
    _merge_labels,
    owned_socket_directory as _owned_socket_directory,
    parse_container_id as _parse_container_id,
    postgres_command as _postgres_command,
    remove_owned_container as _remove_owned_container,
    remove_socket_directory as _remove_socket_directory,
)
from retrieval_supplement_values import SupplementIdentity


_SOCKET_READINESS_SQL = (
    "SELECT current_setting('jit') || ':' || current_setting('listen_addresses') "
    "|| ':' || rtrim(pg_read_file('/proc/1/comm', 0, 64), E'\\n');"
)


def _socket_ready(probe: subprocess.CompletedProcess[str]) -> bool:
    return probe.returncode == 0 and probe.stdout.strip() == "off::postgres"


@dataclass
class SocketPostgresHandle:
    """Minimal ``PostgresHandle`` bound to a disposable Unix-socket or local cluster."""

    database: PostgresContainerDatabase
    container_id: str
    psql_command: str
    psql_args: Sequence[str] = field(init=False)

    def __post_init__(self) -> None:
        self.psql_args = self.database.psql_args

    def psql_scalar(self, sql: str) -> str:
        cmd = [self.psql_command, *self.psql_args, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"]
        return run_psql(cmd, input_text=sql).stdout.strip()


@contextmanager
def disposable_socket_postgres(
    runtime_image_id: str, *, scratch_root: Path = Path("/sandbox-scratch"),
) -> Iterator[tuple[SocketPostgresHandle, dict[str, Any]]]:
    """Start one owned socket-only PostgreSQL container and prove its cleanup."""
    refuse(runtime_image_id == RUNTIME_CONFIG_SHA256, "study_pg:runtime_image_id")
    inherited_labels = _inspect_runtime_image(runtime_image_id)
    expected_labels = _merge_labels(inherited_labels)
    psql_command = _find_psql()
    sock_dir = scratch_root / f"study-pgsock-{secrets.token_hex(8)}"
    sock_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        os.chown(sock_dir, 999, 999)
        refuse(_owned_socket_directory(sock_dir), "study_pg:socket_directory_custody")
    except BaseException as error:
        try:
            _remove_socket_directory(sock_dir)
        except BaseException as socket_cleanup_error:
            raise error from socket_cleanup_error
        if isinstance(error, OSError):
            raise RuntimeError("study_pg:socket_directory_custody") from None
        raise
    cid: str | None = None
    cleanup_info: dict[str, Any] = {
        "container_id": "", "absent_after_rm": False,
        "cleanup_status": "unstarted", "cleanup_error": None,
    }
    expected_command = _postgres_command(sock_dir)
    primary: BaseException | None = None
    try:
        created = subprocess.run(
            _create_command(runtime_image_id, sock_dir, inherited_labels=inherited_labels),
            capture_output=True, text=True, check=False, timeout=60,
        )
        cid = _parse_container_id(created.stdout if created.returncode == 0 else "")
        refuse(cid is not None, "study_pg:container_create_failed")
        assert cid is not None
        cleanup_info["container_id"] = cid
        cleanup_info["cleanup_status"] = "created"
        before_start = _inspect_owned_container(
            cid, runtime_image_id=runtime_image_id, sock_dir=sock_dir,
            expected_command=expected_command, expected_labels=expected_labels, running=False,
        )
        refuse(before_start, "study_pg:container_identity_before_start")
        started = subprocess.run(
            ["docker", "container", "start", cid],
            capture_output=True, text=True, check=False, timeout=60,
        )
        refuse(started.returncode == 0, "study_pg:container_start_failed")
        _inspect_owned_container(
            cid, runtime_image_id=runtime_image_id, sock_dir=sock_dir,
            expected_command=expected_command, expected_labels=expected_labels, running=True,
        )
        cleanup_info["cleanup_status"] = "running"
        db = PostgresContainerDatabase(
            host=str(sock_dir), port=55433, database="repomap_study",
            user="postgres", psql_command=psql_command, password=None,
        )
        handle = SocketPostgresHandle(
            database=db, container_id=cid, psql_command=psql_command,
        )
        deadline = time.monotonic() + 60.0
        ready = False
        probe: subprocess.CompletedProcess[str] | None = None
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [psql_command, *db.psql_args, "-X", "-qAt", "-c",
                 _SOCKET_READINESS_SQL],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if _socket_ready(probe):
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            print(json.dumps({"event": "cloe-study-pg-readiness-refusal-v1",
                              "last_probe_exit": probe.returncode if probe else None,
                              "expected_state_observed": False}), flush=True)
        refuse(ready, "study_pg:socket_readiness_or_jit_failed")
        apply_migrations(default_rdbms_root(), db.psql_args, psql_command=psql_command)
        install_candidate_schema(handle)
        yield handle, cleanup_info
    except BaseException as error:
        primary = error
    cleanup_error: BaseException | None = None
    try:
        if cid:
            _remove_owned_container(
                cid, runtime_image_id=runtime_image_id, sock_dir=sock_dir,
                expected_command=expected_command, expected_labels=expected_labels,
                cleanup_info=cleanup_info,
            )
    except BaseException as error:
        cleanup_error = error
        cleanup_info["cleanup_error"] = type(error).__name__
    try:
        _remove_socket_directory(sock_dir)
    except BaseException as error:
        if cleanup_error is None:
            cleanup_error = error
        cleanup_info["cleanup_error"] = type(error).__name__
    if primary is not None and cleanup_error is not None:
        primary.add_note("study_pg:secondary_cleanup_failure")
    if primary is None:
        primary = cleanup_error
    if primary is not None:
        raise primary


def write_study_supplement_home(
    home: Path,
    working_copy: Path,
    *,
    identity: SupplementIdentity,
    bindings: dict[str, str],
    policy: BodyProjectionPolicy,
    postgres: PostgresContainerDatabase | None = None,
) -> Path:
    """Write the public durable view; the caller retains full control policy."""
    policy.validate()
    refuse(policy.graph_id == identity.graph_id
           and set(bindings.values()) == set(policy.bindings)
           and all(policy.bindings[key].alias == alias for alias, key in bindings.items()),
           "study_pg:publication_scope")
    public_ids = frozenset(
        key for key, resolved in policy.bindings.items()
        if key in policy.selected_binding_ids
        and resolved.resolved_graph_privacy == "public-dev"
        and resolved.resolved_binding_privacy == "public-dev"
        and resolved.resolved_evidence_retention == "full-authorized"
    )
    refuse(bool(public_ids), "study_pg:public_publication_scope_empty")
    bindings = {alias: key for alias, key in bindings.items() if key in public_ids}
    policy = BodyProjectionPolicy(
        graph_id=policy.graph_id, selected_binding_ids=public_ids,
        bindings={key: policy.bindings[key] for key in sorted(public_ids)},
    )
    if postgres is None or postgres.host in {"127.0.0.1", "localhost", "::1"}:
        return write_supplement_home(
            home, working_copy, identity=identity, bindings=bindings,
            policy=policy, postgres=postgres,
        )
    proxy_pg = PostgresContainerDatabase(
        host="127.0.0.1", port=postgres.port, database=postgres.database,
        user=postgres.user, psql_command=postgres.psql_command,
        password=postgres.password,
    )
    written = write_supplement_home(
        home, working_copy, identity=identity, bindings=bindings,
        policy=policy, postgres=proxy_pg,
    )
    toml_path = written / "supplement.cloe.toml"
    text = toml_path.read_text(encoding="utf-8")
    quoted_sock = json.dumps(postgres.host, ensure_ascii=False)
    text = text.replace('[postgres]\nhost = "127.0.0.1"', f"[postgres]\nhost = {quoted_sock}", 1)
    text = text.replace("direct_host_port_enabled = true", "direct_host_port_enabled = false", 1)
    toml_path.write_bytes(text.encode("utf-8"))
    return written


@dataclass(frozen=True)
class PublishedStudyStores:
    sqlite_store: SupplementStoreFixture
    postgres_store: SupplementStoreFixture
    ops_config: OpsConfig
    native_view: MaterializedNativeView
    candidate_pub: CandidatePublication


def publish_study_stores(
    workspace: Path,
    working_copy: Path,
    *,
    identity: SupplementIdentity,
    bindings: dict[str, str],
    policy: BodyProjectionPolicy,
    postgres: PostgresContainerDatabase,
    psql_command: str | None = None,
) -> PublishedStudyStores:
    """Publish identical supplement working copy to SQLite Local and PostgreSQL + FTS."""
    refuse(not workspace.is_symlink(), "study_pg:workspace_symlink")
    workspace.mkdir(mode=0o700, exist_ok=True)
    effective_psql_command = postgres.psql_command if psql_command is None else psql_command
    sqlite_home = write_study_supplement_home(
        workspace / "sqlite-home", working_copy, identity=identity,
        bindings=bindings, policy=policy, postgres=None,
    )
    sqlite_pub = publish_supplement_fixture(sqlite_home, identity=identity, policy=policy)
    sqlite_store = SupplementStoreFixture(sqlite_pub)

    pg_home = write_study_supplement_home(
        workspace / "postgres-home", working_copy, identity=identity,
        bindings=bindings, policy=policy, postgres=postgres,
    )
    pg_pub = publish_supplement_fixture(
        pg_home, identity=identity, policy=policy, psql_command=effective_psql_command,
    )
    pg_store = SupplementStoreFixture(pg_pub, psql_command=effective_psql_command)
    loaded_cfg = load_graph_registry_config_home(pg_home)
    refuse(isinstance(loaded_cfg, OpsConfig), "study_pg:ops_config_required")
    assert isinstance(loaded_cfg, OpsConfig)
    public_bindings = [
        b_id for b_id, res in sorted(policy.bindings.items())
        if res.resolved_binding_privacy == "public-dev"
    ]
    lexical = build_graph_lexical_generation(
        loaded_cfg, identity.graph_id, selected_binding_ids=public_bindings, authorize_body=True,
    )
    refuse(lexical.result == "published" and lexical.published is not None,
           "study_pg:lexical_publication_required")
    aliases = tuple(res.alias for _, res in sorted(policy.bindings.items()))
    native_view = materialize_native_search_view(
        workspace / "native-view", manifest=sqlite_pub.manifest, bundle=sqlite_pub.bundle,
        store=sqlite_pub.store, policy=policy, selected_aliases=aliases,
    )
    cand_pub = CandidatePublication(
        pg_store.pin.repository_identity,
        RunPublicationRecord(GraphRunId(pg_store.pin.run_id), pg_store.pin.receipt),
    ).validate()
    return PublishedStudyStores(sqlite_store, pg_store, loaded_cfg, native_view, cand_pub)


__all__ = [
    "PublishedStudyStores", "SocketPostgresHandle", "cleanup_study",
    "disposable_socket_postgres", "publish_study_stores", "write_study_supplement_home",
]
