"""Synthetic body seam: real functional stores, no frozen model experiment."""
from __future__ import annotations

from pathlib import Path

import pytest

from repomap_test_support.retrieval_body_study_driver import run_authenticated_body_study
from repomap_test_support.retrieval_body_study_runtime_fixtures import (
    BodyTransportDouble,
    functional_body_catalog,
    functional_body_identity,
    publish_functional_body,
)
from repomap_test_support.postgres_harness import temporary_postgres


def test_functional_body_identity_cannot_enter_registered_driver() -> None:
    identity = functional_body_identity()
    catalog = functional_body_catalog()
    assert identity.reserved is False
    assert catalog.registered is False

    class StaleAdmission:
        def to_dict(self):
            return {"role": "body-study", "identity": "functional"}

    with pytest.raises(ValueError, match="body.inner:admission_type"):
        run_authenticated_body_study(StaleAdmission())


def test_real_sqlite_and_postgres_functional_publications_remain_unregistered(tmp_path: Path) -> None:
    sqlite = publish_functional_body(tmp_path / "sqlite")
    assert sqlite.published.backend == "sqlite"
    with temporary_postgres() as database:
        postgres = publish_functional_body(tmp_path / "postgres", postgres=database)
        assert postgres.published.backend == "postgresql"
        assert postgres.pin.is_factory_issued()
        assert sqlite.pin.is_factory_issued()
        assert sqlite.pin.to_dict() != postgres.pin.to_dict()


def test_lost_authority_refusal_cancel_and_cleanup_are_retained() -> None:
    transport = BodyTransportDouble()
    with pytest.raises(RuntimeError, match="explicit_response"):
        transport.exchange({"phase": "CLOE-W5-BODY-STUDY1"})
    transport.cancel()
    transport.cleanup()
    assert transport.events == ["exchange", "cancel", "cleanup"]
