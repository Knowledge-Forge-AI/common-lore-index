"""Offline two-image Docker archive; only verified blobs and one selected layer."""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import os
from pathlib import Path
import struct
import tarfile
import time
from typing import Any, BinaryIO

from runner_pgvector_budget import Allocation, MAX_ARCHIVE, MAX_SELECTED, refuse
from runner_pgvector_prebuilt import (
    BASE_CONFIG, BASE_MANIFEST, BASE_TAG, OWNER_LABEL, OWNER_VALUE, PACKAGE_SHA256,
    PROVIDER_LABEL, RECIPE_LABEL, RUNTIME_TAG, digest, json_bytes, provider_facts,
    read_regular, recipe_digest, safe_path, selected_payloads, strict_object,
)

BLOCK = 1024 * 1024


class ArchiveWriter:
    """Append bounded deterministic USTAR members while hashing the whole output."""

    def __init__(self, destination: Path) -> None:
        self.stream = destination.open("xb")
        destination.chmod(0o600)
        self.count = 0
        self.hasher = hashlib.sha256()
        self.deadline = time.monotonic() + 300

    def write(self, raw: bytes) -> None:
        refuse(time.monotonic() < self.deadline, "archive conversion timeout")
        refuse(self.count + len(raw) <= MAX_ARCHIVE, "archive output cap")
        self.stream.write(raw)
        self.hasher.update(raw)
        self.count += len(raw)

    def header(self, name: str, size: int, *, directory: bool = False) -> None:
        info = tarfile.TarInfo(name)
        info.size = size
        info.uid = info.gid = info.mtime = 0
        info.mode = 0o755 if directory else 0o644
        info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        self.write(info.tobuf(format=tarfile.USTAR_FORMAT))

    def padding(self, size: int) -> None:
        self.write(b"\0" * (-size % 512))

    def regular(self, name: str, raw: bytes) -> None:
        self.header(name, len(raw))
        self.write(raw)
        self.padding(len(raw))

    def finish(self) -> dict[str, Any]:
        self.write(b"\0" * (1024 + (-(self.count + 1024) % 10240)))
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        return {"bytes": self.count, "sha256": self.hasher.hexdigest()}


def overlay_bytes(files: dict[str, bytes]) -> bytes:
    refuse(sum(map(len, files.values())) <= MAX_SELECTED, "selected content cap")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        directory = tarfile.TarInfo("usr/share/doc/postgresql-16-pgvector")
        directory.type, directory.mode = tarfile.DIRTYPE, 0o755
        directory.uid = directory.gid = directory.mtime = 0
        archive.addfile(directory)
        for name, raw in sorted(files.items()):
            safe_path(name)
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), 0o644
            info.uid = info.gid = info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


class CompressedReader(io.RawIOBase):
    """Hash and charge every compressed byte consumed by the gzip decoder."""

    def __init__(self, source: BinaryIO, meter: Allocation, maximum: int) -> None:
        super().__init__()
        self.source, self.meter, self.maximum = source, meter, maximum
        self.hasher = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        refuse(0 <= size <= BLOCK, "unbounded compressed read")
        if self.count == self.maximum or size == 0:
            return b""
        count = min(size, self.maximum - self.count, self.meter.remaining("read_bytes"))
        refuse(count > 0, "compressed read allocation exhausted")
        raw = self.meter.read(self.source.read, count)
        refuse(bool(raw), "short compressed input")
        self.count += len(raw)
        refuse(self.count <= self.maximum, "compressed byte count")
        self.hasher.update(raw)
        return raw


def _copy_layer(writer: ArchiveWriter, path: Path, row: dict[str, Any],
                meter: Allocation) -> dict[str, Any]:
    refuse(not path.is_symlink() and path.stat().st_size == row["size"], "layer size/custody")
    with path.open("rb") as source:
        source.seek(-4, os.SEEK_END)
        footer = meter.read(source.read, 4)
        refuse(len(footer) == 4, "gzip footer")
        size, = struct.unpack("<I", footer)
        refuse(size <= MAX_ARCHIVE, "layer decoded cap")
        source.seek(0)
        compressed = CompressedReader(source, meter, row["size"])
        observed = hashlib.sha256()
        count = 0
        name = "layers/" + row["diff_id"].removeprefix("sha256:") + ".tar"
        writer.header(name, size)
        with gzip.GzipFile(fileobj=compressed) as decoded:
            while True:
                requested = min(BLOCK, size + 1 - count, meter.remaining("read_bytes"))
                refuse(requested > 0, "decoded read allocation exhausted")
                block = meter.read(decoded.read, requested)
                if not block:
                    break
                count += len(block)
                refuse(count <= size, "gzip size mismatch")
                writer.write(block)
                observed.update(block)
        refuse(count == size and "sha256:" + observed.hexdigest() == row["diff_id"],
               "layer diffID")
        refuse(compressed.count == row["size"] and
               "sha256:" + compressed.hasher.hexdigest() == row["digest"], "compressed digest")
        writer.padding(size)
        return {"name": name, "bytes": size, "sha256": observed.hexdigest()}


