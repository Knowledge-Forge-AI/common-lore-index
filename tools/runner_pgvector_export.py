"""Validate an uncompressed Docker-save archive while retaining its one stream."""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path
import select
import subprocess
import tarfile
import time
from typing import Any, IO, cast

from runner_pgvector_budget import Allocation, MAX_ARCHIVE, refuse
from runner_pgvector_prebuilt import RUNTIME_TAG, safe_path, strict_json

BLOCK = 1024 * 1024
MAX_METADATA = 256 * 1024
_OCI_BLOB_PREFIX = "blobs/sha256/"


class RetainedStream(io.RawIOBase):
    """A bounded pipe read is both the hash input and the retained archive write."""

    def __init__(self, source: IO[bytes], target: IO[bytes], meter: Allocation,
                 deadline: float, error_source: IO[bytes] | None = None,
                 error_target: IO[bytes] | None = None) -> None:
        super().__init__()
        self.source, self.target, self.meter, self.deadline = source, target, meter, deadline
        self.count = 0
        self.hasher = hashlib.sha256()
        self.error_source, self.error_target = error_source, error_target
        self.error_count = 0

    def readable(self) -> bool:
        return True

    def read(self, count: int = -1) -> bytes:
        refuse(0 <= count <= BLOCK, "unbounded export read")
        if count == 0:
            return b""
        while True:
            remaining = self.deadline - time.monotonic()
            refuse(remaining > 0, "export timeout")
            watched = [self.source] + ([self.error_source] if self.error_source else [])
            ready, _, _ = select.select(watched, [], [], remaining)
            refuse(bool(ready), "export timeout")
            error_source = self.error_source
            if error_source is not None and error_source in ready:
                maximum = min(65537 - self.error_count, self.meter.remaining("read_bytes"))
                refuse(maximum > 0, "export stderr cap")
                error = self.meter.read(lambda count: os.read(error_source.fileno(), count), maximum)
                self.error_count += len(error)
                refuse(self.error_count <= 65536, "export stderr cap")
                if error and self.error_target is not None:
                    self.error_target.write(error)
                if not error:
                    self.error_source = None
            if self.source in ready:
                break
        limit = min(count, MAX_ARCHIVE + 1 - self.count,
                    self.meter.remaining("read_bytes"), self.meter.remaining("transfer_bytes"))
        refuse(limit > 0, "export allocation exhausted")
        # Reserve before I/O, including failure/short-read paths.
        raw = self.meter.read(lambda count: os.read(self.source.fileno(), count), limit, transfer=True)
        self.count += len(raw)
        refuse(self.count <= MAX_ARCHIVE, "export archive cap")
        self.hasher.update(raw)
        self.target.write(raw)
        return raw


def inspect_export(stream: RetainedStream, expected: dict[str, Any]) -> None:
    """Validate one legacy or modern Docker-save archive without extracting it.

    Docker Engine 25+ emits an OCI layout alongside the historical
    ``manifest.json``.  The OCI index points to an image-manifest blob, which
    in turn points to the config and ordered layer blobs.  We retain only
    bounded JSON metadata and hash every layer while streaming; the resulting
    graph is checked against the derived runtime descriptor before accepting
    the export.
    """
    members: dict[str, dict[str, Any]] = {}
    metadata: dict[str, bytes] = {}
    expected_layers = {row["sha256"]: row["bytes"] for row in expected["runtime_layers"]}
    runtime_id = expected["runtime_config"].removeprefix("sha256:")
    with tarfile.open(fileobj=stream, mode="r|", bufsize=BLOCK) as archive:
        for member in archive:
            name = safe_path(member.name)
            refuse(name not in members and len(members) < 512, "export duplicate/member cap")
            refuse((member.isdir() or member.isreg())
                   and not getattr(member, "sparse", None)
                   and member.type != tarfile.GNUTYPE_SPARSE, "export member type")
            if member.isdir():
                members[name] = {"directory": True}
                continue
            refuse(0 <= member.size <= MAX_ARCHIVE, "export member size")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("pgvector prebuilt refused: export missing regular content")
            hasher, captured, count = hashlib.sha256(), bytearray(), 0
            while count < member.size:
                block = source.read(min(BLOCK, member.size - count))
                refuse(bool(block), "short export member")
                count += len(block)
                hasher.update(block)
                if member.size <= MAX_METADATA:
                    captured.extend(block)
            sha = hasher.hexdigest()
            members[name] = {"bytes": count, "sha256": sha}
            if sha in expected_layers:
                refuse(count == expected_layers[sha], "export layer size")
            elif sha == runtime_id:
                refuse(count <= MAX_METADATA, "runtime config cap")
                metadata[name] = bytes(captured)
            else:
                # Compatibility metadata is validated after the OCI graph is
                # known.  Unknown large files are still hashed, but cannot be
                # treated as metadata because their bytes are not retained.
                metadata[name] = bytes(captured) if member.size <= MAX_METADATA else b""
        # ``r|`` wraps the RetainedStream in tarfile._Stream.  Its internal
        # zero-padding buffer may still contain bytes after the member iterator
        # reaches EOF; drain that buffer before reading the underlying pipe so
        # non-zero trailer data cannot hide behind tarfile's buffering.
        buffered = getattr(archive.fileobj, "buf", b"")
        while buffered:
            tail = archive.fileobj.read(len(buffered))
            refuse(not any(tail), "export hidden trailer")
            buffered = getattr(archive.fileobj, "buf", b"")
        while True:
            tail = archive.fileobj.read(65536)
            if not tail:
                break
            refuse(not any(tail), "export hidden trailer")
    _validate_export_graph(members, metadata, expected, expected_layers, runtime_id)


