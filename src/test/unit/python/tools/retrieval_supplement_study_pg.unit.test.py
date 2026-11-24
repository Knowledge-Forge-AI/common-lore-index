"""Source-level lifecycle guards for the Route A disposable study database."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest

from repomap_test_support import retrieval_supplement_study_pg as study_pg
from repomap_test_support import retrieval_supplement_study_pg_lifecycle as lifecycle
from repomap_test_support.postgres_container import PostgresContainerDatabase
from retrieval_supplement_study_contracts import RUNTIME_CONFIG_SHA256, StudyRefusal


CID = "a" * 64
INHERITED_LABELS = {
    "org.repomap.test.pgvector.recipe": "recipe-digest",
    "org.repomap.test.pgvector.owner": "CLOE-W5-PGDG1",
    "org.repomap.test.pgvector.package_sha256": "package-digest",
    "org.repomap.test.profile": "pgvector",
}
EXPECTED_LABELS = {
    **INHERITED_LABELS,
    "org.repomap.test": "true",
    "org.repomap.component": "postgres",
    "org.repomap.purpose": "supplement-study",
}


def test_create_command_is_exact_socket_only_contract(tmp_path: Path) -> None:
    socket_dir = tmp_path / "socket"
    command = lifecycle.create_command(
        RUNTIME_CONFIG_SHA256, socket_dir, inherited_labels=INHERITED_LABELS,
    )
    assert "--pull=never" in command and "--network" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "999:999"
    assert command[command.index("--memory") + 1] == "2g"
    assert command[command.index("--cpus") + 1] == "2"
    assert command[command.index("--pids-limit") + 1] == "256"
    assert command[command.index("--shm-size") + 1] == "64m"
    assert "/var/lib/postgresql/data:rw,mode=1777,size=256m" in command
    assert "/tmp:rw,mode=1777,size=32m" in command
    mount = command[command.index("--mount") + 1]
    assert f"src={socket_dir},dst={socket_dir}" in mount
    assert f"type=bind,src={socket_dir},dst=/var/run/postgresql,readonly" in command
    assert command.count("--mount") == 2 and "PGPORT=55433" in command
    assert command[-4:] == [
        "-c", f"unix_socket_directories={socket_dir}", "-c", "jit=off",
    ]
    assert all(f"{key}={value}" in command for key, value in EXPECTED_LABELS.items())


def test_inherited_label_collision_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StudyRefusal, match="labels_collision"):
        lifecycle.create_command(
            RUNTIME_CONFIG_SHA256, tmp_path / "socket",
            inherited_labels={"org.repomap.test": "image-owned"},
        )


def test_runtime_and_psql_identity_refuse_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(StudyRefusal, match="runtime_image_id"):
        with study_pg.disposable_socket_postgres("sha256:" + "0" * 64, scratch_root=tmp_path):
            pytest.fail("unexpected runtime admission")
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "psql")
    with pytest.raises(StudyRefusal, match="psql_unavailable"):
        lifecycle.find_psql()
    tool = tmp_path / "psql"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(stat.S_IRWXU)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: str(tool))
    assert lifecycle.find_psql() == str(tool)


def test_partial_create_failure_removes_only_fresh_socket_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(study_pg, "_find_psql", lambda: "/sdk/bin/psql")
    monkeypatch.setattr(study_pg, "_inspect_runtime_image", lambda _image: dict(INHERITED_LABELS))
    monkeypatch.setattr(study_pg.os, "chown", lambda *_args: None)
    monkeypatch.setattr(study_pg, "_owned_socket_directory", lambda _path: True)
    monkeypatch.setattr(study_pg, "_remove_socket_directory", lambda path: path.rmdir())
    monkeypatch.setattr(
        study_pg.subprocess, "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 17, "", "create refused"),
    )
    with pytest.raises(StudyRefusal, match="container_create_failed"):
        with study_pg.disposable_socket_postgres(RUNTIME_CONFIG_SHA256, scratch_root=tmp_path):
            pytest.fail("unexpected partial setup success")
    assert not tuple(tmp_path.iterdir())


def test_socket_directory_custody_rejects_altered_mode(tmp_path: Path) -> None:
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir(mode=0o700)
    socket_dir.chmod(0o755)
    assert not lifecycle.owned_socket_directory(socket_dir)


def _inspected_container(socket_dir: Path, image: str, *, running: bool) -> dict[str, object]:
    return {
        "Id": CID,
        "Image": image,
        "Config": {
            "Image": image,
            "User": "999:999",
            "Labels": EXPECTED_LABELS,
            "Cmd": lifecycle.postgres_command(socket_dir),
            "Env": [
                "POSTGRES_HOST_AUTH_METHOD=trust", "POSTGRES_DB=repomap_study",
                "POSTGRES_USER=postgres", "PGDATA=/var/lib/postgresql/data/pgdata",
                "PGPORT=55433",
            ],
        },
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True, "Memory": 2 * 1024**3,
            "NanoCpus": 2_000_000_000, "PidsLimit": 256, "ShmSize": 64 * 1024**2,
            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
            "PortBindings": {}, "PublishAllPorts": False,
            "Tmpfs": {
                "/tmp": "rw,mode=1777,size=32m",
                "/var/lib/postgresql/data": "rw,mode=1777,size=256m",
            },
            "Binds": None,
            "Mounts": [{
                "Type": "bind", "Source": str(socket_dir),
                "Target": str(socket_dir),
            }, {"Type": "bind", "Source": str(socket_dir),
                "Target": "/var/run/postgresql", "ReadOnly": True}],
        },
        "Mounts": [
            {"Type": "bind", "Source": str(socket_dir), "Destination": str(socket_dir), "RW": True},
            {"Type": "bind", "Source": str(socket_dir), "Destination": "/var/run/postgresql", "RW": False},
            {"Type": "tmpfs", "Destination": "/tmp", "RW": True},
            {"Type": "tmpfs", "Destination": "/var/lib/postgresql/data", "RW": True},
        ],
        "State": {"Running": running},
    }


def test_inspection_rejects_image_or_mount_identity_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    socket_dir = tmp_path / "socket"
    monkeypatch.setattr(
        lifecycle, "inspect_json", lambda _cid: _inspected_container(socket_dir, "sha256:wrong", running=False),
    )
    with pytest.raises(StudyRefusal, match="image_mismatch"):
        lifecycle.inspect_owned_container(
            CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket_dir,
            expected_command=lifecycle.postgres_command(socket_dir),
            expected_labels=EXPECTED_LABELS, running=False,
        )


def test_inspection_rejects_legacy_bind_list_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    socket_dir = tmp_path / "socket"
    info = _inspected_container(socket_dir, RUNTIME_CONFIG_SHA256, running=False)
    host = info["HostConfig"]
    assert isinstance(host, dict)
    host["Binds"] = [f"{socket_dir}:{socket_dir}"]
    monkeypatch.setattr(lifecycle, "inspect_json", lambda _cid: info)
    with pytest.raises(StudyRefusal, match="container_bind_mismatch"):
        lifecycle.inspect_owned_container(
            CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket_dir,
            expected_command=lifecycle.postgres_command(socket_dir),
            expected_labels=EXPECTED_LABELS, running=False,
        )


@pytest.mark.parametrize("fault", [None, "image", "command", "labels", "cli-error", "json"])
def test_runtime_image_inspection_requires_retained_identity(
    monkeypatch: pytest.MonkeyPatch, fault: str | None,
) -> None:
    monkeypatch.setattr(lifecycle, "_retained_runtime_labels", lambda: dict(INHERITED_LABELS))
    document = {"Id": RUNTIME_CONFIG_SHA256, "Config": {
        "Cmd": ["postgres"], "Labels": INHERITED_LABELS,
    }}
    if fault == "image":
        document["Id"] = "sha256:" + "0" * 64
    elif fault == "command":
        document["Config"] = {"Cmd": ["other"], "Labels": INHERITED_LABELS}
    elif fault == "labels":
        document["Config"] = {"Cmd": ["postgres"], "Labels": {}}

    def inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        # `image inspect` already returns full IDs and has no --no-trunc option.
        # A permissive double hid that real CLI refusal before native proof.
        assert command == ["docker", "image", "inspect", RUNTIME_CONFIG_SHA256]
        return subprocess.CompletedProcess(
            command, 1 if fault == "cli-error" else 0,
            "{" if fault == "json" else json.dumps([document]), "",
        )

    monkeypatch.setattr(lifecycle.subprocess, "run", inspect)
    if fault is None:
        assert lifecycle.inspect_runtime_image(RUNTIME_CONFIG_SHA256) == INHERITED_LABELS
    else:
        with pytest.raises((StudyRefusal, RuntimeError)):
            lifecycle.inspect_runtime_image(RUNTIME_CONFIG_SHA256)


def test_handle_and_database_carry_explicit_sdk_psql(tmp_path: Path) -> None:
    command = str(tmp_path / "bin" / "psql")
    database = PostgresContainerDatabase(
        host=str(tmp_path / "socket"), port=55433, user="postgres",
        database="repomap_study", psql_command=command,
    )
    handle = study_pg.SocketPostgresHandle(database=database, container_id=CID, psql_command=command)
    assert database.psql_command == command
    assert handle.psql_command == command
    assert handle.psql_args[:2] == ["-h", str(tmp_path / "socket")]


def test_cleanup_removes_only_exact_owned_container_without_volume_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_dir = tmp_path / "socket"
    info = _inspected_container(socket_dir, RUNTIME_CONFIG_SHA256, running=True)
    calls: list[list[str]] = []

    inspected = [info, info, None]

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, MOUNTINFO if command[1] == "exec" else "", "")

    monkeypatch.setattr(lifecycle, "inspect_json", lambda _cid: inspected.pop(0))
    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    cleanup: dict[str, object] = {}
    lifecycle.remove_owned_container(
        CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket_dir,
        expected_command=lifecycle.postgres_command(socket_dir), expected_labels=EXPECTED_LABELS,
        cleanup_info=cleanup,
    )
    assert cleanup == {"absent_after_rm": True, "cleanup_status": "clean"}
    assert all("--volumes" not in command for command in calls)
    assert ["docker", "container", "rm", "--force", CID] in calls


MOUNTINFO = (
    "1 0 0:1 / /tmp rw,nosuid - tmpfs tmpfs rw,size=32768k\n"
    "2 0 0:2 / /var/lib/postgresql/data rw - tmpfs tmpfs rw,size=262144k\n"
)


@pytest.mark.parametrize("value,accepted", [(False, True), (True, False), (None, False), (0, False), ("false", False)])
def test_configured_bind_default_does_not_accept_malformed_or_readonly_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object, accepted: bool,
) -> None:
    socket = tmp_path / "socket"
    info = _inspected_container(socket, RUNTIME_CONFIG_SHA256, running=False)
    host = info["HostConfig"]
    assert isinstance(host, dict) and isinstance(host["Mounts"], list)
    host["Mounts"][0]["ReadOnly"] = value
    monkeypatch.setattr(lifecycle, "inspect_json", lambda _cid: info)
    def inspect() -> bool:
        return lifecycle.inspect_owned_container(
            CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket,
            expected_command=lifecycle.postgres_command(socket), expected_labels=EXPECTED_LABELS, running=False)
    if accepted:
        assert inspect()
    else:
        with pytest.raises(StudyRefusal, match="config_bind_mismatch"):
            inspect()


@pytest.mark.parametrize("raw,code", [
    (MOUNTINFO, 0), (MOUNTINFO.replace("/tmp rw", "/tmp ro"), 0),
    (MOUNTINFO.replace("tmpfs tmpfs", "ext4 rootfs"), 0),
    (MOUNTINFO.splitlines()[0] + "\n", 0), (MOUNTINFO + MOUNTINFO, 0),
    ("malformed", 0), (MOUNTINFO, 1), ("x" * 65537, 0),
])
def test_kernel_tmpfs_readback_is_exact_bounded_and_read_only(
    monkeypatch: pytest.MonkeyPatch, raw: str, code: int,
) -> None:
    def read(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "exec", CID, "cat", "/proc/self/mountinfo"]
        return subprocess.CompletedProcess(command, code, raw, "")
    monkeypatch.setattr(lifecycle.subprocess, "run", read)
    if raw == MOUNTINFO and code == 0:
        lifecycle.verify_tmpfs_mounts(CID)
    else:
        with pytest.raises(StudyRefusal, match="tmpfs_mountinfo"):
            lifecycle.verify_tmpfs_mounts(CID)


def test_omitted_config_flag_and_engine_tmpfs_rows_keep_effective_bind_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = tmp_path / "socket"
    info = _inspected_container(socket, RUNTIME_CONFIG_SHA256, running=True)
    mounts = info["Mounts"]
    assert isinstance(mounts, list)
    effective = mounts[:2]
    info["Mounts"] = effective
    monkeypatch.setattr(lifecycle, "inspect_json", lambda _cid: info)
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda command, **_kw: subprocess.CompletedProcess(command, 0, MOUNTINFO, ""))
    def inspect() -> bool:
        return lifecycle.inspect_owned_container(
            CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket,
            expected_command=lifecycle.postgres_command(socket), expected_labels=EXPECTED_LABELS, running=True)
    assert inspect()
    effective[0]["RW"] = False
    with pytest.raises(StudyRefusal, match="socket_mount_mismatch"):
        inspect()


@pytest.mark.parametrize("fault", ["source", "target", "config-readonly", "effective-rw", "missing", "duplicate", "port"])
def test_bootstrap_alias_is_same_owned_directory_and_metadata_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    socket = tmp_path / "socket"
    info = _inspected_container(socket, RUNTIME_CONFIG_SHA256, running=False)
    host, config, mounts = info["HostConfig"], info["Config"], info["Mounts"]
    assert isinstance(host, dict) and isinstance(config, dict) and isinstance(mounts, list)
    configured = host["Mounts"]
    assert isinstance(configured, list)
    if fault == "source":
        configured[1]["Source"] = "/foreign/socket"
    elif fault == "target":
        configured[1]["Target"] = "/foreign/target"
    elif fault == "config-readonly":
        configured[1]["ReadOnly"] = False
    elif fault == "effective-rw":
        mounts[1]["RW"] = True
    elif fault == "missing":
        configured.pop()
    elif fault == "duplicate":
        configured.append(dict(configured[1]))
    else:
        env = config["Env"]
        assert isinstance(env, list)
        env.remove("PGPORT=55433")
    monkeypatch.setattr(lifecycle, "inspect_json", lambda _cid: info)
    with pytest.raises(StudyRefusal):
        lifecycle.inspect_owned_container(
            CID, runtime_image_id=RUNTIME_CONFIG_SHA256, sock_dir=socket,
            expected_command=lifecycle.postgres_command(socket), expected_labels=EXPECTED_LABELS, running=False)


@pytest.mark.parametrize("code,output,ready", [
    (0, "off::postgres\n", True), (0, "off::bash\n", False),
    (0, "off:\n", False), (0, "on::postgres\n", False),
    (0, "off:*:postgres\n", False), (2, "off::postgres\n", False),
])
def test_readiness_rejects_temporary_bootstrap_jit_and_network_drift(
    code: int, output: str, ready: bool,
) -> None:
    assert study_pg._socket_ready(subprocess.CompletedProcess([], code, output, "")) is ready
    assert "pg_read_file('/proc/1/comm', 0, 64)" in study_pg._SOCKET_READINESS_SQL


def test_schema_installer_is_single_owner_of_experimental_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(study_pg, "_inspect_runtime_image", lambda _image: dict(INHERITED_LABELS))
    monkeypatch.setattr(study_pg, "_find_psql", lambda: "/sdk/psql")
    monkeypatch.setattr(study_pg.os, "chown", lambda *_args: None)
    monkeypatch.setattr(study_pg, "_owned_socket_directory", lambda _path: True)
    monkeypatch.setattr(study_pg, "_inspect_owned_container", lambda *_args, **_kw: True)
    monkeypatch.setattr(study_pg, "_remove_socket_directory", lambda path: path.rmdir())
    monkeypatch.setattr(study_pg, "apply_migrations", lambda *_args, **_kw: events.append("core"))
    monkeypatch.setattr(study_pg, "install_candidate_schema", lambda _handle: events.append("candidate"))

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "container", "create"]:
            return subprocess.CompletedProcess(command, 0, CID, "")
        if command[:3] == ["docker", "container", "start"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[0] == "/sdk/psql" and study_pg._SOCKET_READINESS_SQL in command
        return subprocess.CompletedProcess(command, 0, "off::postgres\n", "")

    def cleanup(_cid: str, **kwargs: object) -> None:
        info = kwargs["cleanup_info"]
        assert isinstance(info, dict)
        info.update(absent_after_rm=True, cleanup_status="clean")

    monkeypatch.setattr(study_pg.subprocess, "run", run)
    monkeypatch.setattr(study_pg, "_remove_owned_container", cleanup)
    with study_pg.disposable_socket_postgres(RUNTIME_CONFIG_SHA256, scratch_root=tmp_path) as (_, receipt):
        assert events == ["core", "candidate"]
    assert receipt["absent_after_rm"] is True
    assert not tuple(tmp_path.iterdir())
