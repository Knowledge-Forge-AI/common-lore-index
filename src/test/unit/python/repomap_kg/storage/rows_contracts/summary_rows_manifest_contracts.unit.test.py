from __future__ import annotations

import os
from pathlib import Path

import pytest

from common_lore_index.storage._summary_rows_api_manifest import iter_run_manifest_payloads


def test_bulk_manifest_summary_payload_read_bulk_manifest_payloads_api_manifest_summary_payload_read_api_manifest_payloads_bulk_summary_from_storage_payload_api_summary_from_storage_payload_manifest_records_contracts(
    tmp_path,
) -> None:
    import common_lore_index.storage.summary_rows_manifest as summary_rows_manifest
    bulk_manifest = tmp_path / ".repomap" / "bulk-runs" / "source" / "run" / "manifest.json"
    bulk_manifest.parent.mkdir(parents=True)
    bulk_manifest.write_text(
        """{
            "bulk_run_id": "bulk-1",
            "source_id": "source-1",
            "corpus_kind": "email_export",
            "policy_status": "accepted",
            "file_count_included": 3,
            "file_count_skipped": 2,
            "total_bytes_included": 123,
            "extractor_counts": {"python": 1},
            "diagnostic_counts": {"policy": 2},
            "redaction_counts": {"private_path": 1},
            "skipped_files": [false, {"reason": "archive_deferred"}, null],
            "limit_hit": true,
            "limit_reason": "max_files_exceeded,max_depth_exceeded"
        }""",
        encoding="utf-8",
    )
    bad_bulk_manifest = (
        tmp_path / ".repomap" / "bulk-runs" / "source" / "bad" / "manifest.json"
    )
    bad_bulk_manifest.parent.mkdir(parents=True)
    bad_bulk_manifest.write_text("[not an object]", encoding="utf-8")

    bulk_payloads = summary_rows_manifest.read_bulk_manifest_payloads(
        tmp_path / ".repomap" / "bulk-runs",
        root_path=tmp_path.resolve(),
    )
    assert len(bulk_payloads) == 2

    bulk_summary_payload = summary_rows_manifest.bulk_manifest_summary_payload(tmp_path)
    assert bulk_summary_payload["bulk_runs"] == 1
    assert bulk_summary_payload["sources"] == 1
    assert bulk_summary_payload["source_ids"] == ["source-1"]
    assert bulk_summary_payload["file_count_included"] == 3
    assert bulk_summary_payload["redaction_counts"] == {"private_path": 1}
    assert bulk_summary_payload["diagnostic_counts"] == {
        "manifest_parse_error": 1,
        "policy": 2,
    }
    assert bulk_summary_payload["archive_deferred"] == 1
    assert bulk_summary_payload["limit_hit_count"] == 1
    assert bulk_summary_payload["max_files_hit_count"] == 1
    assert bulk_summary_payload["max_depth_hit_count"] == 1

    bulk_summary = summary_rows_manifest.bulk_summary_from_storage_payload(
        bulk_summary_payload
    )
    assert bulk_summary.to_dict()["source_ids"] == ("source-1",)
    assert bulk_summary.to_dict()["no_external_fetch"] is True

    api_manifest = tmp_path / ".repomap" / "api-runs" / "source" / "run" / "manifest.json"
    api_manifest.parent.mkdir(parents=True)
    api_manifest.write_text(
        """{
            "api_run_id": "api-1",
            "source_id": "source-1",
            "source_type": "github.api",
            "api_source_class": "github",
            "provider_name": "github",
            "provider_product": "issues",
            "policy_status": "accepted",
            "no_network": false,
            "no_mutation": false,
            "no_credentials_resolved": false,
            "no_scheduler": false,
            "requests": [null, {
                "endpoint_name": "repo",
                "method": "GET",
                "downstream_route": "json",
                "response_type": "application/json"
            }],
            "responses": [false, {
                "endpoint_name": "repo",
                "response_byte_count": 12,
                "redacted": true,
                "artifact_path": "artifact.json"
            }]
        }""",
        encoding="utf-8",
    )
    bad_api_manifest = (
        tmp_path / ".repomap" / "api-runs" / "source" / "bad" / "manifest.json"
    )
    bad_api_manifest.parent.mkdir(parents=True)
    bad_api_manifest.write_text("not-json", encoding="utf-8")

    api_payloads = summary_rows_manifest.read_api_manifest_payloads(
        tmp_path / ".repomap" / "api-runs",
        root_path=tmp_path.resolve(),
    )
    assert len(api_payloads) == 2

    api_summary_payload = summary_rows_manifest.api_manifest_summary_payload(tmp_path)
    assert api_summary_payload["api_runs"] == 1
    assert api_summary_payload["sources"] == 1
    assert api_summary_payload["endpoint_names"] == ["repo"]
    assert api_summary_payload["methods"] == {"GET": 1}
    assert api_summary_payload["requests"] == api_summary_payload["responses"] == 1
    assert api_summary_payload["redacted_responses"] == 1
    assert api_summary_payload["diagnostic_counts"] == {"manifest_parse_error": 1}
    assert api_summary_payload["no_network"] is False
    assert api_summary_payload["no_mutation"] is False
    assert api_summary_payload["no_credentials_resolved"] is False
    assert api_summary_payload["no_scheduler"] is False

    api_summary = summary_rows_manifest.api_summary_from_storage_payload(
        api_summary_payload
    )
    assert api_summary.to_dict()["source_ids"] == ("source-1",)
    assert api_summary.to_dict()["no_provider_specific_behavior"] is True


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires a named pipe")
def test_retained_manifest_scan_refuses_a_pipe_without_reading_its_body(tmp_path, monkeypatch):
    run_root = tmp_path / "api-runs"
    pipe = run_root / "source" / "pipe" / "manifest.json"
    pipe.parent.mkdir(parents=True)
    os.mkfifo(pipe)
    canonical_pipe = pipe.resolve()
    nonobject = run_root / "source" / "not-object" / "manifest.json"
    nonobject.parent.mkdir()
    nonobject.write_text("[]", encoding="utf-8")
    regular = run_root / "source" / "regular" / "manifest.json"
    regular.parent.mkdir()
    regular.write_text('{"source_id":"fixture"}', encoding="utf-8")
    original = Path.open
    open_descriptor = os.open
    open_text = os.fdopen
    pipe_identity = pipe.stat()

    def guarded_open(path, *args, **kwargs):
        assert path != canonical_pipe, "refused FIFO must never reach text I/O"
        return original(path, *args, **kwargs)

    def guarded_descriptor(path, flags, *args, **kwargs):
        if path == canonical_pipe:
            assert flags & os.O_NONBLOCK, "retained manifest descriptors must not block on a pipe"
        return open_descriptor(path, flags, *args, **kwargs)

    def guarded_fdopen(descriptor, *args, **kwargs):
        identity = os.fstat(descriptor)
        assert (identity.st_dev, identity.st_ino) != (pipe_identity.st_dev, pipe_identity.st_ino)
        return open_text(descriptor, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_descriptor)
    monkeypatch.setattr(os, "fdopen", guarded_fdopen)
    observed = iter_run_manifest_payloads(run_root, root_path=tmp_path.resolve())
    assert next(observed) == {"_manifest_parse_error": True}
    assert next(observed) == {"_manifest_parse_error": True}
    assert next(observed) == {"source_id": "fixture"}
    assert not list(observed)


def test_retained_manifest_reader_fetches_each_path_only_as_the_consumer_advances(tmp_path):
    run_root = tmp_path / "bulk-runs"
    paths = [run_root / "source" / name / "manifest.json" for name in ("a", "b")]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_text('{"source_id":"fixture"}', encoding="utf-8")
    observed = iter_run_manifest_payloads(run_root, root_path=tmp_path.resolve())
    assert next(observed) == {"source_id": "fixture"}
    paths[1].write_text('{"source_id":"updated"}', encoding="utf-8")
    assert tuple(observed) == ({"source_id": "updated"},)

def test_manifest_root_probe_error_keeps_its_fatal_disposition(tmp_path, monkeypatch):
    root = tmp_path / "api-runs"
    probe = Path.is_dir

    def is_dir(path):
        if path == root:
            raise PermissionError("fixture denied the root probe")
        return probe(path)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    with pytest.raises(PermissionError, match="fixture denied the root probe"):
        tuple(iter_run_manifest_payloads(root, root_path=tmp_path))
