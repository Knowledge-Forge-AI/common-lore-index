"""Public synthetic Docker-save layouts for the bounded export parser tests.

The rows are tar members, not image or package payloads.  They model both the
legacy ``manifest.json`` form and the OCI index/image-manifest form emitted by
modern Docker engines.  No member is executed or extracted by this helper.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
             allow_nan=False) + "\n").encode()


def legacy_export_members(config: bytes, layers: list[bytes], *, tag: str | None = None,
                          mutation: str | None = None) -> list[tuple[str, bytes, str]]:
    """Return one synthetic pre-25 Docker-save member set."""
    config_name = _digest(config) + ".json"
    layer_names = [f"{index}/layer.tar" for index in range(len(layers))]
    manifest: dict[str, Any] = {"Config": config_name, "Layers": layer_names,
                                "RepoTags": [] if tag is None else [tag]}
    if mutation == "foreign-url":
        manifest["LayerSources"] = {name: {"urls": ["https://example.invalid/layer"]}
                                     for name in layer_names}
    if mutation == "config-name":
        manifest["Config"] = "wrong.json"
    rows: list[tuple[str, bytes, str]] = [("manifest.json", _json([manifest]), "file"),
                                          (config_name, config, "file")]
    rows.extend((name, layer, "file") for name, layer in zip(layer_names, layers, strict=True))
    if mutation == "extra-file":
        rows.append(("unrelated.bin", b"unrelated", "file"))
    return rows


def oci_export_members(config: bytes, layers: list[bytes], *, tag: str | None = None,
                       mutation: str | None = None) -> list[tuple[str, bytes, str]]:
    """Return a modern Docker-save OCI layout with one linux/arm64 image."""
    config_digest = _digest(config)
    layer_digests = [_digest(layer) for layer in layers]
    config_blob = b"tampered-config" if mutation == "config-content" else config
    layer_names = [_blob_path(digest) for digest in layer_digests]
    config_name = _blob_path(config_digest)
    image_manifest: dict[str, Any] = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": "sha256:" + config_digest, "size": len(config)},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": "sha256:" + digest, "size": len(layer)}
                   for digest, layer in zip(layer_digests, layers, strict=True)],
    }
    if mutation == "layer-digest" and image_manifest["layers"]:
        image_manifest["layers"][0]["digest"] = "sha256:" + "0" * 64
    if mutation == "layer-size" and image_manifest["layers"]:
        image_manifest["layers"][0]["size"] += 1
    if mutation == "layer-order" and len(image_manifest["layers"]) > 1:
        image_manifest["layers"] = list(reversed(image_manifest["layers"]))
    if mutation == "foreign-url" and image_manifest["layers"]:
        image_manifest["layers"][0]["urls"] = ["https://example.invalid/layer"]
    if mutation == "config-digest":
        image_manifest["config"]["digest"] = "sha256:" + "0" * 64
    if mutation == "config-size":
        image_manifest["config"]["size"] += 1
    if mutation == "manifest-content":
        image_manifest["mediaType"] = "application/vnd.oci.image.index.v1+json"
    image_manifest_raw = _json(image_manifest)
    image_manifest_digest = _digest(image_manifest_raw)
    manifest_name = _blob_path(image_manifest_digest)
    manifest_blob = b"tampered" if mutation == "manifest-blob" else image_manifest_raw
    index: dict[str, Any] = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "digest": "sha256:" + image_manifest_digest,
                       "size": len(image_manifest_raw),
                       "platform": {"os": "linux", "architecture": "arm64"}}],
    }
    if mutation == "platform":
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "amd64"}
    elif mutation in {"omit-platform", "moby-defaults"}:
        index["manifests"][0].pop("platform")
    if mutation == "manifest-size":
        index["manifests"][0]["size"] += 1
    if mutation == "duplicate-index":
        index["manifests"].append(dict(index["manifests"][0]))
    legacy = {"Config": config_name, "Layers": layer_names,
              "RepoTags": [] if tag is None else [tag]}
    layout_version = "1.1.0" if mutation == "oci-layout-version" else "1.0.0"
    rows = [("manifest.json", _json([legacy]), "file"),
            ("index.json", _json(index), "file"),
            ("oci-layout", _json({"imageLayoutVersion": layout_version}), "file"),
            (manifest_name, manifest_blob, "file"),
            (config_name, config_blob, "file")]
    rows.extend((name, layer, "file") for name, layer in zip(layer_names, layers, strict=True))
    auxiliary_ids = ["%064x" % (index + 1) for index in range(len(layers))]
    for position, identifier in enumerate(auxiliary_ids):
        record: dict[str, Any] = {"id": identifier,
                                  "created": "1970-01-01T00:00:00Z" if position + 1 < len(layers)
                                             else json.loads(config).get("created", "2024-01-01T00:00:00Z"),
                                  "os": "linux", "architecture": "arm64",
                                  "config": json.loads(config).get("config", {})}
        if position:
            record["parent"] = auxiliary_ids[position - 1]
        if mutation == "auxiliary-chain" and position == len(layers) - 1:
            record["parent"] = "f" * 64
        elif mutation == "moby-defaults" and position == len(layers) - 1:
            record["config"].update(Hostname="", User="", AttachStdin=False, WorkingDir="")
            record.pop("architecture")
        elif mutation == "auxiliary-platform" and position == 0:
            record["architecture"] = "amd64"
        elif mutation == "auxiliary-metadata" and position == 0:
            record["rootfs"] = {"type": "layers"}
        aux_raw = _json(record)
        rows.append((_blob_path(_digest(aux_raw)), aux_raw, "file"))
    if mutation == "extra-blob":
        rows.append((_blob_path("f" * 64), b"extra", "file"))
    if mutation == "auxiliary-extra":
        extra = _json({"id": "e" * 64, "created": "1970-01-01T00:00:00Z", "os": "linux",
                       "architecture": "arm64", "config": {}})
        rows.append((_blob_path(_digest(extra)), extra, "file"))
    return rows


def _blob_path(digest: str) -> str:
    return "blobs/sha256/" + digest
