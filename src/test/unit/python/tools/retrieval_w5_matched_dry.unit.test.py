"""Synthetic dry-run admission before input reads or runtime work."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from repomap_test_support.retrieval_w5_matched_fixture import make_synthetic_archives, synthetic_tar
from retrieval_body_study_journal_io import root_identity
from retrieval_w5_matched_dry import EVALUATOR_MEMBER, DryRunPlan, OfflineClosure, run_synthetic_dry

HEX = "a" * 64
OFFLINE = OfflineClosure(HEX, HEX, HEX, HEX)


@pytest.mark.parametrize("failure", ("plan", "cohort", "artifact", "custody", "new-attempt-root"))
def test_refuses_unbound_dry_launch_before_touching_inputs(tmp_path: Path, failure: str) -> None:
    evaluator, grade, attempt = (tmp_path / name for name in ("evaluator", "grade", "attempt"))
    for root in (evaluator, attempt):
        root.mkdir(mode=0o700)
    commitment = make_synthetic_archives().write(tmp_path / "source", include_grade=False)
    plan = DryRunPlan(commitment, HEX, HEX, OFFLINE, root_identity(attempt))
    pin = plan.identity()
    observed = OFFLINE
    if failure == "plan":
        pin = "b" * 64
    elif failure == "cohort":
        plan = replace(plan, commitment=replace(commitment, cohort="actual-cohort"))
    elif failure == "artifact":
        observed = replace(OFFLINE, runtime_archive_sha256="b" * 64)
    elif failure == "custody":
        grade = evaluator / "grade"
    else:
        attempt = tmp_path / "another-attempt"
        attempt.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="dry_attempt_owner" if failure == "new-attempt-root" else None):
        run_synthetic_dry(
            plan, expected_plan_sha256=pin, observed_closure=observed, evaluator_root=evaluator,
            grade_root=grade, attempt_root=attempt, open_runtime=lambda _: pytest.fail("runtime reached"),
            cleanup_verified=lambda: pytest.fail("cleanup reached"),
        )
    assert list(attempt.iterdir()) == []


def test_bound_bad_input_consumes_reservation_and_never_opens_runtime(tmp_path: Path) -> None:
    evaluator, grade, attempt = (tmp_path / name for name in ("evaluator", "grade", "attempt"))
    for root in (evaluator, attempt):
        root.mkdir(mode=0o700)
    archive = make_synthetic_archives()
    raw = synthetic_tar(archive.evaluator)
    commitment = archive.write(tmp_path / "source", include_grade=False)
    assert sha256(raw).hexdigest() == commitment.evaluation_sha256
    (evaluator / EVALUATOR_MEMBER).write_bytes(raw + b"tampered")
    plan = DryRunPlan(commitment, HEX, HEX, OFFLINE, root_identity(attempt))
    for expected in ("digest", "destination_exists"):
        with pytest.raises(ValueError, match=expected):
            run_synthetic_dry(
                plan, expected_plan_sha256=plan.identity(), observed_closure=OFFLINE, evaluator_root=evaluator,
                grade_root=grade, attempt_root=attempt, open_runtime=lambda _: pytest.fail("runtime reached"),
                cleanup_verified=lambda: pytest.fail("cleanup reached"),
            )
        (evaluator / EVALUATOR_MEMBER).write_bytes(raw)
    assert (attempt / "dry-terminal.json").is_file()
    assert not (attempt / "ranking").exists()
