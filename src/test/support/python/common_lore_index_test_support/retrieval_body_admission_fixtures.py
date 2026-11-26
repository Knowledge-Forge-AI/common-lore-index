"""Synthetic issued publication pins for unrelated body-class unit inputs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index_test_support.retrieval_body_fixtures import make_body_policy
from common_lore_index_test_support.w5_semantic_support import make_w5_readback_adapter, make_w5_receipt
from retrieval_body_values import digest_bytes
from retrieval_semantic_provenance import AcceptedStoreReadback, verify_publication_provenance_from_adapter


@dataclass
class BodyReadbackCell:
    current: AcceptedStoreReadback
    reads: int = 0

    def __call__(self) -> AcceptedStoreReadback:
        self.reads += 1
        return self.current

    def advance(self) -> None:
        assert self.current.receipt.portable is not None
        vector = tuple((binding, 2, snapshot) for binding, _, snapshot in self.current.receipt.portable.snapshot_vector)
        receipt = make_w5_receipt(generation=2, snapshot_vector=vector)
        self.current = make_w5_readback_adapter(receipt, graph_id=self.current.graph_id,
            repository_identity=self.current.repository_identity, backend=self.current.backend,
            run_id=2, generation=2).current


def make_body_publication(*, policy: BodyProjectionPolicy | None = None,
                          backend: str = 'sqlite', omit: frozenset[str] = frozenset()) -> tuple[Any, BodyReadbackCell]:
    actual = make_body_policy() if policy is None else policy
    public = sorted(binding for binding, value in actual.bindings.items()
                    if value.resolved_binding_privacy == 'public-dev' and binding not in omit)
    vector = tuple((binding, 1, 'snap1:' + digest_bytes(binding.encode()).removeprefix('sha256:')) for binding in public)
    receipt = make_w5_receipt(snapshot_vector=vector)
    current = make_w5_readback_adapter(receipt, graph_id=actual.graph_id,
                                      repository_identity='repo1:unit-body-canopy', backend=backend).current
    cell = BodyReadbackCell(current)
    return verify_publication_provenance_from_adapter(cell, policy=actual), cell
