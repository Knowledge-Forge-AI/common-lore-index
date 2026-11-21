"""Bind the matched cohort to independent live SQLite and PostgreSQL publications."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from common_lore_index.graph.multi_source import graph_source_binding_id
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.storage.lexical_query_sql import RawLexicalQueryRow
from retrieval_body_study_files import read_member
from retrieval_evaluation_views import MaterializedNativeView
from retrieval_semantic_fusion import FileIdentityTuple
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_primitives import digest_json
from retrieval_w5_matched_design import Arm, Slot, Stratum
from retrieval_w5_matched_inputs import MatchedInput, TaskInput, digest_value, require_match

_DENIAL = "binding_privacy_not_authorized"
_UNSUPPORTED = "unsupported_semantic_query_mode"


def full_policy_digest(policy: BodyProjectionPolicy) -> str:
    """Include every projection limit, even fields outside the legacy digest."""
    return digest_json(policy.to_dict() | {"max_projected_files": policy.max_projected_files}).removeprefix("sha256:")


def _pin_sha(pin: VerifiedPublicationPin) -> str:
    return digest_json(pin.to_dict()).removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class ProjectedFileWitness:
    """A backend-observed content identity, rather than a rank result."""

    file: FileIdentityTuple
    sha256: str
    lexical_entries: int


@dataclass(frozen=True, slots=True)
class CaptureReadback:
    """Result of reading a specific store's materialized projected file inventory."""

    observed_pin_sha256: str
    full_policy_sha256: str
    files: tuple[ProjectedFileWitness, ...]
    lexical_generation_id: str | None = None
    vector_generation_id: str | None = None
    store_policy_sha256: str | None = None
    store_configuration_sha256: str | None = None


CaptureReader = Callable[[], CaptureReadback]


def read_native_capture(
    view: MaterializedNativeView, pin: VerifiedPublicationPin, policy: BodyProjectionPolicy,
) -> CaptureReadback:
    """Verify live materialized body bytes against the native snapshot witness."""
    witness = view.materialization_witness
    require_match(witness is not None and pin.is_factory_issued() and pin.backend == "sqlite", "native_witness")
    assert witness is not None
    witness.validate()
    require_match(
        (witness.graph_id, witness.publication_bundle_id, witness.snapshot_manifest_id, witness.graph_candidate_id,
         witness.policy_digest, witness.snapshot_vector)
        == (pin.graph_id, pin.publication_bundle_id, pin.snapshot_manifest_id, pin.graph_candidate_id,
            policy.policy_digest(), pin.snapshot_vector), "native_witness_binding",
    )
    paths = {row.view_relative_path: (row.binding_id, row.source_relative_path) for row in witness.files}
    require_match(len(paths) == len(witness.files) and dict(view.path_to_binding) == paths
                  and tuple(view.selected_relative_paths) == tuple(paths)
                  and dict(view.excluded_bindings) == dict(witness.excluded_bindings)
                  and set(view.path_to_surface) == paths.keys(), "native_witness_inventory")
    files = []
    for row in witness.files:
        require_match(row.surface_kind == "raw_body" and view.path_to_surface[row.view_relative_path].value == row.match_surface
                      and 0 < row.byte_length <= 4096, "native_witness_surface")
        raw = read_member(view.view_root, row.view_relative_path, max_bytes=row.byte_length,
                          expected_sha256=row.content_sha256)
        require_match(len(raw) == row.byte_length, "native_witness_length")
        files.append(ProjectedFileWitness(
            FileIdentityTuple(row.binding_id, row.snapshot_id, row.source_relative_path),
            row.content_sha256.removeprefix("sha256:"), 0,
        ))
    return CaptureReadback(_pin_sha(pin), full_policy_digest(policy),
                           tuple(sorted(files, key=lambda row: row.file.utf8_sort_key())))


class MissingObservedIdentity(ValueError):
    """A backend envelope omitted a required observed identity."""


