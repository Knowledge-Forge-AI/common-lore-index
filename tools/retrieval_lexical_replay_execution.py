"""Owned generation census, immutable ledger, and descriptive paired summary."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from statistics import fmean
from typing import Any

from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_publication import lexical_connection_parameters, resolve_ops_graph
from common_lore_index.storage.lexical_driver import execute_fetchone, lexical_connection_scope
from offline_qualification_values import digest, json_bytes
from retrieval_body_catalog import BodyCase
from retrieval_body_study_metrics import METRIC_NAMES
from retrieval_evaluation_admission_w5 import AdmissionVerificationError
from retrieval_lexical_replay import (
    CANDIDATE, LEGACY, GenerationCensus, ReplayIntegrityError, ReplayShortfall,
    ReplayStore, ReplayTelemetryError, freeze_slots, observe_slot, require_integrity, schedule,
)
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_semantic_representation import RepresentationValidationError
from retrieval_supplement_files import publish_member


_CENSUS_SQL = """
SELECT a.publication_bundle_id, a.snapshot_manifest_id, a.run_id, a.lexical_generation_id,
       g.state, g.resolved_graph_privacy, g.policy_digest,
       (SELECT COALESCE(jsonb_agg(jsonb_build_array(f.binding_id, f.path)
                                 ORDER BY f.binding_id COLLATE "C", f.path COLLATE "C"), '[]')
        FROM (SELECT DISTINCT ch.binding_id, ch.source_relative_path AS path
              FROM cloe_lexical.chunks ch
              WHERE ch.repository_id = a.repository_id AND ch.lexical_generation_id = a.lexical_generation_id
              ORDER BY ch.binding_id, ch.source_relative_path LIMIT 257) f)
FROM cloe_lexical.authority a
JOIN public.repositories r ON r.id = a.repository_id
JOIN cloe_lexical.generations g ON g.repository_id = a.repository_id
                             AND g.lexical_generation_id = a.lexical_generation_id
