"""Unit tests for the supplement input pin, final freeze and exclusive file custody."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from repomap_test_support.retrieval_supplement_fixtures import (
    make_test_bindings,
    make_test_catalog_payload,
    make_test_corpus_payload,
    make_test_identity,
    make_test_policy,
)
import retrieval_supplement_files as files_module
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_corpus import materialize_corpus, parse_corpus
from retrieval_supplement_files import file_inventory, publish_member, read_member
from retrieval_supplement_freeze import (
    PIN_FILE_NAME,
    build_final_freeze,
    build_input_pin,
    encode_record,
    publish_record,
    verify_final_freeze,
    verify_input_pin,
)
from retrieval_supplement_values import (
    REGISTERED_INPUT_PIN_SHA256,
    SupplementError,
    SupplementIdentity,
    canonical_bytes,
    digest_bytes,
)

OLD_FIXTURE = "src/test/fixtures/retrieval_evaluation/old-v1/catalog.json"
BUILDER = ["tools/retrieval_evaluation_schema.py"]
EXPLICIT = ["tools/retrieval_supplement_values.py"]


@pytest.mark.parametrize("bad", [[], {}])
def test_corpus_role_container_is_a_validation_error(bad: object) -> None:
    payload = make_test_corpus_payload()
    payload["files"][0]["role"] = bad
    with pytest.raises(SupplementError):
        parse_corpus(canonical_bytes(payload), identity=make_test_identity(), bindings=make_test_bindings())


def _catalog():
    return parse_catalog(
        canonical_bytes(make_test_catalog_payload()), identity=make_test_identity(),
        binding_ids=tuple(make_test_bindings().values()),
    )


def _repo(tmp_path: Path, *, bare: bool = False) -> tuple[Path, bytes]:
    repo = tmp_path / "repo"
    for relative, data in (
        (OLD_FIXTURE, b'{"opaque":"never decoded"}'),
        (BUILDER[0], b"SCHEMA = 1\n"),
        (EXPLICIT[0], b"VALUE = 1\n"),
    ):
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_bytes(data)
    old = (repo / OLD_FIXTURE).read_bytes()
    manifest = canonical_bytes({
        "commit": "a" * 40,
        "files": [{"path": OLD_FIXTURE, "bytes": len(old), "sha256": digest_bytes(old)[7:] if bare else digest_bytes(old)}],
    })
    return repo, manifest


def _input_files(manifest: bytes) -> dict[str, bytes]:
    return {
        "catalog-v1.json": canonical_bytes(make_test_catalog_payload()),
        "corpus-v1.json": canonical_bytes(make_test_corpus_payload()),
        "rationale.md": b"# Synthetic rationale\n",
        "bindings-v1.json": canonical_bytes(make_test_bindings()),
        "policy-v1.json": canonical_bytes(make_test_policy().to_dict()),
        "predecessor-manifest.json": manifest,
        "namespace-v1.json": canonical_bytes({"prefix": "unit-supplement-"}),
        "static-inventory-v1.json": canonical_bytes({"members": 18}),
    }


def _pinned(tmp_path: Path, pin: dict[str, Any] | None = None, *, bare: bool = False) -> tuple[Path, bytes, Path, bytes]:
    repo, manifest = _repo(tmp_path, bare=bare)
    files = _input_files(manifest)
    if pin is None:
        pin = build_input_pin(_catalog(), input_files=files, policy=make_test_policy(),
                              predecessor_manifest_sha256=digest_bytes(manifest))
    root = tmp_path / "inputs"
    root.mkdir()
    for name, data in files.items():
        publish_member(root, name, data)
    publish_record(root, PIN_FILE_NAME, pin)
    return root, encode_record(pin), repo, manifest


def _verify(root: Path, pin_raw: bytes, **overrides: Any):
    kwargs: dict[str, Any] = {
        "expected_pin_sha256": digest_bytes(pin_raw), "identity": make_test_identity(),
        "bindings": make_test_bindings(),
    }
    kwargs.update(overrides)
    return verify_input_pin(root, pin_raw, **kwargs)


def test_input_pin_is_data_only_and_verifies(tmp_path: Path) -> None:
    root, pin_raw, _repo_root, _manifest = _pinned(tmp_path)
    catalog = _verify(root, pin_raw)
    assert catalog.raw == canonical_bytes(make_test_catalog_payload())
    pin = json.loads(pin_raw)
    assert pin["source_closure_claimed"] is False and pin["execution_authority_granted"] is False
    assert pin["policy_digest"] == make_test_policy().policy_digest()
    assert len(pin["task_digests"]) == 16
    assert set(pin["task_digests"]["unit-supplement-01"]) == {"retained_task_sha256", "task_sha256"}
    assert file_inventory(root) == tuple(sorted([*_input_files(b"").keys(), PIN_FILE_NAME]))


def test_build_input_pin_detects_input_drift(tmp_path: Path) -> None:
    _repo_root, manifest = _repo(tmp_path)
    good = _input_files(manifest)
    cases: list[tuple[dict[str, bytes], str, Any]] = []
    changed = dict(good, **{"catalog-v1.json": good["catalog-v1.json"] + b" "})
    cases.append((changed, "catalog_bytes_mismatch", make_test_policy()))
    forged = make_test_policy().to_dict()
    forged["policy_digest"] = "sha256:" + "1" * 64
    cases.append((dict(good, **{"policy-v1.json": canonical_bytes(forged)}), "policy:field_or_digest_drift",
                  make_test_policy()))
    other = canonical_bytes(make_test_policy(vault_privacy="public-dev").to_dict())
    cases.append((dict(good, **{"policy-v1.json": other}), "input_pin:policy_mismatch", make_test_policy()))
    missing = dict(good)
    del missing["namespace-v1.json"]
    cases.append((missing, "input_files:membership_mismatch", make_test_policy()))
    cases.append((dict(good, **{"namespace-v1.json": b'{"a":1,"a":1}'}), "json_duplicate_key",
                  make_test_policy()))
    for files, code, policy in cases:
        with pytest.raises(SupplementError, match=code):
            build_input_pin(_catalog(), input_files=files, policy=policy,
                            predecessor_manifest_sha256=digest_bytes(manifest))
    with pytest.raises(SupplementError, match="input_pin:predecessor_drift"):
        build_input_pin(_catalog(), input_files=good, policy=make_test_policy(),
                        predecessor_manifest_sha256=digest_bytes(b"other"))


def test_verify_input_pin_rejects_tree_identity_and_digest_drift(tmp_path: Path) -> None:
    root, pin_raw, _repo_root, _manifest = _pinned(tmp_path)
    with pytest.raises(SupplementError, match="input_pin:digest_mismatch"):
        _verify(root, pin_raw, expected_pin_sha256="sha256:" + "2" * 64)
    with pytest.raises(SupplementError, match="registered_identity_required"):
        _verify(root, pin_raw, expected_pin_sha256=REGISTERED_INPUT_PIN_SHA256)
    other = SupplementIdentity("unit-supplement-other", "unit-supplement-corpus",
                               "CLOE-W5-SUPPLEMENT1-UNIT", "unit-supplement-graph", "unit-supplement-")
    with pytest.raises(SupplementError, match="supplement_id:identity_mismatch"):
        _verify(root, pin_raw, identity=other)
    swapped = dict(make_test_bindings(), lanterncode="unit-supplement-binding-lanterndocs")
    with pytest.raises(SupplementError, match="bindings_mismatch"):
        _verify(root, pin_raw, bindings=swapped)
    rationale = root / "rationale.md"
    original = rationale.read_bytes()
    rationale.write_bytes(original.replace(b"Synthetic", b"Synthetiq"))
    with pytest.raises(SupplementError, match="io:member_digest_mismatch"):
        _verify(root, pin_raw)
    rationale.write_bytes(original)
    (root / "extra.json").write_bytes(b"{}")
    with pytest.raises(SupplementError, match="tree_membership_mismatch"):
        _verify(root, pin_raw)
    (root / "extra.json").unlink()
    os.symlink("rationale.md", root / "alias.md")
    with pytest.raises(SupplementError, match="io:nonregular_member"):
        _verify(root, pin_raw)


def test_forged_task_digest_cannot_be_reproduced(tmp_path: Path) -> None:
    repo, manifest = _repo(tmp_path / "seed")
    pin = build_input_pin(_catalog(), input_files=_input_files(manifest), policy=make_test_policy(),
                          predecessor_manifest_sha256=digest_bytes(manifest))
    forged = copy.deepcopy(pin)
    forged["task_digests"]["unit-supplement-01"]["task_sha256"] = "sha256:" + "3" * 64
    root, pin_raw, _repo_root, _manifest = _pinned(tmp_path, forged)
    with pytest.raises(SupplementError, match="input_pin:reproduction_mismatch"):
        _verify(root, pin_raw)


def _sources(repo: Path, members: list[str]) -> dict[str, bytes]:
    return {member: (repo / member).read_bytes() for member in members}


def test_predecessor_bare_hex_manifest_preserves_bytes_and_detects_drift(tmp_path: Path) -> None:
    _root, pin_raw, repo, manifest = _pinned(tmp_path, bare=True)
    kwargs: dict[str, Any] = dict(expected_input_pin_sha256=digest_bytes(pin_raw),
        source_files=_sources(repo, BUILDER + EXPLICIT), builder_members=BUILDER,
        explicit_members=EXPLICIT, predecessor_manifest_raw=manifest)
    original = json.loads(manifest)["files"][0]["sha256"]
    assert len(original) == 64 and not original.startswith("sha256:")
    freeze = build_final_freeze(repo, pin_raw, **kwargs)
    assert freeze["predecessor_manifest_sha256"] == digest_bytes(manifest)
    assert json.loads(manifest)["files"][0]["sha256"] == original
    (repo / OLD_FIXTURE).write_bytes(b"x" * (repo / OLD_FIXTURE).stat().st_size)
    with pytest.raises(SupplementError, match="member_digest_mismatch"):
        build_final_freeze(repo, pin_raw, **kwargs)


def test_pretty_pin_preserves_bytes_and_requires_external_final_pin(tmp_path: Path) -> None:
    root, original_raw, repo, manifest = _pinned(tmp_path)
    pretty_raw = (json.dumps(json.loads(original_raw), indent=2, sort_keys=True) + "\n").encode()
    (root / PIN_FILE_NAME).write_bytes(pretty_raw)
    assert _verify(root, pretty_raw).raw == _catalog().raw
    kwargs: dict[str, Any] = {
        "source_files": _sources(repo, BUILDER + EXPLICIT), "builder_members": BUILDER,
        "explicit_members": EXPLICIT, "predecessor_manifest_raw": manifest,
    }
    freeze = build_final_freeze(repo, pretty_raw, expected_input_pin_sha256=digest_bytes(pretty_raw), **kwargs)
    assert freeze["input_pin_utf8"].encode() == pretty_raw
    with pytest.raises(SupplementError, match="input_pin_digest_mismatch"):
        build_final_freeze(repo, pretty_raw, expected_input_pin_sha256=digest_bytes(original_raw), **kwargs)


def test_final_freeze_embeds_exact_pin_and_source_closure(tmp_path: Path) -> None:
    _root, pin_raw, repo, manifest = _pinned(tmp_path)
    freeze = build_final_freeze(repo, pin_raw, expected_input_pin_sha256=digest_bytes(pin_raw), source_files=_sources(repo, BUILDER + EXPLICIT),
                                builder_members=BUILDER, explicit_members=EXPLICIT,
                                predecessor_manifest_raw=manifest)
    assert freeze["input_pin_utf8"].encode("utf-8") == pin_raw
    assert freeze["input_pin_sha256"] == digest_bytes(pin_raw)
    assert freeze["builder_members"] == BUILDER and freeze["explicit_members"] == EXPLICIT
    assert freeze["source_files"][EXPLICIT[0]]["sha256"] == digest_bytes(b"VALUE = 1\n")
    assert freeze["predecessor_file_count"] == 1 and freeze["execution_authority_granted"] is False
    kwargs: dict[str, Any] = {
        "input_pin_raw": pin_raw, "fresh_source_files": _sources(repo, BUILDER + EXPLICIT),
        "expected_input_pin_sha256": digest_bytes(pin_raw),
        "builder_members": BUILDER, "explicit_members": EXPLICIT, "predecessor_manifest_raw": manifest,
    }
    verify_final_freeze(repo, freeze, **kwargs)
    verify_final_freeze(repo, encode_record(freeze), **kwargs)
    out = tmp_path / "out"
    out.mkdir()
    digest = publish_record(out, "freeze-v1.json", freeze)
    assert digest == digest_bytes((out / "freeze-v1.json").read_bytes())
    with pytest.raises(SupplementError, match="io:destination_exists"):
        publish_record(out, "freeze-v1.json", freeze)
    with pytest.raises(SupplementError, match="freeze:input_pin_drift"):
        verify_final_freeze(repo, freeze, **dict(kwargs, input_pin_raw=pin_raw + b" "))

    added = repo / "tools/retrieval_evaluation_added.py"
    added.write_bytes(b"ADDED = 1\n")
    fresh = BUILDER + ["tools/retrieval_evaluation_added.py"]
    with pytest.raises(SupplementError, match="freeze:reproduction_mismatch"):
        verify_final_freeze(repo, freeze, **dict(kwargs, builder_members=fresh,
                                                 fresh_source_files=_sources(repo, fresh + EXPLICIT)))
    with pytest.raises(SupplementError, match="freeze:reproduction_mismatch"):
        verify_final_freeze(repo, freeze, **dict(kwargs, builder_members=[],
                                                 fresh_source_files=_sources(repo, EXPLICIT)))
    (repo / EXPLICIT[0]).write_bytes(b"VALUE = 2\n")
    with pytest.raises(SupplementError, match="freeze:source_drift"):
        verify_final_freeze(repo, freeze, **kwargs)


def test_final_freeze_rejects_membership_fixtures_test_support_and_predecessor_drift(tmp_path: Path) -> None:
    _root, pin_raw, repo, manifest = _pinned(tmp_path)

    def build(members: list[str], sources: dict[str, bytes], raw: bytes = manifest) -> None:
        build_final_freeze(repo, pin_raw, expected_input_pin_sha256=digest_bytes(pin_raw), source_files=sources, builder_members=BUILDER,
                           explicit_members=members, predecessor_manifest_raw=raw)

    with pytest.raises(SupplementError, match="source_membership_mismatch"):
        build(EXPLICIT, _sources(repo, BUILDER))
    with pytest.raises(SupplementError, match="fixture_path_in_sources"):
        build(EXPLICIT + [OLD_FIXTURE], _sources(repo, BUILDER + EXPLICIT + [OLD_FIXTURE]))
    bad = "tools/retrieval_supplement_bad.py"
    (repo / bad).write_bytes(b"from repomap_test_support.thing import x\n")
    with pytest.raises(SupplementError, match="tool_imports_test_support"):
        build(EXPLICIT + [bad], _sources(repo, BUILDER + EXPLICIT + [bad]))
    with pytest.raises(SupplementError, match="predecessor_manifest_mismatch"):
        build(EXPLICIT, _sources(repo, BUILDER + EXPLICIT), raw=manifest + b" ")
    old = repo / OLD_FIXTURE
    old.write_bytes(old.read_bytes().replace(b"never", b"nevER"))
    with pytest.raises(SupplementError, match="io:member_digest_mismatch"):
        build(EXPLICIT, _sources(repo, BUILDER + EXPLICIT))


def test_corpus_is_strict_and_materialized_as_data_only(tmp_path: Path) -> None:
    corpus = parse_corpus(canonical_bytes(make_test_corpus_payload()), identity=make_test_identity(),
                          bindings=make_test_bindings())
    target = tmp_path / "materialized"
    target.mkdir()
    written = materialize_corpus(corpus, target)
    assert len(written) == 18 and "lanterncode/tests/test_flame_color.py" in written
    assert (target / "lanterncode/lantern/registry.py").read_bytes() == corpus.files[0].content
    assert not list(target.rglob("__pycache__"))
    with pytest.raises(SupplementError, match="io:root_not_empty"):
        materialize_corpus(corpus, target)
    edits: list[tuple[Any, str]] = [
        (lambda f: f[0].update(source_relative_path="lanterncode/registry.py"), "alias_prefixed_path"),
        (lambda f: f[0].update(role="test_source"), "test_source_path"),
        (lambda f: f[0].update(source_relative_path="lantern/notes.md"), "code_requires_python"),
        (lambda f: f[0].update(source_relative_path="tests/test_registry.py"), "test_path_requires"),
        (lambda f: f[0].update(content_utf8="x" * 4097), "content_utf8:too_large"),
        (lambda f: f[0].update(content_utf8="a\x00b"), "content_utf8:nul"),
        (lambda f: f[0].update(binding_alias="lanternghost"), "binding_alias:unknown"),
        (lambda f: f[0].update(role="denied"), "denied_alias_mixed"),
        (lambda f: f.append(dict(f[0])), "duplicate_member"),
        (lambda f: f[0].update(extra=True), "corpus.file:unknown_fields"),
    ]
    for edit, code in edits:
        payload = copy.deepcopy(make_test_corpus_payload())
        edit(payload["files"])
        with pytest.raises(SupplementError, match=code):
            parse_corpus(canonical_bytes(payload), identity=make_test_identity(), bindings=make_test_bindings())


def test_file_custody_symlinks_nonregular_and_bounds(tmp_path: Path) -> None:
    root = tmp_path / "custody"
    root.mkdir()
    publish_member(root, "nested/deeper/member.txt", b"payload")
    assert read_member(root, "nested/deeper/member.txt", max_bytes=7,
                       expected_sha256=digest_bytes(b"payload")) == b"payload"
    with pytest.raises(SupplementError, match="io:member_too_large"):
        read_member(root, "nested/deeper/member.txt", max_bytes=6)
    os.symlink(root / "nested", root / "linked")
    with pytest.raises(SupplementError, match="io:unsafe_directory_component"):
        read_member(root, "linked/deeper/member.txt", max_bytes=7)
    with pytest.raises(SupplementError, match="io:unsafe_directory_component"):
        file_inventory(root / "linked")
    with pytest.raises(SupplementError, match="io:unsafe_directory_component"):
        publish_member(root, "linked/new.txt", b"x")
    os.unlink(root / "linked")
    os.mkfifo(root / "pipe")
    with pytest.raises(SupplementError, match="io:member_not_regular"):
        read_member(root, "pipe", max_bytes=10)
    with pytest.raises(SupplementError, match="io:nonregular_member"):
        file_inventory(root)
    os.unlink(root / "pipe")
    assert file_inventory(root) == ("nested/deeper/member.txt",)


def test_partial_reads_mutation_and_failed_publication_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "custody"
    root.mkdir()
    publish_member(root, "member.bin", b"0123456789")
    real_read = os.read
    monkeypatch.setattr(files_module.os, "read", lambda fd, n: real_read(fd, min(n, 3)))
    assert read_member(root, "member.bin", max_bytes=10) == b"0123456789"

    mutated: list[bool] = []

    def mutating_read(fd: int, n: int) -> bytes:
        if not mutated:
            mutated.append(True)
            with open(root / "member.bin", "ab") as handle:
                handle.write(b"!")
        return real_read(fd, n)

    monkeypatch.setattr(files_module.os, "read", mutating_read)
    with pytest.raises(SupplementError, match="io:member_changed"):
        read_member(root, "member.bin", max_bytes=64)
    monkeypatch.setattr(files_module.os, "read", real_read)
    with pytest.raises(SupplementError, match="io:destination_exists"):
        publish_member(root, "member.bin", b"other")
    assert (root / "member.bin").read_bytes().startswith(b"0123456789")

    def failing_fsync(fd: int) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(files_module.os, "fsync", failing_fsync)
    with pytest.raises(SupplementError, match="io:publication_failed"):
        publish_member(root, "fresh.bin", b"data")
    monkeypatch.undo()
    assert file_inventory(root) == ("member.bin",)
    with pytest.raises(SupplementError, match="io:root_must_be_absolute"):
        file_inventory(Path("relative"))