def _descriptor(value: Any, *, label: str,
                media_types: set[str] | None = None) -> tuple[str, int]:
    """Return one strict OCI descriptor digest/size pair."""
    refuse(isinstance(value, dict) and set(value) <= {"mediaType", "digest", "size", "platform", "annotations"},
           label + " fields")
    if media_types is not None:
        refuse(value.get("mediaType") in media_types, label + " media type")
    digest_value, size = value.get("digest"), value.get("size")
    refuse(isinstance(digest_value, str) and len(digest_value) == 71
           and digest_value.startswith("sha256:")
           and all(ch in "0123456789abcdef" for ch in digest_value[7:])
           and type(size) is int and 0 <= size <= MAX_ARCHIVE, label + " identity")
    return digest_value[7:], size


def _validate_export_graph(members: dict[str, dict[str, Any]], metadata: dict[str, bytes],
                           expected: dict[str, Any], expected_layers: dict[str, int],
                           runtime_id: str) -> None:
    """Validate legacy names or the exact modern OCI index/manifest graph."""
    refuse("manifest.json" in metadata, "Docker compatibility manifest required")
    legacy_raw = metadata["manifest.json"]
    legacy = strict_json(legacy_raw)
    refuse(isinstance(legacy, list) and len(legacy) == 1, "one exported runtime required")
    legacy_row = legacy[0]
    refuse(isinstance(legacy_row, dict)
           and set(legacy_row) <= {"Config", "Layers", "RepoTags", "LayerSources", "Parent"}
           and legacy_row.get("RepoTags") in (None, [], [RUNTIME_TAG])
           and legacy_row.get("Parent") in (None, ""),
           "export manifest fields/tags")
    legacy_config = safe_path(legacy_row.get("Config"))
    legacy_layers = legacy_row.get("Layers")
    refuse(isinstance(legacy_layers, list)
           and len(legacy_layers) == len(expected["runtime_layers"]), "export layer count")
    config_member = members.get(legacy_config)
    config_bytes = config_member.get("bytes") if isinstance(config_member, dict) else None
    refuse(isinstance(config_member, dict) and config_member.get("sha256") == runtime_id
           and type(config_bytes) is int,
           "export config identity")
    for name, required in zip(legacy_layers, expected["runtime_layers"], strict=True):
        name = safe_path(name)
        refuse(members.get(name) == {"bytes": required["bytes"], "sha256": required["sha256"]},
               "export ordered layer identity")
    refuse("urls" not in json.dumps(legacy_row.get("LayerSources", {})).lower(),
           "foreign export layer")

    if "index.json" not in metadata:
        # Legacy Docker-save archives have no OCI directory.  Reject every
        # unreferenced regular member, while permitting the conventional
        # compatibility files and zero-length directory entries.
        referenced = {"manifest.json", legacy_config, *legacy_layers}
        refuse(all(name in referenced or name in {"repositories", "oci-layout"}
                   or row.get("directory") for name, row in members.items()),
               "unexpected legacy export content/encoding")
        return

    index = strict_json(metadata["index.json"])
    refuse(isinstance(index, dict) and index.get("schemaVersion") == 2
           and index.get("mediaType") == "application/vnd.oci.image.index.v1+json"
           and isinstance(index.get("manifests"), list)
           and len(index["manifests"]) == 1, "OCI index shape")
    descriptor, manifest_size = _descriptor(
        index["manifests"][0], label="OCI manifest",
        media_types={"application/vnd.oci.image.manifest.v1+json"})
    platform = index["manifests"][0].get("platform")
    if platform is not None:
        refuse(isinstance(platform, dict) and platform.get("os") == "linux"
               and platform.get("architecture") == "arm64", "OCI platform")
    manifest_name = _OCI_BLOB_PREFIX + descriptor
    manifest_raw = metadata.get(manifest_name, b"")
    refuse(len(manifest_raw) == manifest_size
           and members.get(manifest_name) == {"bytes": manifest_size, "sha256": descriptor},
           "OCI manifest blob")
    image_manifest = strict_json(manifest_raw)
    refuse(isinstance(image_manifest, dict)
           and image_manifest.get("schemaVersion") == 2
           and image_manifest.get("mediaType") == "application/vnd.oci.image.manifest.v1+json",
           "OCI image manifest shape")
    config_digest, config_size = _descriptor(
        image_manifest.get("config"), label="OCI config",
        media_types={"application/vnd.oci.image.config.v1+json"})
    refuse(config_digest == runtime_id and config_size == config_bytes
           and legacy_config == _OCI_BLOB_PREFIX + runtime_id,
           "OCI config descriptor")
    config_document = strict_json(metadata[legacy_config])
    refuse(isinstance(config_document, dict)
           and config_document.get("os") == "linux"
           and config_document.get("architecture") == "arm64",
           "OCI config platform")
    layers = image_manifest.get("layers")
    refuse(isinstance(layers, list) and len(layers) == len(expected["runtime_layers"]),
           "OCI layer count")
    for descriptor_value, required in zip(layers, expected["runtime_layers"], strict=True):
        layer_digest, layer_size = _descriptor(
            descriptor_value, label="OCI layer",
            media_types={"application/vnd.oci.image.layer.v1.tar",
                         "application/vnd.docker.image.rootfs.diff.tar"})
        refuse(layer_digest == required["sha256"] and layer_size == required["bytes"],
               "OCI ordered layer descriptor")
        layer_name = _OCI_BLOB_PREFIX + layer_digest
        refuse(members.get(layer_name) == {"bytes": layer_size, "sha256": layer_digest},
               "OCI layer blob")
    layout = strict_json(metadata.get("oci-layout", b""))
    refuse(isinstance(layout, dict) and set(layout) == {"imageLayoutVersion"}
           and layout["imageLayoutVersion"] == "1.0.0", "OCI layout version")
    referenced = {"manifest.json", "index.json", "oci-layout", "repositories",
                  legacy_config, manifest_name,
                  *[_OCI_BLOB_PREFIX + row["sha256"] for row in expected["runtime_layers"]]}
    auxiliary = [name for name, row in members.items()
                 if name.startswith(_OCI_BLOB_PREFIX) and name not in referenced
                 and not row.get("directory")]
    refuse(len(auxiliary) == len(expected["runtime_layers"]), "V1 auxiliary count")
    _validate_v1_auxiliary(members, metadata, auxiliary, config_document,
                           len(expected["runtime_layers"]))
    refuse(all(name in referenced or name in auxiliary or row.get("directory")
               for name, row in members.items()),
           "unexpected OCI export content")