WHERE r.repository_identity = %s
"""


def read_generation_census(config: OpsConfig, pin: VerifiedPublicationPin) -> GenerationCensus:
    """Collect the actual fenced generation and its binding-qualified files in one query."""
    graph = resolve_ops_graph(config, pin.graph_id)
    params = lexical_connection_parameters(config, graph, statement_timeout_ms=2000, read_only=True)
    with lexical_connection_scope(params, read_only=True) as connection:
        row = execute_fetchone(connection, _CENSUS_SQL, (pin.repository_identity,))
        connection.rollback()
    if row is None or len(row) != 8:
        raise ReplayIntegrityError("generation_authority_missing")
    publication, snapshot, run, generation, state, privacy, policy, raw_files = row
    require_integrity(state == "published" and privacy == "public-dev"
                      and all(isinstance(item, str) and item for item in (publication, snapshot, generation))
                      and isinstance(policy, str) and type(run) is int, "generation_authority_fields")
    if not isinstance(raw_files, list) or len(raw_files) > 256:
        raise ReplayShortfall("generation_file_census_bound")
    files = []
    for pair in raw_files:
        require_integrity(isinstance(pair, list) and len(pair) == 2
                          and all(isinstance(item, str) and item for item in pair), "census_shape")
        files.append((pair[0], pair[1]))
    return GenerationCensus(publication, snapshot, run, generation, tuple(files), policy)


def _summary(rows: Sequence[dict[str, Any]], cases: Sequence[BodyCase]) -> dict[str, Any]:
    by_slot = {(row["task_id"], row["arm"]): row for row in rows}
    deltas: dict[str, list[dict[str, float]]] = defaultdict(list)
    arm_metrics: dict[str, list[dict[str, float]]] = {LEGACY: [], CANDIDATE: []}
    paired: list[dict[str, Any]] = []
    complete = True
    for case in cases:
        if case.kind != "body_positive":
            continue
        task = case.task
        baseline = by_slot.get((task.task_id, LEGACY), {})
        candidate = by_slot.get((task.task_id, CANDIDATE), {})
        left, right = baseline.get("metrics"), candidate.get("metrics")
        if left is None or right is None:
            complete = False
            continue
        delta = {name: round(right[name] - left[name], 6) for name in METRIC_NAMES}
        arm_metrics[LEGACY].append(left)
        arm_metrics[CANDIDATE].append(right)
        deltas[task.family].append(delta)
        recovered = sorted(set(candidate["files"]) - set(baseline["files"]))
        lost = sorted(set(baseline["files"]) - set(candidate["files"]))
        paired.append({"task_id": task.task_id, "family": task.family, "delta": delta,
                       "candidate_new_files": recovered, "candidate_lost_files": lost})
    family = {key: {name: round(fmean(row[name] for row in value), 6) for name in METRIC_NAMES}
              for key, value in sorted(deltas.items())}
    aggregate = ({name: round(fmean(row["delta"][name] for row in paired), 6) for name in METRIC_NAMES}
                 if complete and paired else None)
    return {
        "body_positive_pair_deltas": paired,
        "body_positive_family_mean": family if complete else None,
        "body_positive_mean_delta": aggregate,
        "body_positive_arm_means": {
            arm: {name: round(fmean(row[name] for row in values), 6) for name in METRIC_NAMES}
            for arm, values in arm_metrics.items() if complete and values
        },
        "absence": {
            arm: dict(Counter(row.get("absence", "unresolved") for row in rows
                              if row["kind"] == "absent" and row["arm"] == arm))
            for arm in (LEGACY, CANDIDATE)
        },
    }


def paired_replay(
    cases: Sequence[BodyCase], store: ReplayStore, *,
    expected_files: tuple[tuple[str, str], ...], treatment: dict[str, str],
    retain: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Predeclare and journal each slot before spending its only query opportunity."""
    slots = schedule(cases)
    retain("ledger.json", {"slots": [slot.key() | {"state": "unstarted"} for slot in slots],
                           "treatment": treatment})
    rows: list[dict[str, Any]] = []
    outcome, reason = "complete_descriptive", None
    initial = None
    started = 0
    try:
        initial = store.census()
        initial.require_pin(store.pin, store.generation, store.lexical_policy_digest)
        if initial.files != expected_files:
            raise ReplayShortfall("candidate_file_universe_changed")
        frozen = freeze_slots(slots, store)
        material = {"generation": asdict(initial), "publication_pin": store.pin.to_dict(),
                    "policy_sha256": digest(json_bytes(store.policy.to_dict())),
                    "slots": [slot.entry() for slot in frozen], "treatment": treatment}
        retain("preflight.json", material | {"treatment_sha256": digest(json_bytes(material))})
        for index, slot in enumerate(frozen, 1):
            retain(f"slot-{index:02d}-started.json", slot.entry())
            started += 1
            result = observe_slot(slot, store, initial)
            retain(f"slot-{index:02d}-observed.json", result)
            rows.append(result)
            if result["state"] == "inadmissible":
                raise ReplayIntegrityError(result["reason"])
        if any(row["state"] in {"failed", "not_run"}
               or (row["kind"] == "body_positive" and row["metrics"] is None) for row in rows):
            outcome, reason = "incomplete_technical", "query_failure_or_not_run"
    except ReplayIntegrityError as error:
        outcome, reason = "inadmissible", str(error)
    except (AdmissionVerificationError, RepresentationValidationError) as error:
        outcome, reason = "inadmissible", type(error).__name__
    except ReplayShortfall as error:
        outcome, reason = ("inadmissible" if started else "inconclusive_preflight"), str(error)
    except (ReplayTelemetryError, KeyboardInterrupt) as error:
        outcome, reason = "incomplete_technical", type(error).__name__
    except Exception as error:
        outcome, reason = "incomplete_technical", type(error).__name__
    if initial is not None:
        try:
            final = store.census()
            final.require_pin(store.pin, store.generation, store.lexical_policy_digest)
            require_integrity(final == initial, "generation_census_drift")
            retain("exit-census.json", asdict(final))
        except ReplayIntegrityError:
            outcome, reason = "inadmissible", "exit_generation_drift"
        except Exception:
            if outcome != "inadmissible":
                outcome, reason = "incomplete_technical", "exit_census_unverified"
    return {
        "schema": "cloe-w5-secondary-lexical-replay-result-v1",
        "outcome": outcome, "reason": reason, "planned_slots": len(slots),
        "started_slots": started, "observed_slots": len(rows),
        "query_calls_observed": sum(bool(row["query_started"]) for row in rows),
        "treatment": treatment, "observations": rows,
        "quality": _summary(rows, cases) if outcome == "complete_descriptive" else None,
        "acceptance": "secondary_descriptive_only",
        "physical_postgresql_io": "unknown",
    }


class ReplayJournal:
    """Publish each report member once into the sandbox-owned evidence directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._written_bytes = 0

    def retain(self, name: str, document: dict[str, Any]) -> None:
        data = json_bytes(document)
        # Leave 1 MiB for archive headers and padding under the 4 MiB transport cap.
        maximum_member = 512 * 1024
        ceiling = 3 * 1024**2
        if name != "terminal.json":
            ceiling -= maximum_member
        if len(data) > maximum_member or self._written_bytes + len(data) > ceiling:
            raise RuntimeError("lexical replay receipt exceeds bounded size")
        publish_member(self.root, name, data)
        self._written_bytes += len(data)
