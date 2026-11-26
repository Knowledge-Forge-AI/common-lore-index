"""Unit ownership for the body inner boundary and body epoch routing."""
from __future__ import annotations

from dataclasses import replace

import pytest

from common_lore_index_test_support.retrieval_body_study_driver import run_authenticated_body_study
from retrieval_body_study_preflight import WorkerEpochReceipt, _ISSUED, _receipt_identity


def test_inner_requires_factory_issued_plan_admission() -> None:
    with pytest.raises(ValueError, match="body.inner:admission_type"):
        run_authenticated_body_study(object())


def test_inner_does_not_accept_a_caller_injected_context() -> None:
    class ForgedAdmission:
        def to_dict(self):
            return {}

    with pytest.raises(ValueError, match="body.inner:admission_type"):
        run_authenticated_body_study(ForgedAdmission())


def test_epoch_receipt_replacement_loses_factory_seal() -> None:
    receipt = WorkerEpochReceipt(
        "sha256:" + "1" * 64, "sha256:" + "2" * 64, "canonical-node-v1",
        (), (), "sha256:" + "3" * 64, "sha256:" + "4" * 64,
        _origin=_ISSUED,
    )
    object.__setattr__(receipt, "_seal", _receipt_identity(receipt))
    assert WorkerEpochReceipt.is_factory_issued(receipt)
    replaced = replace(receipt, admission_identity="sha256:" + "5" * 64)
    assert not WorkerEpochReceipt.is_factory_issued(replaced)
