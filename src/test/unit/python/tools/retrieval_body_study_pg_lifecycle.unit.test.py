"""Synthetic BODY PG lifecycle custody and bounded-I/O proofs."""
from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Any

import pytest

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_publication import (
    lexical_connection_parameters, resolve_ops_graph,
)
from common_lore_index.runtime.postgres_route import (
    effective_postgres_route, execution_postgres,
)
from repomap_test_support import retrieval_body_study_pg as study_pg
from repomap_test_support.postgres_container_config import PostgresContainerDatabase
from repomap_test_support.retrieval_body_study_pg_lifecycle import (
    BODY_PG_COMMAND_LIMIT, BODY_PG_LABELS, COMMAND_RESERVATION_BYTES,
    MAX_BODY_LIFECYCLE_RESERVED_BYTES, MAX_COMMAND_OUTPUT_BYTES,
    MAX_READINESS_PROBES, BodyProcessError, BodyProcessResult,
)
from retrieval_body_study_values import BodyStudyError
from retrieval_body_study_inputs import load_body_study_inputs
from retrieval_body_values import REGISTERED_IDENTITY

CID = "a" * 64
OTHER_CID = "b" * 64
IMAGE = "sha256:" + "c" * 64


def test_pg_command_envelope_is_explicit_and_finite() -> None:
    assert BODY_PG_COMMAND_LIMIT == 140
    assert COMMAND_RESERVATION_BYTES == 131074
    assert MAX_BODY_LIFECYCLE_RESERVED_BYTES == 18350360
    assert MAX_BODY_LIFECYCLE_RESERVED_BYTES == BODY_PG_COMMAND_LIMIT * COMMAND_RESERVATION_BYTES


def _socket_dir(command: list[str]) -> Path:
    mount = next(item for item in command if item.startswith("type=bind,src="))
    return Path(mount.split(",dst=", 1)[0].removeprefix("type=bind,src="))


def _container_info(socket_dir: Path, name: str, *, running: bool) -> dict[str, Any]:
    return {
        "Id": CID, "Name": "/" + name, "Image": IMAGE,
        "Config": {"User": "999:999", "Labels": dict(BODY_PG_LABELS)},
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True,
            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
            "Memory": 2 * 1024**3, "NanoCpus": 2_000_000_000,
            "PidsLimit": 256, "ShmSize": 64 * 1024**2,
            "PortBindings": {}, "PublishAllPorts": False,
            "Tmpfs": {"/tmp": "rw,mode=1777,size=32m",
                      "/var/lib/postgresql/data": "rw,mode=1777,size=256m"},
        },
        "Mounts": [
            {"Type": "bind", "Source": str(socket_dir), "Destination": str(socket_dir)},
            {"Type": "bind", "Source": str(socket_dir), "Destination": "/var/run/postgresql"},
        ],
        "State": {"Running": running},
    }


def test_registered_socket_config_separates_connection_and_runtime_hosts(
    tmp_path: Path,
) -> None:
    """A Unix socket remains the DB connection target, never the runtime bind host."""
    repo_root = Path(__file__).resolve().parents[5]
    frame = load_body_study_inputs(
        repo_root / "src/test/fixtures/retrieval_evaluation/body-v2",
        predecessor_root=repo_root / "src/test/fixtures/retrieval_evaluation",
        validate=True,
    )
    snapshot = frame.require_current()
    working = tmp_path / "working"
    working.mkdir()
    for alias in snapshot.corpus.bindings:
        (working / alias).mkdir()
    socket_dir = (tmp_path / "body-pgsock").resolve()
    socket_dir.mkdir()
    postgres = PostgresContainerDatabase(
        host=str(socket_dir), port=55433, database="repomap_body_study",
        user="postgres", psql_command="psql",
    )

    home = study_pg._write_registered_home(home=tmp_path / "home", working=working,
                                           snapshot=snapshot, policy=snapshot.policy,
                                           postgres=postgres)
    document = tomllib.loads((home / "body.cloe.toml").read_text(encoding="utf-8"))

    assert document["postgres"]["host"] == str(socket_dir)
    assert document["runtime"]["postgres"]["bind_host"] == "127.0.0.1"
    assert document["runtime"]["postgres"]["bind_host"] != str(socket_dir)
    assert document["runtime"]["postgres"]["host_port"] == postgres.port

    config = load_graph_registry_config_home(home)
    assert isinstance(config, OpsConfig)
    graph = resolve_ops_graph(config, REGISTERED_IDENTITY.graph_id)
    route = effective_postgres_route(config)
    executed = execution_postgres(config)
    lexical = lexical_connection_parameters(config, graph)
    assert config.postgres.host == str(socket_dir)
    assert config.runtime.bind_host == "127.0.0.1"
    assert config.runtime.postgres.bind_host == "127.0.0.1"
    assert route.kind == "configured"
    assert executed.host == str(socket_dir)
    assert lexical.host == str(socket_dir)


