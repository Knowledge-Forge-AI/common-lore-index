"""Unrelated functional body fixture façade for the synthetic seam only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval_body_study_values import require


def functional_body_identity() -> Any:
    from common_lore_index_test_support.retrieval_body_pin_fixtures import make_body_identity

    identity = make_body_identity()
    require(not identity.reserved, "body.fixture:functional_identity")
    return identity


def functional_body_catalog() -> Any:
    from common_lore_index_test_support.retrieval_body_fixtures import make_body_bindings, make_body_catalog_payload
    from retrieval_body_catalog import parse_functional_catalog
    from retrieval_body_values import canonical_bytes

    catalog = parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()),
        identity=functional_body_identity(),
        binding_ids=tuple(make_body_bindings().values()),
    )
    require(not catalog.registered, "body.fixture:functional_catalog")
    return catalog


def publish_functional_body(root: Path, *, postgres: Any | None = None) -> Any:
    """Publish unit-body bytes through the maintained real SQLite/PG fixture."""
    from common_lore_index_test_support.retrieval_body_fixtures import make_body_policy
    from common_lore_index_test_support.retrieval_body_pin_fixtures import make_body_bindings, make_body_corpus_payload
    from retrieval_body_corpus import parse_body_corpus
    from retrieval_body_values import canonical_bytes
    from common_lore_index_test_support.retrieval_body_publication_fixture import BodyStoreFixture, materialize_body_home

    identity = functional_body_identity()
    corpus = parse_body_corpus(
        canonical_bytes(make_body_corpus_payload()), identity=identity,
        bindings=make_body_bindings(),
    )
    policy = make_body_policy()
    published = materialize_body_home(root, corpus=corpus, identity=identity, policy=policy, postgres=postgres)
    return BodyStoreFixture(published, psql_command=getattr(postgres, "psql_command", "psql"))


class BodyTransportDouble:
    """Narrow synthetic exchange double; it cannot mint frozen-study receipts."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def exchange(self, request: Any) -> Any:
        self.events.append("exchange")
        raise RuntimeError("body.fixture:synthetic_transport_requires_explicit_response")

    def cancel(self) -> None:
        self.events.append("cancel")

    def cleanup(self) -> None:
        self.events.append("cleanup")


__all__ = [
    "BodyTransportDouble",
    "functional_body_catalog",
    "functional_body_identity",
    "publish_functional_body",
]
