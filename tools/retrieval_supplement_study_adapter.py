"""Supplement preparation algebra and injected worker-request seam validation.

Input is the inert ``prepare_supplement`` output (two exchanges: 66+20=86 and
40+20=60 records = 146 total). This module re-validates record algebra,
exact reference joins, query/document ordering and duplicates, and validates
worker requests built through an injected seam. Live binding to
``PublicGenerationInput`` and ``PublicTextPreparation`` lives in
``retrieval_supplement_study_live_requests`` and is re-exported here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from retrieval_semantic_generator_worker_contracts import ATTEMPT_ID_PATTERN
from retrieval_supplement_preparation import PREPARATION_SCHEMA, REPRESENTATIONS
from retrieval_supplement_study_contracts import (
    SLOT_COUNT, TOTAL_RECORDS, V1_DOCUMENTS, V1_QUERIES, V2_DOCUMENTS, V2_QUERIES, refuse,
)
from retrieval_supplement_primitives import ARMS, REPETITIONS, canonical_bytes

InputFactory = Callable[[str, tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]], Any]
RequestBuilder = Callable[..., Mapping[str, Any]]

EXPECTED_COUNTS = ((V1_DOCUMENTS, V1_QUERIES), (V2_DOCUMENTS, V2_QUERIES))


@dataclass(frozen=True)
class WorkerExchange:
    representation: str
    arm: str
    exchange_id: str
    documents: tuple[Mapping[str, Any], ...]
    queries: tuple[Mapping[str, Any], ...]

    @property
    def record_count(self) -> int:
        return len(self.documents) + len(self.queries)


def _rows(value: object, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        refuse(False, f"adapter:{label}_list_required")
        return ()
    if type(value) is not list:
        refuse(False, f"adapter:{label}_list_required")
        return ()
    rows: tuple[Mapping[str, Any], ...] = tuple(
        row for row in value if isinstance(row, Mapping)
    )
    refuse(len(rows) == len(value), f"adapter:{label}_objects_required")
    return rows


def attempt_for_exchange(attempt_id: str, representation: str) -> str:
    candidate = f"{attempt_id}-{representation}"
    refuse(ATTEMPT_ID_PATTERN.fullmatch(candidate) is not None, "adapter:attempt_id_invalid")
    return candidate


def _validate_references(
    references: Sequence[Mapping[str, Any]], v1: WorkerExchange,
    v2: WorkerExchange, ordered_task_ids: Sequence[str] | None,
) -> None:
    v1_tasks = [str(row.get("task_id")) for row in v1.queries]
    v2_tasks = [str(row.get("task_id")) for row in v2.queries]
    if ordered_task_ids is not None:
        order_index = {task_id: idx for idx, task_id in enumerate(ordered_task_ids)}
        for task_list in (v1_tasks, v2_tasks):
            refuse(all(task_id in order_index for task_id in task_list), "adapter:unknown_query_task")
            positions = [order_index[task_id] for task_id in task_list]
            refuse(positions == sorted(positions), "adapter:query_order_drift")
    seen_slots: set[tuple[str, str, int]] = set()
    fusion_tasks_by_rep: dict[int, list[str]] = {rep: [] for rep in range(1, REPETITIONS + 1)}
    v1_expected = {
        (ARMS[3], task_id, rep): (v1.exchange_id, f"{v1.exchange_id}:{task_id}")
        for task_id in v1_tasks for rep in range(1, REPETITIONS + 1)
    }
    v2_expected = {
        (ARMS[4], task_id, rep): (v2.exchange_id, f"{v2.exchange_id}:{task_id}")
        for task_id in v2_tasks for rep in range(1, REPETITIONS + 1)
    }
    observed_v1_v2: dict[tuple[str, str, int], tuple[str, str]] = {}
    for ref in references:
        arm = str(ref.get("arm", ""))
        task_id = str(ref.get("task_id", ""))
        rep = ref.get("repetition")
        exchange_id = str(ref.get("exchange_id", ""))
        query_id = str(ref.get("query_id", ""))
        refuse(arm in (ARMS[3], ARMS[4], ARMS[5]), "adapter:reference_arm")
        refuse(type(rep) is int and 1 <= int(rep) <= REPETITIONS, "adapter:reference_repetition")
        assert isinstance(rep, int)
        slot_key = (arm, task_id, rep)
        refuse(slot_key not in seen_slots, "adapter:duplicate_reference")
        seen_slots.add(slot_key)
        if arm in (ARMS[3], ARMS[4]):
            observed_v1_v2[slot_key] = (exchange_id, query_id)
        else:
            refuse(task_id in set(v2_tasks), "adapter:fusion_reference_without_v2_query")
            refuse(exchange_id == v2.exchange_id and query_id == f"{v2.exchange_id}:{task_id}",
                   "adapter:fusion_reference_join_mismatch")
            fusion_tasks_by_rep[rep].append(task_id)
    refuse(observed_v1_v2 == {**v1_expected, **v2_expected}, "adapter:reference_join_mismatch")
    first_fusion = fusion_tasks_by_rep[1]
    refuse(all(fusion_tasks_by_rep[rep] == first_fusion for rep in range(1, REPETITIONS + 1)),
           "adapter:fusion_repetition_asymmetry")
    expected_ref_count = len(v1_expected) + len(v2_expected) + REPETITIONS * len(first_fusion)
    refuse(len(references) == expected_ref_count, "adapter:repetition_references")


def parse_exchanges(
    preparation: Mapping[str, Any], *,
    expected_counts: Sequence[tuple[int, int]] = EXPECTED_COUNTS,
    expected_total: int = TOTAL_RECORDS,
    expected_slots: int = SLOT_COUNT,
) -> tuple[WorkerExchange, WorkerExchange]:
    """Validate frozen counts, query/document order, and exact repetition joins."""
    refuse(preparation.get("schema") == PREPARATION_SCHEMA, "adapter:preparation_schema")
    refuse(preparation.get("execution_authorized") is False, "adapter:preparation_must_be_inert")
    refuse(preparation.get("queries_executed") is False
           and preparation.get("model_workers_started") == 0, "adapter:preparation_spent")
    refuse(tuple(preparation.get("arms", ())) == ARMS, "adapter:arm_order")
    refuse(preparation.get("repetitions") == REPETITIONS, "adapter:repetitions")
    refuse(preparation.get("slot_count") == expected_slots, "adapter:slot_count")
    ordered_raw = preparation.get("ordered_task_ids")
    ordered_task_ids: list[str] | None = None
    if ordered_raw is not None:
        refuse(type(ordered_raw) is list and all(type(item) is str and bool(item) for item in ordered_raw)
               and len(set(ordered_raw)) == len(ordered_raw), "adapter:ordered_task_ids")
        ordered_task_ids = [str(item) for item in ordered_raw]
    exchanges = _rows(preparation.get("exchanges"), "exchanges")
    refuse(len(exchanges) == 2, "adapter:two_representations_required")
    parsed: list[WorkerExchange] = []
    seen_queries: set[str] = set()
    for index, (arm, representation) in enumerate(REPRESENTATIONS):
        exchange = exchanges[index]
        refuse(exchange.get("arm") == arm and exchange.get("representation") == representation,
               "adapter:representation_order")
        exchange_id = str(exchange.get("exchange_id", ""))
        refuse(bool(exchange_id), "adapter:exchange_id_required")
        documents = _rows(exchange.get("documents"), "documents")
        queries = _rows(exchange.get("queries"), "queries")
        refuse((len(documents), len(queries)) == tuple(expected_counts[index]),
               "adapter:frozen_record_counts")
        refuse(exchange.get("document_count") == len(documents)
               and exchange.get("query_count") == len(queries)
               and exchange.get("record_count") == len(documents) + len(queries)
               and exchange.get("embedding_count_expected") == len(documents) + len(queries),
               "adapter:count_algebra")
        doc_keys = [(str(row.get("binding_id")), str(row.get("canonical_key"))) for row in documents]
        refuse(doc_keys == sorted(doc_keys) and len(set(doc_keys)) == len(doc_keys),
               "adapter:document_order_or_duplicate")
        query_ids = [str(row.get("query_id")) for row in queries]
        task_ids = [str(row.get("task_id")) for row in queries]
        refuse(len(set(query_ids)) == len(query_ids) and len(set(task_ids)) == len(task_ids),
               "adapter:duplicate_query")
        refuse(query_ids == [f"{exchange_id}:{task_id}" for task_id in task_ids],
               "adapter:query_exchange_binding")
        refuse(not (set(query_ids) & seen_queries), "adapter:cross_representation_query_reuse")
        seen_queries.update(query_ids)
        refuse(all(type(row.get("text")) is str and bool(str(row["text"]).strip()) for row in queries),
               "adapter:query_text_required")
        parsed.append(WorkerExchange(representation, arm, exchange_id, documents, queries))
    refuse(sum(item.record_count for item in parsed) == expected_total, "adapter:total_records")
    references = _rows(preparation.get("references"), "references")
    _validate_references(references, parsed[0], parsed[1], ordered_task_ids)
    return parsed[0], parsed[1]


def validate_built_request_records(
    request: Mapping[str, Any], exchange: WorkerExchange, inputs: Any,
) -> None:
    records = request.get("records")
    refuse(type(records) is list and len(records) == exchange.record_count,
           "adapter:request_record_count")
    assert isinstance(records, list)
    seen_ids: set[str] = set()
    for row in records:
        refuse(isinstance(row, Mapping), "adapter:request_record_object")
        rec_id, mode, text = row.get("record_id"), row.get("mode"), row.get("text")
        refuse(type(rec_id) is str and bool(rec_id) and rec_id not in seen_ids,
               "adapter:request_record_id")
        refuse(mode in {"document", "query"}, "adapter:request_record_mode")
        refuse(type(text) is str and bool(text.strip()), "adapter:request_record_text")
        seen_ids.add(str(rec_id))
    doc_rows = records[:len(exchange.documents)]
    query_rows = records[len(exchange.documents):]
    refuse(all(row["mode"] == "document" for row in doc_rows), "adapter:document_partition_order")
    refuse(all(row["mode"] == "query" for row in query_rows), "adapter:query_partition_order")
    for row, expected_query in zip(query_rows, exchange.queries, strict=True):
        task_id = str(expected_query["task_id"])
        refuse(row["text"] == expected_query["text"], "adapter:query_text_mismatch")
        rec_id = str(row["record_id"])
        refuse(rec_id in {expected_query["query_id"], f"query:{task_id}", f"ptx.q.{task_id}"},
               "adapter:query_record_identity_mismatch")
    if isinstance(inputs, Mapping) and isinstance(inputs.get("records"), list):
        expected_tuples = [(str(r.get("record_id")), str(r.get("mode")), str(r.get("text")))
                           for r in inputs["records"]]
        actual_tuples = [(str(r["record_id"]), str(r["mode"]), str(r["text"])) for r in records]
        refuse(actual_tuples == expected_tuples, "adapter:request_input_drift")


def build_study_requests(
    preparation: Mapping[str, Any], *, attempt_id: str,
    artifact_profile_identity: str, make_inputs: InputFactory,
    build_request: RequestBuilder,
    expected_counts: Sequence[tuple[int, int]] = EXPECTED_COUNTS,
    expected_total: int = TOTAL_RECORDS,
    expected_slots: int = SLOT_COUNT,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Build two sequential worker requests through an injected seam and check 1-to-1 records."""
    refuse(type(attempt_id) is str and ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is not None,
           "adapter:attempt_required")
    v1, v2 = parse_exchanges(
        preparation, expected_counts=expected_counts,
        expected_total=expected_total, expected_slots=expected_slots,
    )
    requests: list[Mapping[str, Any]] = []
    for exchange in (v1, v2):
        scoped_attempt = attempt_for_exchange(attempt_id, exchange.representation)
        inputs = make_inputs(exchange.representation, exchange.documents, exchange.queries)
        refuse(inputs is not None, "adapter:inputs_refused")
        request = build_request(
            inputs, attempt_id=scoped_attempt,
            artifact_profile_identity=artifact_profile_identity,
        )
        refuse(isinstance(request, Mapping), "adapter:request_object_required")
        refuse(request.get("attempt_id") == scoped_attempt, "adapter:request_attempt_mismatch")
        validate_built_request_records(request, exchange, inputs)
        requests.append(request)
    return requests[0], requests[1]


def request_payload_bytes(request: Mapping[str, Any]) -> bytes:
    return canonical_bytes(json.loads(json.dumps(dict(request))))


__all__ = [
    "EXPECTED_COUNTS", "InputFactory", "RequestBuilder", "WorkerExchange",
    "attempt_for_exchange", "build_study_requests", "parse_exchanges",
    "request_payload_bytes", "validate_built_request_records",
]
