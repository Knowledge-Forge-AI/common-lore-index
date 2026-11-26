"""Unrelated synthetic input, policy and operand builders for the matched adapter."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from typing import Any

from common_lore_index.graph.multi_source_records import graph_source_binding_id
from common_lore_index.retrieval.lexical_policy import PRODUCTION_MAX_AGGREGATE_INPUT_BYTES, PRODUCTION_MAX_PROJECTED_FILES
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy, ResolvedBindingPolicy
from common_lore_index.retrieval.query_contracts import QueryMode
from common_lore_index_test_support.retrieval_body_fixtures import GRAPH
from retrieval_semantic_fusion import FileIdentityTuple
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_primitives import canonical_bytes, digest_json
from retrieval_w5_matched_attempt import FitObservation, RankCoverage, RankedObservation, publication_pair_sha256
from retrieval_w5_matched_design import Arm, Case, Slot, Stratum, Wording, plan_slots
from retrieval_w5_matched_inputs import InputCommitment, MatchedInput, _lexical_for
from retrieval_w5_matched_publications import (
    CaptureReadback, MatchedPublications, ObservedPublication, ProjectedFileWitness, full_policy_digest, refusal_for,
)

PUBLIC = ("elm", "oak")
DENIED = ("barred-a", "barred-b")
MATCH = "W5_SYNTHETIC_needle:"
ABSENT = "W5_SYNTHETIC_missing:"
EVALUATOR = "evaluator.tar"
GRADE = "grade.tar"
REFUSAL = "evaluator_admission_refusal"


def synthetic_tar(members: Mapping[str, bytes]) -> bytes:
    target = BytesIO()
    with tarfile.open(fileobj=target, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o400
            archive.addfile(info, BytesIO(raw))
    return target.getvalue()


def _cases() -> tuple[Case, ...]:
    strata = tuple(kind for kind in (Stratum.BEHAVIOR, Stratum.CONTEXT, Stratum.TESTS, Stratum.ANCHOR) for _ in range(4))
    strata += tuple(kind for kind in (Stratum.ABSENT, Stratum.AMBIGUOUS, Stratum.PRIVACY, Stratum.UNSUPPORTED) for _ in range(2))
    return tuple(Case(n, kind, tuple(Wording)[(n - 1) % 2] if n <= 12 else None)
                 for n, kind in enumerate(strata, 1))


def _plans(cases: tuple[Case, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    requests, native, lexical = [], [], []
    for case in cases:
        ordinal = case.ordinal
        clue = f"Which specimen explains canopy branch {ordinal}?"
        scope: tuple[str, ...] = PUBLIC
        mode = QueryMode.RANKED_DISCOVERY.value
        patterns = ["canopy", "branch"]
        if case.stratum is Stratum.ANCHOR:
            clue, patterns = f"specimen_{ordinal % 12}", [f"specimen_{ordinal % 12}"]
        if case.stratum is Stratum.PRIVACY:
            scope = ("elm", "barred-a") if ordinal == 21 else ("barred-b",)
            mode, patterns = REFUSAL, []
        if case.stratum is Stratum.UNSUPPORTED:
            clue = MATCH if ordinal == 23 else ABSENT
            mode, patterns = QueryMode.LITERAL_CAPTURED_TEXT.value, [clue]
        request = {
            "ordinal": ordinal, "stratum": case.stratum.value, "bindings": list(scope), "mode": mode, "clue": clue,
            "wording": case.wording.value if case.wording else None,
        }
        requests.append(request)
        native.append({"ordinal": ordinal, "mode": mode, "bindings": list(scope), "source_path_prefix": None,
                       "fixed_strings": True, "ignore_case": mode == QueryMode.RANKED_DISCOVERY.value,
                       "limit": 10, "patterns": patterns})
        if mode != REFUSAL:
            lexical.append(_lexical_for(request)[1])
    return requests, native, lexical


def _source() -> tuple[dict[str, Any], dict[str, bytes]]:
    sources, files, members = [], [], {}
    for alias in PUBLIC:
        notice = b"Synthetic permissive fixture notice.\n"
        members[f"notices/{alias}.txt"] = notice
        sources.append({"binding": alias, "repo": f"synthetic/{alias}", "commit": "a" * 40, "tree": "b" * 40,
                        "license": "MIT", "license_path": "LICENSE", "license_sha256": sha256(notice).hexdigest()})
        for n in range(12):
            path = f"specimen_{n}.py"
            marker = MATCH if (alias, n) == ("elm", 0) else "synthetic evidence"
            raw = f"def canopy_{n}():\n    # {marker}\n    return '{alias} branch {n}'\n".encode()
            digest = sha256(raw).hexdigest()
            files.append({"binding": alias, "role": "source", "path": path, "bytes": len(raw), "sha256": digest,
                          "order_key": digest, "sample_order": n + 1,
                          "blob": sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()})
            members[f"population/{alias}/{path}"] = raw
    return {"sources": sources, "files": files, "sampling_rules_sha256": "c" * 64}, members


def _grade(cohort: str, frame: str, cases: str) -> dict[str, bytes]:
    rows = []
    for ordinal in range(1, 25):
        exact = {"binding": "elm", "path": f"specimen_{ordinal % 12}.py", "grade": 3}
        if ordinal <= 16:
            control, targets = "none", [exact]
        elif ordinal <= 18:
            control, targets = "answer_absent", []
        elif ordinal <= 20:
            control, targets = "ambiguous", [exact | {"grade": 2}, exact | {"binding": "oak", "grade": 2}]
        elif ordinal <= 22:
            control, targets = "binding_privacy_not_authorized", []
        else:
            control = "literal_scoped_match" if ordinal == 23 else "literal_scoped_complete_empty"
            targets = []
        rows.append({"ordinal": ordinal, "golds": targets, "control": control})
    return {
        "cohort.json": canonical_bytes({"cohort": cohort, "frame_sha256": frame, "cases_sha256": cases}),
        "golds.json": canonical_bytes(rows),
        "literal-expectations.json": canonical_bytes([
            {"ordinal": 23, "expected_outcome": "literal_scoped_match",
             "expected_files": [{"binding": "elm", "path": "specimen_0.py"}]},
            {"ordinal": 24, "expected_outcome": "literal_scoped_complete_empty", "expected_files": []},
        ]),
    }


@dataclass(frozen=True)
class SyntheticArchives:
    evaluator: dict[str, bytes]
    grader: dict[str, bytes]

    def write(self, root: Path, *, include_grade: bool = True) -> InputCommitment:
        root.mkdir(mode=0o700)
        raw = synthetic_tar(self.evaluator)
        grade = synthetic_tar(self.grader)
        (root / EVALUATOR).write_bytes(raw)
        if include_grade:
            (root / GRADE).write_bytes(grade)
        return InputCommitment("synthetic-matched", sha256(raw).hexdigest(),
            str(json.loads(self.evaluator["cohort.json"])["case_frame_sha256"]), sha256(self.evaluator["cases.json"]).hexdigest(),
            sha256(grade).hexdigest())


def make_synthetic_archives() -> SyntheticArchives:
    cases = _cases()
    rows, native, lexical = _plans(cases)
    frame, members = _source()
    members.update({"cases.json": canonical_bytes(rows), "source-frame.json": canonical_bytes(frame),
                    "native-plans.json": canonical_bytes(native), "lexical-plans.json": canonical_bytes(lexical),
                    "schedule.json": canonical_bytes([{"ordinal": slot.ordinal, "arm": slot.arm.value}
                                                      for slot in plan_slots(cases)])})
    # The original source-frame commitment includes fields outside the
    # evaluator's projected frame. The evaluator archive pins both values.
    frame_sha = sha256(canonical_bytes(frame | {"original_frame_profile": "synthetic"})).hexdigest()
    cases_sha = sha256(members["cases.json"]).hexdigest()
    grade = _grade("synthetic-matched", frame_sha, cases_sha)
    members["cohort.json"] = canonical_bytes({
        "cohort": "synthetic-matched", "phase": "CLOE-W5-HELDOUT-COHORT1", "generation": 1,
        "case_frame_sha256": frame_sha, "cases_sha256": cases_sha,
        "grading_commitment_sha256": sha256(synthetic_tar(grade)).hexdigest(),
        "runtime_authorized": False, "binding_policy": dict.fromkeys(PUBLIC, "public-dev") | dict.fromkeys(DENIED, "denied"),
    })
    return SyntheticArchives(members, grade)


def synthetic_policy() -> tuple[BodyProjectionPolicy, dict[str, str]]:
    bindings = {alias: graph_source_binding_id(GRAPH, alias) for alias in PUBLIC + DENIED}
    values = {}
    for alias, binding in bindings.items():
        privacy = "private-memory" if alias in DENIED else "public-dev"
        values[binding] = ResolvedBindingPolicy(binding, alias, "source", "folder", "public-dev", "public-dev",
                                                privacy, privacy, "full-authorized", "full-authorized")
    return BodyProjectionPolicy(
        graph_id=GRAPH, selected_binding_ids=frozenset(bindings[alias] for alias in PUBLIC), bindings=values,
        max_projected_files=PRODUCTION_MAX_PROJECTED_FILES,
        max_aggregate_input_bytes=PRODUCTION_MAX_AGGREGATE_INPUT_BYTES,
    ).validate(), bindings


def captured_fixture(
    inputs: MatchedInput, policy: BodyProjectionPolicy, pin: VerifiedPublicationPin, bindings: Mapping[str, str],
) -> CaptureReadback:
    """Explicit unit double. The integration owner obtains witnesses from both stores."""
    snapshots = pin.snapshot_by_binding()
    pg = pin.backend == "postgresql"
    return CaptureReadback(
        digest_json(pin.to_dict()).removeprefix("sha256:"), full_policy_digest(policy),
        tuple(ProjectedFileWitness(FileIdentityTuple(bindings[row.binding], snapshots[bindings[row.binding]][1], row.path),
                                  row.sha256, int(pg)) for row in inputs.files),
        "synthetic-lexical" if pg else None, "unused-in-functional-proof" if pg else None,
    )


def ready_fit(pair: MatchedPublications) -> FitObservation:
    return FitObservation(
        pair.inputs.commitment.evaluation_sha256, publication_pair_sha256(pair),
        {(row.binding, row.path): (8,) for row in pair.inputs.files},
        {task.case.ordinal: 5 for task in pair.inputs.tasks if refusal_for(task, Arm.FUSION) is None}, 4096,
        {slot: True for slot in pair.inputs.slots if slot.arm is not Arm.FUSION
         and refusal_for(pair.inputs.tasks[slot.ordinal - 1], slot.arm) is None},
    )


def synthetic_result(pair: MatchedPublications, slot: Slot) -> RankedObservation:
    pin = pair.pin_for(slot.arm)
    capture = pair.postgres_capture
    ordinal = slot.ordinal
    hits: tuple[FileIdentityTuple, ...] = ()
    if ordinal <= 16 or ordinal in (19, 20, 23):
        key = pair.binding_ids["elm"]
        path = f"specimen_{0 if ordinal == 23 else ordinal % 12}.py"
        hits = (FileIdentityTuple(key, pin.snapshot_by_binding()[key][1], path),)
    mode = QueryMode.LITERAL_CAPTURED_TEXT if slot.arm is Arm.NATIVE or ordinal >= 23 else QueryMode.RANKED_DISCOVERY
    return RankedObservation(
        ObservedPublication(pin.publication_bundle_id, pin.snapshot_manifest_id, pin.run_id,
                            capture.lexical_generation_id if slot.arm is not Arm.NATIVE else None,
                            capture.vector_generation_id if slot.arm is Arm.FUSION else None),
        hits, RankCoverage.COMPLETE, mode, slot.arm is Arm.NATIVE and ordinal < 23,
    )