@dataclass(frozen=True, slots=True)
class ObservedPublication:
    """Metadata observed by a backend adapter before any caller fallbacks."""

    publication_bundle_id: str | None
    snapshot_manifest_id: str | None
    accepted_run_id: int | None
    lexical_generation_id: str | None = None
    vector_generation_id: str | None = None

    @classmethod
    def from_lexical_row(cls, row: RawLexicalQueryRow) -> "ObservedPublication":
        require_match(type(row) is RawLexicalQueryRow, "lexical_row")
        return cls(row.publication_bundle_id, row.snapshot_manifest_id, row.run_id, row.lexical_generation_id)


@dataclass(frozen=True, slots=True)
class MatchedPublications:
    """Factory-bound pair with independently retained capabilities and identities."""

    inputs: MatchedInput
    sqlite: VerifiedPublicationPin
    postgres: VerifiedPublicationPin
    binding_ids: Mapping[str, str]
    inventory: frozenset[tuple[str, str]]
    policy_sha256: str
    native_policy: BodyProjectionPolicy
    sqlite_capture: CaptureReadback
    postgres_capture: CaptureReadback
    _sqlite_read: CaptureReader = field(repr=False)
    _postgres_read: CaptureReader = field(repr=False)
    _issued_identity: int | None = field(default=None, init=False, repr=False, compare=False)

    def selected_ids(self, task: TaskInput) -> frozenset[str]:
        return frozenset(self.binding_ids[alias] for alias in task.bindings)

    def is_factory_issued(self) -> bool:
        return self._issued_identity == id(self)

    def revalidate(self, arm: Arm | None = None) -> None:
        require_match(self.is_factory_issued(), "unbound_publications")
        if arm is None or arm is Arm.NATIVE:
            _verify_current(self.sqlite)
            require_match(self._sqlite_read() == self.sqlite_capture, "sqlite_capture_drift")
        if arm is None or arm is not Arm.NATIVE:
            _verify_current(self.postgres)
            require_match(self._postgres_read() == self.postgres_capture, "postgres_capture_drift")

    def require_postgres_reader(self, reader: CaptureReader) -> None:
        require_match(reader is self._postgres_read, "postgres_capture_reader_drift")

    def pin_for(self, arm: Arm) -> VerifiedPublicationPin:
        return self.sqlite if arm is Arm.NATIVE else self.postgres

    def check_observed(self, slot: Slot, evidence: ObservedPublication, *, completed: bool = True) -> None:
        """Reject present contradictions before evaluating missing fields."""
        if type(evidence) is not ObservedPublication:
            raise MissingObservedIdentity("matched_input:observation_identity_missing")
        pin = self.pin_for(slot.arm)
        required = (
            (evidence.publication_bundle_id, pin.publication_bundle_id, "observation_identity"),
            (evidence.snapshot_manifest_id, pin.snapshot_manifest_id, "observation_identity"),
            (evidence.accepted_run_id, pin.run_id, "observation_identity"),
            (evidence.lexical_generation_id,
             self.postgres_capture.lexical_generation_id if slot.arm is not Arm.NATIVE else None,
             "lexical_generation"),
            (evidence.vector_generation_id,
             self.postgres_capture.vector_generation_id if slot.arm is Arm.FUSION else None,
             "vector_generation"),
        )
        for actual, expected, label in required:
            if actual is not None:
                require_match(type(actual) is type(expected) and actual == expected, label + "_drift")
        for actual, expected, label in required:
            if completed and actual is None and expected is not None:
                raise MissingObservedIdentity("matched_input:" + label + "_missing")


def refusal_for(task: TaskInput, arm: Arm) -> str | None:
    """Compute refused paths before callers can plan a query or invoke a model."""
    if task.case.stratum is Stratum.PRIVACY:
        return _DENIAL
    if task.case.stratum is Stratum.UNSUPPORTED and arm is Arm.FUSION:
        return _UNSUPPORTED
    return None


def _verify_current(pin: VerifiedPublicationPin) -> None:
    require_match(type(pin) is VerifiedPublicationPin and pin.is_factory_issued(), "publication_factory")
    pin.revalidate_current()


