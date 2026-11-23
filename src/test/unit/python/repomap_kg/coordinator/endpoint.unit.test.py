import json
import os
from pathlib import Path
from typing import Any, Mapping, cast

import pytest

from common_lore_index.coordinator import endpoint
from common_lore_index.coordinator.endpoint import (
    EndpointDescriptorError,
    LoopbackEndpointDescriptor,
    load_endpoint_descriptor,
    write_endpoint_descriptor,
)


def descriptor(**updates):
    values = {
        "schema_version": 1,
        "host": "127.0.0.1",
        "port": 43123,
        "instance_id": "instance-1",
        "fencing_epoch": 7,
        "auth_token": "token-1234567890",
    }
    values.update(updates)
    return LoopbackEndpointDescriptor(**cast(dict[str, Any], values))


def test_descriptor_round_trips_exact_versioned_fields(tmp_path: Path):
    path = tmp_path / "coordinator.endpoint.json"

    write_endpoint_descriptor(path, descriptor())

    loaded = load_endpoint_descriptor(path)
    assert loaded == descriptor()
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "schema_version",
        "host",
        "port",
        "instance_id",
        "fencing_epoch",
        "auth_token",
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"host": "0.0.0.0"},
        {"host": "localhost"},
        {"port": 0},
        {"port": -1},
        {"port": 65536},
        {"port": True},
        {"port": "8080"},
        {"fencing_epoch": 0},
        {"fencing_epoch": -5},
        {"fencing_epoch": True},
        {"fencing_epoch": "7"},
        {"auth_token": ""},
        {"auth_token": "a" * 257},
        {"auth_token": 12345},
        {"instance_id": ""},
        {"instance_id": "a" * 129},
        {"instance_id": 12345},
        {"instance_id": "C:\\private\\worker"},
    ],
)
def test_descriptor_rejects_unsafe_values(updates):
    with pytest.raises(EndpointDescriptorError, match="descriptor"):
        descriptor(**updates).validate()


def test_descriptor_rejects_extra_or_missing_fields(tmp_path: Path):
    path = tmp_path / "coordinator.endpoint.json"
    path.write_text(
        json.dumps({**descriptor().__dict__, "extra": "nope"}),
        encoding="utf-8",
    )

    with pytest.raises(EndpointDescriptorError, match="descriptor"):
        load_endpoint_descriptor(path)


def test_descriptor_rotation_replaces_only_the_known_file(tmp_path: Path):
    path = tmp_path / "coordinator.endpoint.json"
    write_endpoint_descriptor(path, descriptor())
    write_endpoint_descriptor(path, descriptor(port=43124, fencing_epoch=8))

    assert load_endpoint_descriptor(path).port == 43124
    assert load_endpoint_descriptor(path).fencing_epoch == 8


def test_descriptor_rejects_symlink_without_following_it(tmp_path: Path):
    backing = tmp_path / "backing.json"
    write_endpoint_descriptor(backing, descriptor())
    path = tmp_path / "coordinator.endpoint.json"
    path.symlink_to(backing)

    with pytest.raises(EndpointDescriptorError, match="descriptor"):
        load_endpoint_descriptor(path)


def test_descriptor_can_require_the_current_instance_and_epoch(tmp_path: Path):
    path = tmp_path / "coordinator.endpoint.json"
    write_endpoint_descriptor(path, descriptor())

    assert load_endpoint_descriptor(
        path, expected_instance_id="instance-1", expected_fencing_epoch=7
    ) == descriptor()
    with pytest.raises(EndpointDescriptorError, match="stale"):
        load_endpoint_descriptor(path, expected_instance_id="instance-2")
    with pytest.raises(EndpointDescriptorError, match="stale"):
        load_endpoint_descriptor(path, expected_fencing_epoch=8)


def test_write_descriptor_requires_absolute_path():
    with pytest.raises(EndpointDescriptorError, match="path is invalid"):
        write_endpoint_descriptor(Path("relative/path.json"), descriptor())


def test_descriptor_fresh_factory():
    desc = LoopbackEndpointDescriptor.fresh(
        port=54321,
        instance_id="fresh-instance",
        fencing_epoch=3,
    )
    assert desc.port == 54321
    assert desc.instance_id == "fresh-instance"
    assert desc.fencing_epoch == 3
    assert len(desc.auth_token) >= 16


def test_descriptor_from_mapping_type_errors():
    with pytest.raises(EndpointDescriptorError, match="fields are invalid"):
        LoopbackEndpointDescriptor.from_mapping(cast(Mapping[str, object], "not-a-mapping"))