class _DockerDouble:
    def __init__(self, *, create: str = "ok", candidates: tuple[str, ...] | None = None,
                 readiness: str = "ready") -> None:
        self.create = create
        self.candidates = (CID,) if candidates is None else candidates
        self.readiness = readiness
        self.calls: list[list[str]] = []
        self.present = True
        self.running = False
        self.socket_dir: Path | None = None
        self.container_name: str | None = None
        self.charged = 0

    def charge(self, amount: int) -> None:
        self.charged += amount

    def __call__(self, command: list[str], *, timeout: float,
                 byte_charger: Any = None) -> BodyProcessResult:
        self.calls.append(command)
        if command[:3] == ["docker", "container", "create"]:
            self.socket_dir = _socket_dir(command)
            self.container_name = command[command.index("--name") + 1]
            if self.create == "timeout":
                raise BodyProcessError("body.pg:command_timeout", measured_bytes=4)
            if self.create == "nonzero":
                return BodyProcessResult(17, "", "create refused", 4)
            if self.create == "malformed":
                return BodyProcessResult(0, "not-a-container-id", "", 4)
            return BodyProcessResult(0, CID + "\n", "", 4)
        if command[:5] == ["docker", "container", "ls", "--all", "--no-trunc"]:
            return BodyProcessResult(0, "\n".join(self.candidates) + "\n", "", 4)
        if command[:4] == ["docker", "container", "inspect", CID]:
            if not self.present:
                return BodyProcessResult(1, "", "No such container", 4)
            assert self.socket_dir is not None and self.container_name is not None
            return BodyProcessResult(
                0, json.dumps([_container_info(self.socket_dir, self.container_name,
                                                running=self.running)]), "", 4,
            )
        if command[:4] == ["docker", "container", "inspect", OTHER_CID]:
            return BodyProcessResult(1, "", "No such container", 4)
        if command[:3] == ["docker", "container", "start"]:
            self.running = True
            return BodyProcessResult(0, "", "", 4)
        if command[:4] == ["docker", "container", "rm", "--force"]:
            assert command[-1] == CID
            self.present = False
            return BodyProcessResult(0, CID + "\n", "", 4)
        if command and command[0].endswith("/psql"):
            if self.readiness == "timeout":
                if byte_charger is not None:
                    byte_charger(COMMAND_RESERVATION_BYTES)
                raise BodyProcessError("body.pg:command_timeout",
                                       reserved_bytes=COMMAND_RESERVATION_BYTES)
            if self.readiness == "overflow":
                if byte_charger is not None:
                    byte_charger(COMMAND_RESERVATION_BYTES)
                return BodyProcessResult(0, "x" * (MAX_COMMAND_OUTPUT_BYTES + 1), "",
                                         MAX_COMMAND_OUTPUT_BYTES + 1,
                                         COMMAND_RESERVATION_BYTES)
            return BodyProcessResult(0, "off:", "", 4, COMMAND_RESERVATION_BYTES)
        raise AssertionError(f"unexpected command shape: {command!r}")