def runtime_config_for_diff_id(base: dict[str, Any], overlay_diff_id: str) -> dict[str, Any]:
    result = copy.deepcopy(base)
    refuse((base["os"], base["architecture"]) == ("linux", "arm64")
           and base["config"].get("Cmd") == ["postgres"], "base platform/command")
    result["created"] = "1970-01-01T00:00:00Z"
    result["rootfs"]["diff_ids"].append(overlay_diff_id)
    result.setdefault("history", []).append({"created": "1970-01-01T00:00:00Z",
        "created_by": "common-lore-index offline selected PGDG provider"})
    labels = result["config"].setdefault("Labels", {}) or {}
    labels.update({OWNER_LABEL: OWNER_VALUE, PROVIDER_LABEL: PACKAGE_SHA256,
                   RECIPE_LABEL: recipe_digest(), "org.repomap.test.profile": "pgvector"})
    result["config"]["Labels"] = labels
    return result


def derive_archive(base_root: Path, bundle: Path, destination: Path,
                   meter: Allocation) -> dict[str, Any]:
    """One counted materialization; failed construction never releases its slot."""
    meter.charge("materializations", 1)
    facts = provider_facts()
    manifest_raw = read_regular(base_root / "manifest.json", 65536, meter)
    refuse("sha256:" + digest(manifest_raw) == BASE_MANIFEST, "base registry manifest")
    manifest = strict_object(manifest_raw)
    refuse(manifest["config"]["digest"] == BASE_CONFIG
           and manifest["layers"] == [{k: row[k] for k in ("mediaType", "size", "digest")}
                                      for row in facts["base_layers"]], "base layer descriptors")
    config_raw = read_regular(base_root / "config.json", 65536, meter)
    refuse("sha256:" + digest(config_raw) == BASE_CONFIG, "base config digest")
    base = strict_object(config_raw)
    refuse(base["rootfs"]["diff_ids"] == [row["diff_id"] for row in facts["base_layers"]],
           "base ordered diffIDs")
    selected = selected_payloads(bundle, meter)
    overlay = overlay_bytes(selected)
    runtime = runtime_config_for_diff_id(base, "sha256:" + digest(overlay))
    runtime_raw = json_bytes(runtime)
    runtime_id = "sha256:" + digest(runtime_raw)
    names = ["layers/" + row["diff_id"].removeprefix("sha256:") + ".tar"
             for row in facts["base_layers"]]
    refuse(len(set(names)) == len(names), "repeated layer inventory")
    overlay_name = "layers/" + digest(overlay) + ".tar"
    archive_manifest: list[dict[str, Any]] = [
        {"Config": BASE_CONFIG.removeprefix("sha256:") + ".json",
         "RepoTags": [BASE_TAG], "Layers": names},
        {"Config": runtime_id.removeprefix("sha256:") + ".json",
         "RepoTags": [RUNTIME_TAG], "Layers": [*names, overlay_name]},
    ]
    writer = ArchiveWriter(destination)
    try:
        writer.regular("manifest.json", json_bytes(archive_manifest))
        writer.regular(archive_manifest[0]["Config"], config_raw)
        writer.regular(archive_manifest[1]["Config"], runtime_raw)
        layers = [_copy_layer(writer, base_root / f"layer-{index:02d}.tar.gz", row, meter)
                  for index, row in enumerate(facts["base_layers"])]
        writer.regular(overlay_name, overlay)
        summary = writer.finish()
    except BaseException:
        writer.stream.close()
        raise
    result = {"schema": "cloe-pgvector-derived-archive-v1", **summary,
        "base_config": BASE_CONFIG, "runtime_config": runtime_id,
        "runtime_config_document": runtime, "base_config_document": base,
        "base_config_raw": config_raw.decode("utf-8"),
        "base_layers": layers,
        "runtime_layers": [*layers, {"name": overlay_name, "bytes": len(overlay),
                                    "sha256": digest(overlay)}],
        "entrypoint": base["config"].get("Entrypoint"),
        "selected_file_count": len(selected), "selected_file_bytes": sum(map(len, selected.values())),
        "native_abi_or_loading_proven": False}
    meter.bind("derived_archive", result)
    return result
