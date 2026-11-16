"""Owned Docker lifecycle checks for the supplement study PostgreSQL cluster."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from retrieval_supplement_study_contracts import (
    PG_MEMORY_BYTES,
    PG_SHM_BYTES,
    PG_TMPFS_BYTES,
    refuse,
)


_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_POSTGRES_UID = "999:999"
_PGDATA_DESTINATION = "/var/lib/postgresql/data"
_TMP_DESTINATION = "/tmp"
_BOOTSTRAP_SOCKET_DESTINATION = "/var/run/postgresql"
_PG_LABELS = {
    "org.repomap.test": "true",
    "org.repomap.component": "postgres",
    "org.repomap.purpose": "supplement-study",
}
_POSTGRES_ENV = {
    "POSTGRES_HOST_AUTH_METHOD": "trust",
    "POSTGRES_DB": "repomap_study",
    "POSTGRES_USER": "postgres",
    "PGPORT": "55433",
}
_PGDATA_TMPFS = f"rw,mode=1777,size={PG_TMPFS_BYTES // 1024**2}m"


def find_psql() -> str:
    """Resolve the existing SDK-local psql once; never install or substitute it."""
    command = shutil.which("psql")
    refuse(
        isinstance(command, str) and Path(command).is_absolute()
        and Path(command).is_file() and os.access(command, os.X_OK),
        "study_pg:psql_unavailable",
    )
    assert isinstance(command, str)
    return command


def owned_socket_directory(path: Path) -> bool:
    stat = path.stat()
    return stat.st_uid == 999 and stat.st_gid == 999 and stat.st_mode & 0o777 == 0o700


def postgres_command(sock_dir: Path) -> list[str]:
    return [
        "-c", "listen_addresses=", "-c", "port=55433",
        "-c", f"unix_socket_directories={sock_dir}", "-c", "jit=off",
    ]


def _retained_runtime_labels() -> dict[str, str]:
    from runner_pgvector_prebuilt import (
        OWNER_LABEL, OWNER_VALUE, PACKAGE_SHA256, PROVIDER_LABEL, RECIPE_LABEL,
        recipe_digest,
    )

    return {
        OWNER_LABEL: OWNER_VALUE,
        PROVIDER_LABEL: PACKAGE_SHA256,
        RECIPE_LABEL: recipe_digest(),
        "org.repomap.test.profile": "pgvector",
    }


def inspect_runtime_image(runtime_image_id: str) -> dict[str, str]:
    inspected = subprocess.run(
        ["docker", "image", "inspect", runtime_image_id],
        capture_output=True, text=True, check=False, timeout=30,
    )
    refuse(inspected.returncode == 0, "study_pg:runtime_image_inspect_failed")
    try:
        value = json.loads(inspected.stdout)
    except (TypeError, ValueError):
        raise RuntimeError("study_pg:runtime_image_inspect_invalid") from None
    refuse(type(value) is list and len(value) == 1 and isinstance(value[0], dict),
           "study_pg:runtime_image_inspect_shape")
    document = value[0]
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    retained = _retained_runtime_labels()
    refuse(document.get("Id") == runtime_image_id, "study_pg:runtime_image_id_mismatch")
    refuse(isinstance(config, Mapping) and config.get("Cmd") == ["postgres"],
           "study_pg:runtime_image_config_mismatch")
    refuse(isinstance(labels, Mapping) and all(labels.get(key) == expected
                                               for key, expected in retained.items()),
           "study_pg:runtime_image_recipe_owner_mismatch")
    assert isinstance(labels, Mapping)
    refuse(all(isinstance(key, str) and isinstance(item, str) for key, item in labels.items()),
           "study_pg:runtime_image_labels_shape")
    return dict(labels)


def _merge_labels(inherited: Mapping[str, str]) -> dict[str, str]:
    refuse(all(isinstance(key, str) and isinstance(value, str)
               for key, value in inherited.items()), "study_pg:runtime_labels_shape")
    refuse(not set(inherited).intersection(_PG_LABELS), "study_pg:runtime_labels_collision")
    return {**dict(inherited), **_PG_LABELS}


def create_command(
    runtime_image_id: str, sock_dir: Path,
    *, inherited_labels: Mapping[str, str] | None = None,
) -> list[str]:
    labels = _merge_labels({} if inherited_labels is None else inherited_labels)
    command = [
        "docker", "container", "create", "--pull=never", "--network", "none",
        "--user", _POSTGRES_UID, "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--shm-size", "64m",
        "--tmpfs", f"{_TMP_DESTINATION}:rw,mode=1777,size=32m",
        "--tmpfs", f"{_PGDATA_DESTINATION}:{_PGDATA_TMPFS}",
        "--mount", f"type=bind,src={sock_dir},dst={sock_dir}",
        "--mount", f"type=bind,src={sock_dir},dst={_BOOTSTRAP_SOCKET_DESTINATION},readonly",
    ]
    for key, value in sorted(labels.items()):
        command.extend(("--label", f"{key}={value}"))
    for key, value in sorted(_POSTGRES_ENV.items()):
        command.extend(("--env", f"{key}={value}"))
    command.extend(("--env", "PGDATA=/var/lib/postgresql/data/pgdata", runtime_image_id))
    command.extend(postgres_command(sock_dir))
    return command


def parse_container_id(raw: str) -> str | None:
    value = raw.strip()
    return value if _CONTAINER_ID.fullmatch(value) else None


def inspect_json(cid: str) -> dict[str, Any] | None:
    inspected = subprocess.run(
        ["docker", "container", "inspect", cid],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if inspected.returncode != 0:
        detail = inspected.stderr.lower()
        if "no such" in detail or "not found" in detail:
            return None
        raise RuntimeError("study_pg:container_inspect_failed")
    try:
        value = json.loads(inspected.stdout)
    except (TypeError, ValueError):
        raise RuntimeError("study_pg:container_inspect_invalid") from None
    refuse(type(value) is list and len(value) == 1 and isinstance(value[0], dict),
           "study_pg:container_inspect_shape")
    return value[0]


def inspect_owned_container(
    cid: str, *, runtime_image_id: str, sock_dir: Path,
    expected_command: list[str], expected_labels: Mapping[str, str], running: bool,
) -> bool:
    info = inspect_json(cid)
    refuse(info is not None, "study_pg:container_missing")
    assert info is not None
    config = info.get("Config")
    host = info.get("HostConfig")
    state = info.get("State")
    refuse(info.get("Id") == cid and info.get("Image") == runtime_image_id,
           "study_pg:container_id_or_image_mismatch")
    refuse(isinstance(config, Mapping) and isinstance(host, Mapping) and isinstance(state, Mapping),
           "study_pg:container_inspect_sections")
    assert isinstance(config, Mapping) and isinstance(host, Mapping) and isinstance(state, Mapping)
    refuse(config.get("Image") == runtime_image_id, "study_pg:container_image_mismatch")
    refuse(config.get("User") == _POSTGRES_UID, "study_pg:container_user_mismatch")
    refuse(config.get("Labels") == dict(expected_labels), "study_pg:container_labels_mismatch")
    refuse(config.get("Cmd") == expected_command, "study_pg:container_command_mismatch")
    env = config.get("Env")
    refuse(isinstance(env, list) and all(f"{key}={value}" in env for key, value in _POSTGRES_ENV.items())
           and "PGDATA=/var/lib/postgresql/data/pgdata" in env, "study_pg:container_env_mismatch")
    refuse(host.get("NetworkMode") == "none" and host.get("ReadonlyRootfs") is True,
           "study_pg:container_containment_mismatch")
    refuse(host.get("Memory") == PG_MEMORY_BYTES and host.get("NanoCpus") == 2_000_000_000
           and host.get("PidsLimit") == 256 and host.get("ShmSize") == PG_SHM_BYTES,
           "study_pg:container_limits_mismatch")
    refuse(host.get("CapDrop") == ["ALL"] and host.get("SecurityOpt") == ["no-new-privileges"],
           "study_pg:container_security_mismatch")
    refuse(not host.get("PortBindings") and host.get("PublishAllPorts") is not True,
           "study_pg:container_port_mismatch")
    tmpfs = host.get("Tmpfs")
    refuse(tmpfs == {
        _TMP_DESTINATION: "rw,mode=1777,size=32m",
        _PGDATA_DESTINATION: _PGDATA_TMPFS,
    }, "study_pg:container_tmpfs_mismatch")
    binds = host.get("Binds")
    refuse(binds in (None, []), "study_pg:container_bind_mismatch")
    configured_mounts = host.get("Mounts")
    refuse(isinstance(configured_mounts, list), "study_pg:container_config_mounts_mismatch")
    assert isinstance(configured_mounts, list)
    refuse(all(isinstance(item, Mapping) and item.get("Type") in {"bind", "tmpfs"}
               for item in configured_mounts), "study_pg:container_config_mounts_mismatch")
    configured_binds = [item for item in configured_mounts
                        if isinstance(item, Mapping) and item.get("Type") == "bind"]
    destinations = {str(sock_dir), _BOOTSTRAP_SOCKET_DESTINATION}
    refuse(len(configured_binds) == 2 and all(type(item.get("Target")) is str for item in configured_binds)
           and {item.get("Target") for item in configured_binds} == destinations,
           "study_pg:container_config_mounts_mismatch")
    for configured_bind in configured_binds:
        readonly = configured_bind.get("Target") == _BOOTSTRAP_SOCKET_DESTINATION
        refuse(configured_bind.get("Source") == str(sock_dir)
               and configured_bind.get("ReadOnly", False) is readonly,
               "study_pg:container_config_bind_mismatch")
    refuse(not any(isinstance(item, Mapping) and item.get("Type") == "volume"
                   for item in configured_mounts), "study_pg:container_config_volume_mismatch")
    mounts = info.get("Mounts")
    refuse(isinstance(mounts, list), "study_pg:container_mounts_mismatch")
    assert isinstance(mounts, list)
    refuse(all(isinstance(item, Mapping) and item.get("Type") in {"bind", "tmpfs"}
               for item in mounts), "study_pg:container_mounts_mismatch")
    refuse(not any(isinstance(item, Mapping) and item.get("Type") == "volume"
                   for item in mounts), "study_pg:container_volume_mismatch")
    socket_mounts = [item for item in mounts
                     if isinstance(item, Mapping) and item.get("Type") == "bind"]
    refuse(len(socket_mounts) == 2 and all(type(item.get("Destination")) is str for item in socket_mounts)
           and {item.get("Destination") for item in socket_mounts} == destinations,
           "study_pg:container_mounts_mismatch")
    for socket_mount in socket_mounts:
        writable = socket_mount.get("Destination") == str(sock_dir)
        refuse(socket_mount.get("Source") == str(sock_dir) and socket_mount.get("RW") is writable,
               "study_pg:container_socket_mount_mismatch")
    tmpfs_mounts = [item for item in mounts
                    if isinstance(item, Mapping) and item.get("Type") == "tmpfs"]
    tmpfs_destinations = {item.get("Destination") for item in tmpfs_mounts}
    expected_tmpfs_destinations = {_TMP_DESTINATION, _PGDATA_DESTINATION}
    refuse(not tmpfs_mounts or tmpfs_destinations == expected_tmpfs_destinations,
           "study_pg:container_tmpfs_mount_mismatch")
    if running:
        # Legacy --tmpfs lives in HostConfig.Tmpfs, not necessarily Mounts.
        # Validate any reported entries and prove both actual kernel mounts.
        refuse(not tmpfs_mounts or (tmpfs_destinations == expected_tmpfs_destinations
               and all(item.get("RW") is True for item in tmpfs_mounts)),
               "study_pg:container_tmpfs_mount_mismatch")
        verify_tmpfs_mounts(cid)
    refuse(state.get("Running") is running, "study_pg:container_state_mismatch")
    return True


def verify_tmpfs_mounts(cid: str) -> None:
    """Read only this already identity-checked container's mount namespace."""
    observed = subprocess.run(
        ["docker", "exec", cid, "cat", "/proc/self/mountinfo"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    refuse(observed.returncode == 0 and len(observed.stdout.encode()) <= 65536,
           "study_pg:tmpfs_mountinfo_unavailable")
    targets: dict[str, list[bool]] = {_TMP_DESTINATION: [], _PGDATA_DESTINATION: []}
    for line in observed.stdout.splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            refuse(False, "study_pg:tmpfs_mountinfo_malformed")
        destination = fields[4]
        if destination in targets:
            targets[destination].append(filesystem[0] == "tmpfs" and "rw" in fields[5].split(","))
    proof = {"tmp": targets[_TMP_DESTINATION] == [True],
             "pgdata": targets[_PGDATA_DESTINATION] == [True]}
    # Paths, IDs and raw kernel records stay out of diagnostic stdout.
    print(json.dumps({"event": "cloe-study-pg-tmpfs-readback-v1", **proof}), flush=True)
    refuse(all(proof.values()), "study_pg:tmpfs_mountinfo_mismatch")


def remove_owned_container(
    cid: str, *, runtime_image_id: str, sock_dir: Path,
    expected_command: list[str], expected_labels: Mapping[str, str],
    cleanup_info: dict[str, Any],
) -> None:
    info = inspect_json(cid)
    if info is None:
        cleanup_info["absent_after_rm"] = True
        cleanup_info["cleanup_status"] = "already-absent"
        return
    inspect_owned_container(
        cid, runtime_image_id=runtime_image_id, sock_dir=sock_dir,
        expected_command=expected_command, expected_labels=expected_labels,
        running=bool(info.get("State", {}).get("Running")),
    )
    removed = subprocess.run(
        ["docker", "container", "rm", "--force", cid],
        capture_output=True, text=True, check=False, timeout=60,
    )
    refuse(removed.returncode == 0, "study_pg:container_remove_failed")
    absent = inspect_json(cid) is None
    refuse(absent, "study_pg:container_absence_unproven")
    cleanup_info["absent_after_rm"] = True
    cleanup_info["cleanup_status"] = "clean"


def remove_socket_directory(sock_dir: Path) -> None:
    if not sock_dir.exists() and not sock_dir.is_symlink():
        return
    refuse(sock_dir.is_dir() and not sock_dir.is_symlink(), "study_pg:socket_directory_identity")
    refuse(owned_socket_directory(sock_dir), "study_pg:socket_directory_custody_changed")
    shutil.rmtree(sock_dir)


def cleanup_container_absent(
    cleanup_info: Mapping[str, Any] | None, prior: bool = False,
) -> bool:
    """Retain a proven absence fact without replacing an earlier lifecycle state."""
    if cleanup_info is None:
        return prior
    return bool(cleanup_info.get("absent_after_rm")) or prior


__all__ = [
    "create_command", "find_psql", "inspect_owned_container", "inspect_json",
    "inspect_runtime_image", "cleanup_container_absent",
    "owned_socket_directory", "parse_container_id", "postgres_command",
    "remove_owned_container", "remove_socket_directory",
]