@pytest.fixture
def lifecycle_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(study_pg.shutil, "which", lambda _name: "/usr/bin/psql")
    monkeypatch.setattr(study_pg.os, "chown", lambda *_args: None)
    monkeypatch.setattr(study_pg, "apply_migrations", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(study_pg.time, "sleep", lambda _seconds: None)


@pytest.mark.parametrize("create", ["timeout", "nonzero", "malformed"])
def test_create_failure_recovers_by_exact_name_without_guessing_rm(
    tmp_path: Path, lifecycle_env: None, create: str,
) -> None:
    double = _DockerDouble(create=create, candidates=())
    with pytest.raises(BodyStudyError):
        with study_pg.disposable_socket_postgres(IMAGE, scratch_root=tmp_path,
                                                  command_runner=double):
            pytest.fail("create failure unexpectedly entered lifecycle")
    assert not any(command[:4] == ["docker", "container", "rm", "--force"]
                   for command in double.calls)
    assert not tuple(tmp_path.iterdir())


def test_create_recovery_refuses_ambiguous_owned_name_without_removal(
    tmp_path: Path, lifecycle_env: None,
) -> None:
    double = _DockerDouble(create="timeout", candidates=(CID, OTHER_CID))
    with pytest.raises(BodyStudyError):
        with study_pg.disposable_socket_postgres(IMAGE, scratch_root=tmp_path,
                                                  command_runner=double):
            pytest.fail("ambiguous recovery unexpectedly entered lifecycle")
    assert not any(command[:4] == ["docker", "container", "rm", "--force"]
                   for command in double.calls)
    assert not tuple(tmp_path.iterdir())


def test_readiness_output_bound_is_measured_and_owned_container_is_removed(
    tmp_path: Path, lifecycle_env: None,
) -> None:
    double = _DockerDouble(readiness="overflow")
    with pytest.raises(BodyStudyError):
        with study_pg.disposable_socket_postgres(IMAGE, scratch_root=tmp_path,
                                                  command_runner=double,
                                                  byte_charger=double.charge,
                                                  install_schema=lambda _handle: None):
            pytest.fail("overflow unexpectedly entered lifecycle")
    assert double.charged == COMMAND_RESERVATION_BYTES
    removals = [command for command in double.calls
                if command[:4] == ["docker", "container", "rm", "--force"]]
    assert len(removals) == 1 and removals[0][-1] == CID
    assert "--name" not in removals[0]
    assert not tuple(tmp_path.iterdir())


def test_readiness_timeout_is_capped_at_120_charged_probes(
    tmp_path: Path, lifecycle_env: None,
) -> None:
    double = _DockerDouble(readiness="timeout")
    with pytest.raises(BodyStudyError):
        with study_pg.disposable_socket_postgres(IMAGE, scratch_root=tmp_path,
                                                  command_runner=double,
                                                  byte_charger=double.charge,
                                                  install_schema=lambda _handle: None):
            pytest.fail("timed-out readiness unexpectedly entered lifecycle")
    probe_calls = [command for command in double.calls
                   if command and command[0].endswith("/psql")]
    assert len(probe_calls) == MAX_READINESS_PROBES
    assert double.charged == MAX_READINESS_PROBES * COMMAND_RESERVATION_BYTES
    assert not tuple(tmp_path.iterdir())


def test_successful_cleanup_proves_container_and_socket_absence(
    tmp_path: Path, lifecycle_env: None,
) -> None:
    double = _DockerDouble(readiness="ready")
    with study_pg.disposable_socket_postgres(IMAGE, scratch_root=tmp_path,
                                              command_runner=double,
                                              install_schema=lambda _handle: None) as (handle, cleanup):
        assert handle.cleanup is cleanup
        before_exit = dict(cleanup)
        assert before_exit["container_absent"] is None
        assert cleanup["relations_clean"] is False
        assert cleanup["command_count"] == len(double.calls) == 5
        assert cleanup["command_reserved_bytes"] == COMMAND_RESERVATION_BYTES
        assert cleanup["readiness_probe_count"] == 1
        assert cleanup["readiness_reserved_bytes"] == COMMAND_RESERVATION_BYTES
    assert cleanup["absent_after_rm"] is True
    assert cleanup["container_absent"] is True
    assert cleanup["command_count"] == len(double.calls) == 8
    assert cleanup["socket_absent"] is True
    assert cleanup["socket_cleanup_unknown"] is False
    assert not tuple(tmp_path.iterdir())