def _verify_inventory(
    pin: VerifiedPublicationPin,
    capture: CaptureReadback,
    policy_sha256: str,
    expected: Mapping[tuple[str, str], str],
) -> None:
    require_match(type(capture) is CaptureReadback and type(capture.files) is tuple
                  and capture.observed_pin_sha256 == _pin_sha(pin)
                  and capture.full_policy_sha256 == policy_sha256, "capture_pin_binding")
    snapshots = pin.snapshot_by_binding()
    files = {}
    for row in capture.files:
        require_match(type(row) is ProjectedFileWitness and type(row.file) is FileIdentityTuple, "capture_witness")
        identity = row.file.validate()
        expected_snapshot = snapshots.get(identity.binding_id)
        require_match(identity.binding_id in pin.admitted_public_bindings and expected_snapshot is not None
                      and expected_snapshot[1] == identity.snapshot_id, "capture_snapshot")
        key = identity.file_key()
        require_match(key not in files and type(row.lexical_entries) is int
                      and 0 <= row.lexical_entries <= (4096 if pin.backend == "postgresql" else 0), "capture_file_census")
        files[key] = digest_value(row.sha256)
    require_match(files == expected, "capture_bytes")
    if pin.backend == "postgresql":
        require_match(all(isinstance(value, str) and bool(value)
                          for value in (capture.lexical_generation_id, capture.vector_generation_id)),
                      "fusion_component_readiness")
    else:
        require_match(capture.lexical_generation_id is None and capture.vector_generation_id is None,
                      "sqlite_component_scope")


def bind_matched_publications(
    inputs: MatchedInput,
    *,
    sqlite: VerifiedPublicationPin,
    postgres: VerifiedPublicationPin,
    sqlite_policy: BodyProjectionPolicy,
    postgres_policy: BodyProjectionPolicy,
    expected_policy_sha256: str,
    read_sqlite_capture: CaptureReader,
    read_postgres_capture: CaptureReader,
) -> MatchedPublications:
    """Verify current stores, full configured policy, and both observed file inventories."""
    require_match(type(inputs) is MatchedInput and inputs.is_factory_issued(), "unbound_evaluator_input")
    _verify_current(sqlite)
    _verify_current(postgres)
    require_match(
        sqlite.backend == "sqlite" and postgres.backend == "postgresql"
        and sqlite.graph_id == postgres.graph_id and sqlite.repository_identity == postgres.repository_identity
        and sqlite.generation == postgres.generation == 1 and sqlite.snapshot_vector == postgres.snapshot_vector,
        "publication_pair",
    )
    expected_policy = digest_value(expected_policy_sha256)
    require_match(
        full_policy_digest(sqlite_policy) == full_policy_digest(postgres_policy) == expected_policy
        and sqlite_policy.policy_digest() == sqlite.policy_digest and postgres_policy.policy_digest() == postgres.policy_digest,
        "full_policy_binding",
    )
    aliases = inputs.public_bindings + inputs.denied_bindings
    ids = {alias: str(graph_source_binding_id(sqlite.graph_id, alias)) for alias in aliases}
    admitted = frozenset(ids[alias] for alias in inputs.public_bindings)
    denied = {ids[alias]: _DENIAL for alias in inputs.denied_bindings}
    require_match(
        set(sqlite_policy.bindings) == set(ids.values()) and sqlite_policy.selected_binding_ids == admitted
        and all(sqlite_policy.bindings[value].alias == alias for alias, value in ids.items())
        and sqlite.admitted_public_bindings == postgres.admitted_public_bindings == admitted
        and sqlite.denied_bindings == postgres.denied_bindings == denied
        and admitted <= sqlite.snapshot_by_binding().keys() <= set(ids.values()),
        "configured_binding_scope",
    )
    expected = {(ids[row.binding], row.path): row.sha256 for row in inputs.files}
    sqlite_capture, postgres_capture = read_sqlite_capture(), read_postgres_capture()
    _verify_inventory(sqlite, sqlite_capture, expected_policy, expected)
    _verify_inventory(postgres, postgres_capture, expected_policy, expected)
    pair = MatchedPublications(
        inputs, sqlite, postgres, MappingProxyType(ids), frozenset(expected), expected_policy, sqlite_policy,
        sqlite_capture, postgres_capture, read_sqlite_capture, read_postgres_capture,
    )
    object.__setattr__(pair, "_issued_identity", id(pair))
    pair.revalidate()
    return pair
