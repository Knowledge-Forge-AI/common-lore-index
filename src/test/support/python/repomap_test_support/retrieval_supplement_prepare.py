"""Prepare/check-only supplement artifacts; no evaluation, worker or study command."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from repomap_test_support.retrieval_supplement_source import supplement_source_snapshot
from repomap_test_support.w5_experiment_support import DECLARED_PROFILE_IDENTITY
from repomap_test_support.w5_public_text_learned import profiles
from retrieval_supplement_applicability import CandidateExposure
from retrieval_supplement_corpus import parse_corpus
from retrieval_supplement_declaration import JournalBinding, create_declaration, representation_for, validate_declaration
from retrieval_supplement_files import file_inventory, publish_member, read_member
from retrieval_supplement_freeze import build_final_freeze, policy_from_bytes, verify_final_freeze, verify_input_pin
from retrieval_supplement_preparation import prepare_supplement
from retrieval_supplement_values import (
    ARMS, MAX_JSON_BYTES, REGISTERED_INPUT_PIN_SHA256, SupplementIdentity,
    canonical_bytes, digest_bytes, strict_json,
)

_MEMBERS = ("declaration-v1.json", "freeze-v1.json", "preparation-v1.json", "source-members-v1.json")
_CHECKPOINT = "checkpoint-v1.json"


def _manifest(artifacts: Mapping[str, bytes], pin: str) -> dict[str, Any]:
    return {"schema": "cloe-supplement-source-checkpoint-v1", "input_pin_sha256": pin,
        "files": {name: {"bytes": len(raw), "sha256": digest_bytes(raw)} for name, raw in sorted(artifacts.items())},
        "execution_authorized": False, "model_workers_started": 0, "queries_executed": False,
        "native_or_scientific_acceptance": False}


def static_exposures(raw: bytes) -> tuple[CandidateExposure, ...]:
    """Use only pinned static public rows; no runtime or query hits are inferred."""
    value = strict_json(raw)
    if type(value) is not dict or value.get("static_only") is not True or type(value.get("inventory")) is not list:
        raise ValueError("supplement_static_inventory")
    if len(value["inventory"]) > 80 or value.get("query_or_model_execution") is not False:
        raise ValueError("supplement_static_inventory_scope")
    result = []
    for row in value["inventory"]:
        if type(row) is not dict or type(row.get("public")) is not bool:
            raise ValueError("supplement_static_inventory_row")
        if not row["public"]:
            continue
        paths = row.get("paths")
        if type(paths) is not list or len(paths) != 1 or type(row.get("spans")) is not list:
            raise ValueError("supplement_static_inventory_path")
        path = paths[0]
        spans = [span for span in row["spans"] if type(span) is dict and span.get("path") == path]
        span = spans[0] if spans else None
        start, end = (span["start"], span["end"]) if span is not None else (None, None)
        for arm in ARMS:
            if arm in (ARMS[0], ARMS[2], ARMS[4], ARMS[5]) and span is None:
                continue
            result.append(CandidateExposure(arm, row["binding_id"], path, row["canonical_key"], row["kind"], start, end))
    return tuple(result)


def _artifacts(
    repo_root: Path, input_root: Path, *, identity: SupplementIdentity,
    bindings: Mapping[str, str], expected_pin_sha256: str, artifact_profile_sha256: str,
    attempt_id: str, functional_exposures: Sequence[CandidateExposure] | None,
) -> dict[str, bytes]:
    pin_raw = read_member(input_root, "input-pin-v1.json", max_bytes=MAX_JSON_BYTES, expected_sha256=expected_pin_sha256)
    catalog = verify_input_pin(input_root, pin_raw, expected_pin_sha256=expected_pin_sha256, identity=identity, bindings=bindings)
    corpus = parse_corpus(read_member(input_root, "corpus-v1.json", max_bytes=MAX_JSON_BYTES), identity=identity, bindings=bindings)
    policy = policy_from_bytes(read_member(input_root, "policy-v1.json", max_bytes=MAX_JSON_BYTES))
    if identity.registered and functional_exposures is not None:
        raise ValueError("supplement_registered_exposure_override")
    exposures = tuple(functional_exposures) if functional_exposures is not None else static_exposures(
        read_member(input_root, "static-inventory-v1.json", max_bytes=MAX_JSON_BYTES))
    preparation = prepare_supplement(catalog, corpus, policy=policy, exposures=exposures)
    if identity.registered and artifact_profile_sha256 != DECLARED_PROFILE_IDENTITY:
        raise ValueError("supplement_unapproved_artifact_profile")
    embedding_profiles = {arm: profile.identity for arm, profile in profiles(artifact_profile_sha256).items()}
    snapshot = supplement_source_snapshot(repo_root)
    predecessor = read_member(input_root, "predecessor-manifest.json", max_bytes=MAX_JSON_BYTES)
    freeze = build_final_freeze(
        repo_root, pin_raw, expected_input_pin_sha256=expected_pin_sha256,
        source_files=snapshot.files, builder_members=snapshot.builder_members,
        explicit_members=snapshot.explicit_members, predecessor_manifest_raw=predecessor,
    )
    journals = {}
    for index, arm in enumerate(ARMS):
        suffix = "baseline" if index < 3 else "v1" if index == 3 else "public-text"
        journals[arm] = JournalBinding(attempt_id + "-" + suffix, attempt_id + "-attempt-" + suffix,
                                       representation_for(arm), embedding_profiles.get(representation_for(arm)))
    freeze_raw = canonical_bytes(freeze)
    declaration = create_declaration(
        catalog, input_pin_sha256=expected_pin_sha256, freeze_sha256=digest_bytes(freeze_raw),
        source_closure_sha256=snapshot.identity, artifact_profile_sha256=artifact_profile_sha256,
        embedding_profiles=embedding_profiles,
        attempt_id=attempt_id, journals=journals,
    )
    return {
        "preparation-v1.json": canonical_bytes(preparation), "freeze-v1.json": freeze_raw,
        "declaration-v1.json": declaration.raw,
        "source-members-v1.json": canonical_bytes({"builder_members": snapshot.builder_members,
            "explicit_members": snapshot.explicit_members, "source_files": snapshot.manifest()}),
    }


def prepare_checkpoint(
    repo_root: Path, input_root: Path, output_root: Path, *, identity: SupplementIdentity,
    bindings: Mapping[str, str], expected_pin_sha256: str, artifact_profile_sha256: str,
    attempt_id: str, functional_exposures: Sequence[CandidateExposure] | None = None,
) -> dict[str, Any]:
    """Publish complete exclusive artifacts and a last-written completion manifest."""
    artifacts = _artifacts(repo_root, input_root, identity=identity, bindings=bindings,
        expected_pin_sha256=expected_pin_sha256, artifact_profile_sha256=artifact_profile_sha256,
        attempt_id=attempt_id, functional_exposures=functional_exposures)
    if output_root.exists() or output_root.is_symlink():
        raise ValueError("supplement_output_exists")
    output_root.mkdir(mode=0o700)
    for name, raw in artifacts.items():
        publish_member(output_root, name, raw)
    manifest = _manifest(artifacts, expected_pin_sha256)
    publish_member(output_root, _CHECKPOINT, canonical_bytes(manifest))
    return manifest


def check_checkpoint(
    repo_root: Path, input_root: Path, output_root: Path, *, identity: SupplementIdentity,
    bindings: Mapping[str, str], expected_pin_sha256: str, artifact_profile_sha256: str,
    attempt_id: str, functional_exposures: Sequence[CandidateExposure] | None = None,
) -> dict[str, Any]:
    """Rerun builders and every input check; never trust a cached member set."""
    if file_inventory(output_root) != tuple(sorted((*_MEMBERS, _CHECKPOINT))):
        raise ValueError("supplement_checkpoint_members")
    expected = _artifacts(repo_root, input_root, identity=identity, bindings=bindings,
        expected_pin_sha256=expected_pin_sha256, artifact_profile_sha256=artifact_profile_sha256,
        attempt_id=attempt_id, functional_exposures=functional_exposures)
    manifest = strict_json(read_member(output_root, _CHECKPOINT, max_bytes=MAX_JSON_BYTES))
    if canonical_bytes(manifest) != canonical_bytes(_manifest(expected, expected_pin_sha256)):
        raise ValueError("supplement_checkpoint_manifest")
    for name, raw in expected.items():
        provided = read_member(output_root, name, max_bytes=4 * 1024 * 1024, expected_sha256=digest_bytes(raw))
        if provided != raw or manifest["files"][name] != {"bytes": len(raw), "sha256": digest_bytes(raw)}:
            raise ValueError("supplement_checkpoint_drift")
    # Explicit checks retain the external bindings rather than accepting only
    # self-consistency of the regenerated JSON.
    pin_raw = read_member(input_root, "input-pin-v1.json", max_bytes=MAX_JSON_BYTES)
    catalog = verify_input_pin(input_root, pin_raw, expected_pin_sha256=expected_pin_sha256, identity=identity, bindings=bindings)
    snapshot = supplement_source_snapshot(repo_root)
    verify_final_freeze(repo_root, expected["freeze-v1.json"], expected_input_pin_sha256=expected_pin_sha256,
        input_pin_raw=pin_raw, fresh_source_files=snapshot.files, builder_members=snapshot.builder_members,
        explicit_members=snapshot.explicit_members, predecessor_manifest_raw=read_member(input_root, "predecessor-manifest.json", max_bytes=MAX_JSON_BYTES))
    validate_declaration(expected["declaration-v1.json"], catalog, expected_input_pin_sha256=expected_pin_sha256,
        expected_freeze_sha256=digest_bytes(expected["freeze-v1.json"]), expected_source_closure_sha256=snapshot.identity,
        expected_artifact_profile_sha256=artifact_profile_sha256,
        expected_embedding_profiles={arm: profile.identity for arm, profile in profiles(artifact_profile_sha256).items()})
    return dict(manifest)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--artifact-profile-sha256", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args(argv)
    identity = SupplementIdentity("sup1-calibration-v1", "sup1-corpus-v1", "CLOE-W5-SUPPLEMENT1", "sup1-calibration", "sup1-")
    root = args.repo_root / "src/test/fixtures/retrieval_evaluation/supplement-v1"
    metadata = strict_json(read_member(root, "bindings-v1.json", max_bytes=MAX_JSON_BYTES))
    function = prepare_checkpoint if args.action == "prepare" else check_checkpoint
    function(args.repo_root, root, args.output_root, identity=identity, bindings=metadata["bindings"],
        expected_pin_sha256=REGISTERED_INPUT_PIN_SHA256, artifact_profile_sha256=args.artifact_profile_sha256, attempt_id=args.attempt_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
