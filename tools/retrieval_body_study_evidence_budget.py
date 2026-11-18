"""Finite BODY failure-evidence bookkeeping inside the unchanged read cap."""
from retrieval_body_study_contracts import (
    BODY_FAILURE_BYTES, BODY_OBSERVATION_TOTAL_BYTES, BODY_PG_COMMAND_LIMIT, BODY_PG_COMMAND_OUTPUT_BYTES,
    BODY_RESULT_MEMBERS_BYTES, MAX_MODEL_WORKERS, MAX_PLAN_BYTES, MAX_REPORT_ARCHIVE_BYTES, SLOT_COUNT, refuse,
)
from retrieval_body_study_epoch import INNER_EPOCH_IO_BYTES
from retrieval_body_study_journal_io import MAX_EVENT_BYTES, MAX_EVENTS
from test_sandbox_contract import MAX_REPORT_CONTENT_BYTES

INNER_EPOCH_PREPARATION_BYTES = MAX_MODEL_WORKERS * (MAX_PLAN_BYTES + 1 + INNER_EPOCH_IO_BYTES)


BODY_MEMBER_CONTENT_BYTES = (
    BODY_RESULT_MEMBERS_BYTES + MAX_EVENTS * MAX_EVENT_BYTES + BODY_OBSERVATION_TOTAL_BYTES
)
BODY_EXPORT_METADATA_BYTES = MAX_REPORT_CONTENT_BYTES - BODY_MEMBER_CONTENT_BYTES


def body_evidence_read_allowances() -> dict[str, int]:
    # The canonical exporter also caps all members, including receipt metadata.
    refuse(BODY_EXPORT_METADATA_BYTES > 0, "body report content budget")
    return {
        "body_report_archive_and_other_receipts": MAX_REPORT_ARCHIVE_BYTES + 128 * 1024,
        "body_host_report_and_failures": BODY_RESULT_MEMBERS_BYTES + 3,
        "body_host_journal_and_observations": (
            MAX_EVENTS * (MAX_EVENT_BYTES + 1) + BODY_OBSERVATION_TOTAL_BYTES + SLOT_COUNT
        ),
        "body_inner_failure_envelope_readbacks": 2 * (BODY_FAILURE_BYTES + 1),
        "body_pg_command_stream_reservations": BODY_PG_COMMAND_LIMIT * 2 * (BODY_PG_COMMAND_OUTPUT_BYTES + 1),
        "body_original_observation_readbacks": 6 * BODY_OBSERVATION_TOTAL_BYTES,
        "body_journal_inner_readbacks": 2 * 900 * 16 * 1024,
        "body_counter_and_extra_plan_reads": 2 * MAX_PLAN_BYTES,
        "body_inner_epoch_preparation": INNER_EPOCH_PREPARATION_BYTES,
    }