def _validate_v1_auxiliary(members: dict[str, dict[str, Any]], metadata: dict[str, bytes],
                           names: list[str], config_document: dict[str, Any],
                           layer_count: int) -> None:
    """Check Moby's bounded legacy V1 config chain emitted per layer."""
    allowed = {"id", "parent", "comment", "created", "container", "container_config",
               "docker_version", "author", "config", "architecture", "variant", "os", "size"}
    records: dict[str, dict[str, Any]] = {}
    for name in names:
        raw = metadata.get(name, b"")
        refuse(bool(raw), "V1 auxiliary metadata")
        digest_value = name.removeprefix(_OCI_BLOB_PREFIX)
        refuse(len(digest_value) == 64 and all(ch in "0123456789abcdef" for ch in digest_value)
               and members[name].get("sha256") == digest_value, "V1 auxiliary identity")
        record = strict_json(raw)
        refuse(isinstance(record, dict) and set(record) <= allowed, "V1 auxiliary fields")
        identifier, parent = record.get("id"), record.get("parent", "")
        refuse(isinstance(identifier, str) and len(identifier) == 64
               and all(ch in "0123456789abcdef" for ch in identifier)
               and isinstance(parent, str) and (not parent or len(parent) == 64)
               and (not parent or all(ch in "0123456789abcdef" for ch in parent)),
               "V1 auxiliary chain identity")
        refuse(identifier not in records and identifier != parent, "V1 auxiliary duplicate/loop")
        created = record.get("created")
        refuse(isinstance(created, str), "V1 auxiliary created")
        try:
            datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            refuse(False, "V1 auxiliary created format")
        refuse(record.get("os", "linux") == "linux"
               and record.get("architecture", "arm64") == "arm64",
               "V1 auxiliary platform")
        refuse(not any(key in record for key in ("rootfs", "manifests", "layers", "urls")),
               "V1 auxiliary image graph")
        records[identifier] = record
    roots = [identifier for identifier, record in records.items()
             if not record.get("parent", "")]
    refuse(len(roots) == 1, "V1 auxiliary root")
    children = {record["parent"] for record in records.values() if record.get("parent")}
    tips = [identifier for identifier in records if identifier not in children]
    refuse(len(tips) == 1 and len(records) <= layer_count, "V1 auxiliary chain")
    current = roots[0]
    visited: list[str] = []
    while current:
        refuse(current in records and current not in visited, "V1 auxiliary chain continuity")
        visited.append(current)
        current = next((identifier for identifier, record in records.items()
                        if record.get("parent", "") == current), "")
    refuse(len(visited) == len(records) and visited[-1] in tips, "V1 auxiliary chain closure")
    for identifier in visited[:-1]:
        refuse(records[identifier].get("created") == "1970-01-01T00:00:00Z",
               "V1 auxiliary synthetic epoch")
    final = records[visited[-1]]
    if "created" in config_document:
        refuse(final.get("created") == config_document["created"], "V1 final creation time")
    final_config = final.get("config")
    expected_config = config_document.get("config")
    refuse(isinstance(final_config, dict) and isinstance(expected_config, dict),
           "V1 final config")
    final_config = cast(dict[str, Any], final_config)
    expected_config = cast(dict[str, Any], expected_config)
    # Moby unmarshals the image's compact config into container.Config before
    # reserializing V1 compatibility metadata. That adds known zero fields.
    zero_fields = {"Hostname": "", "Domainname": "", "User": "", "Image": "",
        "WorkingDir": "", "AttachStdin": False, "AttachStdout": False,
        "AttachStderr": False, "Tty": False, "OpenStdin": False, "StdinOnce": False,
        "NetworkDisabled": False, "ArgsEscaped": False, "MacAddress": "",
        "Healthcheck": None, "StopTimeout": None, "Shell": None, "OnBuild": None,
        "ExposedPorts": None, "Volumes": None, "Env": None, "Cmd": None,
        "Entrypoint": None, "Labels": None, "StopSignal": ""}
    refuse(all(key in final_config and final_config[key] == value
               for key, value in expected_config.items()), "V1 final config agreement")
    refuse(all(key in expected_config or (key in zero_fields
               and type(value) is type(zero_fields[key]) and value == zero_fields[key])
               for key, value in final_config.items()), "V1 unexpected config field")


def export_runtime(container_id: str, destination: Path, expected: dict[str, Any],
                   meter: Allocation) -> dict[str, Any]:
    meter.charge("runtime_exports", 1)
    deadline = time.monotonic() + 300
    stderr_path = destination.with_suffix(".stderr")
    with destination.open("xb") as target, stderr_path.open("xb") as error:
        destination.chmod(0o600)
        stderr_path.chmod(0o600)
        child = subprocess.Popen(["docker", "exec", container_id, "docker", "image", "save",
                                  expected["runtime_config"]], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = child.stdout, child.stderr
        if stdout is None or stderr is None:
            raise RuntimeError("pgvector prebuilt refused: export pipe")
        stream = RetainedStream(stdout, target, meter, deadline, stderr, error)
        try:
            inspect_export(stream, expected)
            refuse(child.wait(timeout=max(0.1, deadline - time.monotonic())) == 0,
                   "runtime export command")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            stdout.close()
            stderr.close()
    return {"bytes": stream.count, "sha256": stream.hasher.hexdigest(),
            "runtime_config": expected["runtime_config"], "validated_ordered_layers": True}
