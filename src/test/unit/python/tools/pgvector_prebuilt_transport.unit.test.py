"""Data-only archive, custody, accounting and exact-cohort adverse contracts."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest

import runner_pgvector_context as contexts
import runner_pgvector_transport as transport
from runner_pgvector_archive import overlay_bytes
from runner_pgvector_budget import CAPS
from runner_pgvector_context import CORE_SELECTOR, NATIVE_SELECTOR, validate_cohort, verify_checkout
from runner_pgvector_export import RetainedStream, inspect_export
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN, digest, json_bytes, provider_facts, strict_object
from runner_pgvector_qualification import bounded_native_run
from test_sandbox_contract import PGVECTOR_OFFLINE_PLAN_ENV, PGVECTOR_QUALIFICATION_SDK_IMAGE


from common_lore_index_test_support.pgvector_prebuilt_test_fixtures import (
    allocation, tar_bytes, synthetic_construction, sender_case, offline_probe_case,
)


def test_contract_pins_and_manifest_close_exact_provider_class():
    facts = provider_facts()
    assert PGVECTOR_QUALIFICATION_SDK_IMAGE == (
        "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42")
    assert PGVECTOR_OFFLINE_PLAN_ENV == ENV_OFFLINE_PLAN == "CLOE_PGVECTOR_OFFLINE_PLAN"
    assert facts["package_sha256"] == "fa7c26fe2f076538915fa7efac6cd944777d891ccb251324bea01fda35626919"
    assert len(facts["files"]) == 45 and sum(row["bytes"] for row in facts["files"]) == 287502
    assert not facts["production_admission"] and not facts["scientific_acceptance"]
    assert (CAPS["read_bytes"], CAPS["transfer_bytes"]) == (12 * 1024**3, 6 * 1024**3)
    assert (CAPS["materializations"], CAPS["sdk_core"], CAPS["sdk_native"]) == (1, 3, 1)
    assert not any(row["path"].endswith(".bc") for row in facts["files"])


def test_failure_persists_reservations_and_never_reopens_attempt_slot(tmp_path):
    budget = allocation(tmp_path)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with budget.session() as meter:
            meter.charge("sdk_core", CAPS["sdk_core"])
            raise RuntimeError("synthetic failure")
    with budget.session() as meter:
        assert meter.used["sdk_core"] == CAPS["sdk_core"]
        with pytest.raises(RuntimeError, match="exhausted"):
            meter.charge("sdk_core", 1)
    assert json.loads(budget.path.read_bytes())["used"]["sdk_core"] == CAPS["sdk_core"]


def test_only_proven_unread_bytes_are_reconciled(tmp_path):
    with allocation(tmp_path).session() as meter:
        assert meter.read(lambda count: b"ab", 10, transfer=True) == b"ab"
        assert meter.used["read_bytes"] == meter.used["transfer_bytes"] == 2
        def failed(count):
            raise OSError("synthetic read failure")
        with pytest.raises(OSError):
            meter.read(failed, 7)
        assert meter.used["read_bytes"] == 9
        assert meter.used["transfer_bytes"] == 2
        with pytest.raises(RuntimeError, match="exhausted"):
            meter.charge("read_bytes", CAPS["read_bytes"])


def test_evidence_records_are_write_once_and_survive_failure(tmp_path):
    budget = allocation(tmp_path)
    with pytest.raises(RuntimeError, match="synthetic"):
        with budget.session() as meter:
            meter.bind("core_result", {"exit": 1})
            raise RuntimeError("synthetic failure")
    with budget.session() as meter:
        assert meter.records["core_result"] == {"exit": 1}
        with pytest.raises(RuntimeError, match="already bound"):
            meter.bind("core_result", {"exit": 0})


@pytest.mark.parametrize("mutate", ["caps", "used", "link"])
def test_allocation_refuses_widening_malformed_state_and_links(tmp_path, mutate):
    budget = allocation(tmp_path)
    if mutate == "link":
        real = budget.path.with_suffix(".real")
        budget.path.rename(real)
        budget.path.symlink_to(real)
    else:
        value = json.loads(budget.path.read_bytes())
        value["caps" if mutate == "caps" else "used"]["materializations"] = (
            2 if mutate == "caps" else True)
        budget.path.write_bytes(json_bytes(value))
    with pytest.raises(RuntimeError):
        with budget.session():
            pass


def test_selected_layer_is_deterministic_data_only_and_rejects_escape():
    first = overlay_bytes({"usr/example/binary": b"\0\xff", "usr/example/sql": b"SELECT 1;"})
    second = overlay_bytes({"usr/example/sql": b"SELECT 1;", "usr/example/binary": b"\0\xff"})
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        files = [item for item in archive if item.isreg()]
        assert {item.name for item in files} == {"usr/example/binary", "usr/example/sql"}
        assert all(item.uid == item.gid == item.mtime == 0 and item.mode == 0o644 for item in files)
    with pytest.raises(RuntimeError, match="archive path"):
        overlay_bytes({"../escape": b"data"})


@pytest.mark.parametrize("role,selector,profile", [
    ("core", CORE_SELECTOR, "core"), ("native", NATIVE_SELECTOR, "pgvector"),
])
def test_only_exact_no_coverage_cohorts_are_admitted(role, selector, profile):
    argv = ["--suite", "int", "--no-coverage", "--pg-test-profile", profile,
            "--report-dir", "/public/synthetic-report", "--", selector]
    validate_cohort(argv, role)
    for bad in [
        argv[:-1] + [selector + "::partial"], argv[:-1] + ["src/test/int/python"],
        [x for x in argv if x != "--no-coverage"], ["--suite", "staging", *argv[2:]],
        [*argv[:2], "--threshold", "0", *argv[2:]],
    ]:
        with pytest.raises(RuntimeError):
            validate_cohort(bad, role)


def test_source_custody_checks_real_bytes_links_and_inventory(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_bytes(b"pass\n")
    (tmp_path / "a.py").chmod(0o644)
    (tmp_path / "alias").symlink_to("a.py")
    expected = {"a.py": {"sha256": digest(b"pass\n"), "mode": 0o644}, "alias": {"symlink": "a.py"}}
    def git(command, **kwargs):
        assert command[:3] == ["git", "-c", "safe.directory=" + str(tmp_path.resolve())]
        assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
        output = b"head\n" if "rev-parse" in command else b"a.py\0alias\0"
        return subprocess.CompletedProcess(command, 0, output, b"")
    monkeypatch.setattr(contexts.subprocess, "run", git)
    verify_checkout(tmp_path, expected, "head")
    (tmp_path / "a.py").write_bytes(b"changed\n")
    with pytest.raises(RuntimeError, match="source content drift"):
        verify_checkout(tmp_path, expected, "head")


def test_default_session_unchanged_and_sealed_cohort_refuses_scope_drift(monkeypatch):
    session = SimpleNamespace(config=SimpleNamespace(args=[CORE_SELECTOR]))
    monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    assert contexts.offline_session_owned_by_test(session) is False
    monkeypatch.setenv(ENV_OFFLINE_PLAN, "synthetic")
    monkeypatch.setattr(contexts, "load_inner_plan", lambda env: {"role": "core"})
    monkeypatch.setenv("REPOMAP_TEST_PG_PROFILE", "core")
    assert contexts.offline_session_owned_by_test(session)
    session.config.args = ["src/test/int/python/other.int.test.py"]
    with pytest.raises(RuntimeError, match="cohort drift"):
        contexts.offline_session_owned_by_test(session)


def test_unsupported_host_never_reaches_docker_and_native_metadata_is_required(monkeypatch):
    monkeypatch.setattr(contexts.platform, "machine", lambda: "x86_64")
    calls: list[list[str]] = []
    def rpc(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "linux/arm64\n", "")
    with pytest.raises(RuntimeError, match="native ARM host"):
        contexts.require_native_host(rpc)
    assert not calls
    monkeypatch.setattr(contexts.platform, "machine", lambda: "arm64")
    contexts.require_native_host(rpc)
    assert len(calls) == 1
    def amd(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "linux/amd64\n", "")
    with pytest.raises(RuntimeError, match="native ARM Docker"):
        contexts.require_native_host(amd)


def test_session_hook_starts_normal_context_and_avoids_extra_offline_cluster(monkeypatch):
    # Inspect under a non-conftest module name; never import it as a plugin.
    source = Path(__file__).resolve().parents[5] / "src/test/int/python/conftest.py"
    spec = importlib.util.spec_from_file_location("unit_offline_session_owner", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[str] = []
    class Context:
        def __enter__(self):
            calls.append("enter")
        def __exit__(self, *args):
            calls.append("exit")
    monkeypatch.setattr(module, "postgres_container_session", lambda **kwargs: Context())
    monkeypatch.setattr(contexts, "offline_session_owned_by_test", lambda session: False)
    module.pytest_sessionstart(SimpleNamespace())
    module.pytest_sessionfinish(None, 0)
    assert calls == ["enter", "exit"]
    calls.clear()
    monkeypatch.setattr(contexts, "offline_session_owned_by_test", lambda session: True)
    module.pytest_sessionstart(SimpleNamespace())
    module.pytest_sessionfinish(None, 0)
    assert calls == []


def test_duplicate_and_nonfinite_json_refused():
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(RuntimeError):
            strict_object(raw)


@pytest.mark.parametrize("mutation", ["none", "digest", "escape", "link", "duplicate", "trailer"])
def test_export_replay_is_hash_bound_and_never_extracts(tmp_path, mutation):
    layer, config = b"synthetic opaque rootfs layer", b'{"architecture":"arm64","os":"linux"}'
    expected = {"runtime_config": "sha256:" + digest(config),
                "runtime_layers": [{"bytes": len(layer), "sha256": digest(layer)}]}
    manifest = [{"Config": "config.json", "Layers": ["layer.tar"], "RepoTags": []}]
    rows = [("manifest.json", json_bytes(manifest), "file"), ("config.json", config, "file"),
            ("layer.tar", layer, "file")]
    if mutation == "digest":
        rows[-1] = ("layer.tar", b"wrong", "file")
    elif mutation == "escape":
        rows.append(("../escape", b"", "file"))
    elif mutation == "link":
        rows.append(("link", b"", "link"))
    elif mutation == "duplicate":
        rows.append(rows[0])
    raw = tar_bytes(rows) + (b"hidden" if mutation == "trailer" else b"")
    path = tmp_path / "input.tar"
    path.write_bytes(raw)
    with path.open("rb") as source, (tmp_path / "retained.tar").open("wb") as target:
        with allocation(tmp_path).session() as meter:
            stream = RetainedStream(source, target, meter, time.monotonic() + 5)
            if mutation == "none":
                inspect_export(stream, expected)
                assert stream.count == len(raw) and stream.hasher.hexdigest() == digest(raw)
            else:
                with pytest.raises(RuntimeError):
                    inspect_export(stream, expected)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("mutation", ["none", "short", "overlength", "digest"])
def test_receiver_reads_real_pipe_and_refuses_bad_identity(tmp_path, monkeypatch, capsys, mutation):
    root = tmp_path / "receiver"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(transport, "ROOT", root)
    monkeypatch.setattr(transport, "require_receiver_namespace", lambda: None)
    raw = b"exact-data"
    data = raw[:-1] if mutation == "short" else raw + b"x" if mutation == "overlength" else raw
    expected = digest(raw) if mutation != "digest" else "0" * 64
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as source:
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=source, fileno=source.fileno))
        if mutation == "none":
            transport.receive("image.tar", len(raw), expected)
            assert (root / "image.tar").read_bytes() == raw
            assert json.loads(capsys.readouterr().out) == {"bytes": len(raw), "sha256": digest(raw)}
        else:
            with pytest.raises(RuntimeError):
                transport.receive("image.tar", len(raw), expected)
            assert capsys.readouterr().out == ""


def test_native_diagnostic_command_capture_is_bounded():
    result = bounded_native_run([sys.executable, "-c", "print('ok')"], env={},
        capture_output=True, text=True, input=None, timeout=5, check=False)
    assert result.returncode == 0 and result.stdout == "ok\n"
    with pytest.raises(RuntimeError, match="output cap"):
        bounded_native_run([sys.executable, "-c", "print('x'*70000)"], env={},
            capture_output=True, text=True, input=None, timeout=5, check=False)


@pytest.mark.parametrize("mutation", ["none", "digest", "budget"])
def test_synthetic_archive_derivation_checks_real_layers_and_spends_failures(tmp_path, monkeypatch, mutation):
    synthetic_construction(tmp_path, monkeypatch, mutation)


@pytest.mark.parametrize("mutation", ["none", "source_digest", "receipt_digest", "receipt_cap", "exit"])
def test_binary_sender_proves_peer_identity_or_reaps_its_exact_child(tmp_path, monkeypatch, mutation):
    sender_case(tmp_path, monkeypatch, mutation)


def test_offline_boundary_preserves_real_probe_with_safe_public_input(monkeypatch):
    offline_probe_case(monkeypatch)


from common_lore_index_test_support.pgvector_prebuilt_budget_cases import (
    killed_child_reservation, stale_pending_state, duplicate_state, persistence_failure,
)
from common_lore_index_test_support.pgvector_prebuilt_launch_cases import declaration_case, launch_case, bundle_case
from common_lore_index_test_support.pgvector_prebuilt_stream_cases import export_command_case, parser_layout_case


def test_killed_owned_child_cannot_reopen_a_durable_attempt(tmp_path):
    assert killed_child_reservation(tmp_path)["used"]["materializations"] == 1


@pytest.mark.parametrize("case", ["pending", "duplicate", "replace", "fsync"])
def test_ambiguous_or_unpersistable_ledger_stops_without_reset(tmp_path, case):
    if case == "pending":
        stale_pending_state(tmp_path)
    elif case == "duplicate":
        duplicate_state(tmp_path)
    else:
        persistence_failure(tmp_path, case)


@pytest.mark.parametrize("mutation", ["none", "layout", "short", "exit", "stderr", "timeout"])
def test_export_command_reserves_durably_and_reaps_its_exact_pipe_peer(tmp_path, monkeypatch, mutation):
    export_command_case(tmp_path, monkeypatch, mutation)


@pytest.mark.parametrize("mutation", ["none", "source", "authority", "blocked", "caps", "zero"])
def test_reviewed_declaration_binds_actual_authority_bytes_and_source(tmp_path, monkeypatch, mutation):
    declaration_case(tmp_path, monkeypatch, mutation)


@pytest.mark.parametrize("mutation", ["none", "derive_failure", "missing_core", "core_failure",
    "cleanup", "candidate", "export_identity", "export_size", "native_spent"])
def test_reviewed_launcher_owns_materialization_and_core_before_native_plans(tmp_path, monkeypatch, mutation):
    launch_case(tmp_path, monkeypatch, mutation)


@pytest.mark.parametrize("mutation", ["none", "mapping", "payload", "package", "duplicate"])
def test_retained_payload_mapping_is_exact_and_never_installs_packages(tmp_path, monkeypatch, mutation):
    bundle_case(tmp_path, monkeypatch, mutation)


def test_collected_offline_node_owners_are_checked_after_discovery(monkeypatch):
    session = SimpleNamespace(config=SimpleNamespace(args=[CORE_SELECTOR]),
                              items=[SimpleNamespace(nodeid=CORE_SELECTOR + "::test_public")])
    monkeypatch.setenv(ENV_OFFLINE_PLAN, "synthetic")
    monkeypatch.setenv("REPOMAP_TEST_PG_PROFILE", "core")
    monkeypatch.setattr(contexts, "load_inner_plan", lambda env: {"role": "core"})
    contexts.verify_offline_collected_items(session)
    session.items = [SimpleNamespace(nodeid="unrelated.py::test_other")]
    with pytest.raises(RuntimeError, match="collected owner drift"):
        contexts.verify_offline_collected_items(session)
    session.items = []
    with pytest.raises(RuntimeError, match="cohort empty"):
        contexts.verify_offline_collected_items(session)


@pytest.mark.parametrize("mutation", ["none", "omit-platform", "moby-defaults", "layer-digest",
    "layer-size", "layer-order", "foreign-url", "config-digest", "config-size", "config-content",
    "manifest-content", "manifest-blob", "manifest-size", "duplicate-index", "platform",
    "oci-layout-version", "extra-blob", "auxiliary-chain", "auxiliary-platform", "auxiliary-metadata",
    "auxiliary-extra", "buffered-trailer"])
def test_modern_export_graph_and_moby_auxiliary_metadata_are_closed(tmp_path, mutation):
    parser_layout_case(tmp_path, "oci", mutation)


@pytest.mark.parametrize("mutation", ["none", "foreign-url", "extra-file", "config-name"])
def test_legacy_export_graph_remains_compatible_and_closed(tmp_path, mutation):
    parser_layout_case(tmp_path, "legacy", mutation)


from common_lore_index_test_support.pgvector_prebuilt_native_seam_cases import native_owner_case, report_seam_case, report_reader_case


def test_native_report_rewrite_stays_in_closed_cohort_and_exporter(tmp_path):
    report_seam_case(tmp_path)


def test_actual_native_owner_reloads_sealed_plan_without_a_fake_argv(tmp_path, monkeypatch):
    source = Path(contexts.__file__).resolve().parents[1] / NATIVE_SELECTOR
    spec = importlib.util.spec_from_file_location("unit_prebuilt_native_owner", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    native_owner_case(tmp_path, monkeypatch, module)


def test_offline_report_stream_is_reserved_and_bounded(tmp_path, monkeypatch):
    report_reader_case(tmp_path, monkeypatch)


def test_core_no_coverage_report_rewrite_persist_and_export(tmp_path):
    from common_lore_index_test_support.pgvector_prebuilt_native_seam_cases import core_report_seam_case
    core_report_seam_case(tmp_path)


@pytest.mark.parametrize("role,mutation", [("ordinary", "none"), ("ordinary", "ambient"),
    *[(role, mutation) for role in ("core", "native")
      for mutation in ("none", "auth", "argv", "profile", "forwarded", "ambient")]])
def test_offline_runner_cache_ownership_before_pytest(monkeypatch, role, mutation):
    from common_lore_index_test_support.pgvector_prebuilt_runner_cases import runtime_cache_case
    runtime_cache_case(monkeypatch, role, mutation)


@pytest.mark.parametrize("case", ["none", "caps", "cleanup", "counter", "archive", "pending",
    "replay", "decrease", "parent", "native-blocked", "native", "outer", "forecast", "authority", "cli-error", "rematerialize", "ordinal1"])
def test_cumulative_core_recovery_preserves_failure_and_single_attempt(tmp_path, monkeypatch, case):
    from common_lore_index_test_support.pgvector_prebuilt_recovery_cases import recovery_case
    recovery_case(tmp_path, monkeypatch, case)