@pytest.mark.parametrize(
    "token",
    [
        " whitespace_prefix123456",
        "whitespace_suffix123456 ",
        "token\x00withnull123456",
        "short",
    ],
)
def test_descriptor_rejects_malformed_auth_tokens(token):
    with pytest.raises(EndpointDescriptorError, match="descriptor is invalid"):
        descriptor(auth_token=token).validate()


def test_load_descriptor_rejects_empty_or_bad_permission_file(tmp_path: Path):
    empty_path = tmp_path / "empty.json"
    empty_path.write_bytes(b"")
    empty_path.chmod(0o600)
    with pytest.raises(EndpointDescriptorError, match="unsafe"):
        load_endpoint_descriptor(empty_path)

    perm_path = tmp_path / "badperm.json"
    write_endpoint_descriptor(perm_path, descriptor())
    perm_path.chmod(0o644)
    with pytest.raises(EndpointDescriptorError, match="unsafe"):
        load_endpoint_descriptor(perm_path)


def test_load_descriptor_oversized_or_malformed(tmp_path: Path):
    oversized_path = tmp_path / "oversized.json"
    oversized_path.write_bytes(b"{" + b"a" * 4096 + b"}")
    oversized_path.chmod(0o600)
    with pytest.raises(EndpointDescriptorError, match="unsafe"):
        load_endpoint_descriptor(oversized_path)

    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_bytes(b"not-json-content\n")
    malformed_path.chmod(0o600)
    with pytest.raises(EndpointDescriptorError, match="invalid"):
        load_endpoint_descriptor(malformed_path)


def test_deep_descriptor_is_rejected_as_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "endpoint.json"
    path.write_bytes(b"[" * 1800 + b"]" * 1800)
    path.chmod(0o600)

    def decoder_exhausted(_data: str) -> object:
        raise RecursionError("fixture decoder limit")

    monkeypatch.setattr(json, "loads", decoder_exhausted)
    with pytest.raises(EndpointDescriptorError, match="invalid"):
        load_endpoint_descriptor(path)


def test_read_refuses_in_place_version_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "endpoint.json"
    write_endpoint_descriptor(path, descriptor())
    saved = os.fstat
    identity = path.stat().st_ino
    reads = 0

    def change(fd: int) -> os.stat_result:
        nonlocal reads
        found = saved(fd)
        if found.st_ino == identity:
            reads += 1
            if reads == 2:
                path.write_bytes(path.read_bytes().replace(b"43123", b"43124"))
                os.utime(path, ns=(found.st_atime_ns, found.st_mtime_ns - 2_000_000_000))
                return saved(fd)
        return found

    monkeypatch.setattr(os, "fstat", change)
    with pytest.raises(EndpointDescriptorError, match="unsafe"):
        load_endpoint_descriptor(path)
    assert reads == 2


def test_rotation_rollback_restores_exact_verified_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "endpoint.json"
    write_endpoint_descriptor(path, descriptor())
    # Preserve older, noncanonical whitespace during rollback.
    prior = b" " + path.read_bytes()
    path.write_bytes(prior)

    def fail(_path: Path) -> None:
        raise OSError("fixture directory sync failure")

    monkeypatch.setattr(endpoint, "_fsync_parent", fail)
    with pytest.raises(EndpointDescriptorError, match="publication failed"):
        write_endpoint_descriptor(path, descriptor(port=43124))
    assert path.read_bytes() == prior
    assert load_endpoint_descriptor(path) == descriptor()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor lock")
@pytest.mark.parametrize("fixture", ["linked", "replaced"])
def test_rotation_refuses_unsafe_lock_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: str,
) -> None:
    path = tmp_path / "endpoint.json"
    lock = tmp_path / ".endpoint.json.lock"
    if fixture == "linked":
        alias = tmp_path / "alias"
        alias.touch(mode=0o600)
        os.link(alias, lock)
    else:
        original = endpoint.fcntl.flock

        def change(fd: int, action: int) -> None:
            original(fd, action)
            if action == endpoint.fcntl.LOCK_EX:
                lock.unlink()
                lock.touch(mode=0o600)

        monkeypatch.setattr(endpoint.fcntl, "flock", change)
    with pytest.raises(EndpointDescriptorError, match="lock unsafe"):
        write_endpoint_descriptor(path, descriptor())
    assert lock.exists() and not path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor lock")
def test_rotation_keeps_published_descriptor_on_secondary_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "endpoint.json"
    lock = tmp_path / ".endpoint.json.lock"
    close = os.close

    def close_with_error(fd: int) -> None:
        owned_lock = os.fstat(fd).st_ino == lock.stat().st_ino
        close(fd)
        if owned_lock:
            raise OSError("fixture lock close failure")

    with monkeypatch.context() as fault:
        fault.setattr(os, "close", close_with_error)
        write_endpoint_descriptor(path, descriptor())
    assert load_endpoint_descriptor(path) == descriptor()
