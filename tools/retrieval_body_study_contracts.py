"""Closed constants for the source-bound CLOE-W5 body study.

The body study has its own phase, role, schemas, and admission marker.  The
runtime image and the public worker source are retained assets, not evidence
that an old supplement attempt is allowed to run.  This module is deliberately
leaf-like: importing it performs no I/O and grants no execution authority.
"""
from __future__ import annotations

import json
import math
from typing import Final
from typing import Any

BODY_PHASE: Final = "CLOE-W5-BODY-STUDY1"
BODY_ROLE: Final = "body-study"
BODY_PROFILE: Final = "pgvector"
BODY_SELECTOR: Final = "src/test/int/python/tools/retrieval_body_study_native.int.test.py"
BODY_NODE_ID: Final = f"{BODY_SELECTOR}::test_body_study_actual_attempt"
BODY_MARKER: Final = "requires_body_study_admission"
BODY_PLAN_SCHEMA: Final = "cloe-body-study-plan-v1"
BODY_INNER_PLAN_SCHEMA: Final = "cloe-body-study-inner-plan-v1"
BODY_ADMISSION_SCHEMA: Final = "cloe-body-study-admission-v1"
BODY_RESERVATION_SCHEMA: Final = "cloe-body-study-reservation-v1"
BODY_REPORT_SCHEMA: Final = "cloe-body-study-report-v1"
BODY_ACTUAL_REPORT_SCHEMA: Final = "cloe-body-study-actual-report-v1"
BODY_OPERATION_SCHEMA: Final = "cloe-body-study-operation-v1"
BODY_SCHEDULE_SCHEMA: Final = "cloe-body-study-schedule-v1"
BODY_FORECAST_SCHEMA: Final = "cloe-body-study-byte-forecast-v1"
BODY_PARENT_ADMISSION_SCHEMA: Final = "cloe-body-study-parent-admission-v1"
BODY_INNER_RECEIPT_SCHEMA: Final = "cloe-body-study-inner-source-read-receipt-v1"

ENV_BODY_PLAN: Final = "CLOE_BODY_STUDY_PLAN"
ENV_BODY_ADMISSION: Final = "CLOE_BODY_STUDY_ADMISSION"
ENV_OFFLINE_PLAN: Final = "CLOE_PGVECTOR_OFFLINE_PLAN"
INNER_BODY_REPORT_DIR: Final = "/sandbox-report/report/body-study"
INNER_BODY_SCRATCH_DIR: Final = "/sandbox-scratch/pgvector"
OPERATOR_SELECTION: Final = "ADR0206 Route A"
BODY_PROTOCOL_SHA256: Final = "sha256:ea8714d3b5ba224ae5a5245f8dc028b48d7793042fd3952d8b1ead342886b7ce"
BODY_OPERATOR_DECISION_SHA256: Final = "sha256:7b7afb13f73fc4c1d4efefc070233cb10d73de18ead5a070b686431f26a8a6ae"

# Registered body-v2 fixture input pin.  This is an input binding, never an
# identity for a supplement or a predecessor phase.
BODY_INPUT_PIN_SHA256: Final = "sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b"
BODY_SDK_IMAGE_SHA256: Final = "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42"
BODY_PROVIDER_SHA256: Final = "b366c8507b36746ea1512c4a9a3d6819214b65203645b7c9555bd450d5f797ec"
BODY_PACKAGE_SHA256: Final = "fa7c26fe2f076538915fa7efac6cd944777d891ccb251324bea01fda35626919"
BODY_BASE_CONFIG_SHA256: Final = "sha256:2e7ed4ea03deedbb45c26c68bf2a7090dc68dd8518a9c75928c4b9b0f59bc411"
BODY_RUNTIME_CONFIG_SHA256: Final = "sha256:4b8c5fb11ee4bd545a4cc7db662932d74c8213e0016bbb92d3d6b5809476a1f8"
BODY_RUNTIME_ARCHIVE_BYTES: Final = 502_569_984
BODY_RUNTIME_ARCHIVE_SHA256: Final = "sha256:efa131b8123911f523845d4b36411936a2e63a66c15457cdc5ac86cd4c7a3a66"
BODY_SDK_RECIPE_SHA256: Final = "24ddf31d8890922c012559d5cab4b1dffe4b41e5b19e9b3438555aabe22d2862"
# The retained artifact is a pinned input to each sibling worker.  These
# aliases are BODY-scoped names for the maintained artifact profile; they do
# not make an old supplement plan admissible.
BODY_ARTIFACT_SOURCE_IDENTITY_SHA256: Final = "sha256:033cf41ffc5ca108b6e26638573554bac64b84be744903809904ef3364722180"
BODY_ARTIFACT_PIN_DOCUMENT_SHA256: Final = "sha256:f0237e38018f3524b87f85f7fff2bef41eda125b8f954d575aaee233541896b6"
BODY_ARTIFACT_FILE_COUNT: Final = 30
BODY_ARTIFACT_TOTAL_BYTES: Final = 182_037_753
# The maintained public-text worker helper owns this identity domain.  The
# host still obtains and rehashes its current 17-file map on every authority
# hop; this constant is only the BODY adapter's fixed worker join.
BODY_WORKER_CLOSURE_SHA256: Final = "sha256:3ef59123fd9281409d42a8e0f4b02471ec3088c54429033d62cbd6b51af01e23"
BODY_WORKER_MEMORY_BYTES: Final = 2 * 1024**3
BODY_WORKER_SCRATCH_BYTES: Final = 768 * 1024**2
BODY_WORKER_TMP_BYTES: Final = 64 * 1024**2
MAX_RUNTIME_IMPORTS: Final = 1
MAX_RUNTIME_EXPORTS: Final = 0
WORKER_CLOSURE_FILES: Final = 17

V1_DOCUMENTS: Final = 66
V1_QUERIES: Final = 4
V2_DOCUMENTS: Final = 44
V2_QUERIES: Final = 20
TOTAL_RECORDS: Final = V1_DOCUMENTS + V1_QUERIES + V2_DOCUMENTS + V2_QUERIES
TASK_COUNT: Final = 24
ARM_COUNT: Final = 6
REPETITIONS: Final = 3
SLOT_COUNT: Final = TASK_COUNT * ARM_COUNT * REPETITIONS
MAX_MODEL_WORKERS: Final = 2
BODY_SDK_SECONDS: Final = 4_800
BODY_CONTROLLER_SECONDS: Final = 5_400
WORKER_READINESS_SECONDS: Final = 120
WORKER_INIT_SECONDS: Final = 600
WORKER_MODEL_SECONDS: Final = 550
WORKER_AGGREGATE_SECONDS: Final = 1_700
PG_TMPFS_BYTES: Final = 256 * 1024**2
PG_SHM_BYTES: Final = 64 * 1024**2
WORKER_MEMORY_BYTES: Final = 2 * 1024**3
WORKER_SCRATCH_BYTES: Final = 768 * 1024**2
WORKER_TMP_BYTES: Final = 64 * 1024**2
PG_MEMORY_BYTES: Final = 2 * 1024**3
AUTOMATIC_DEFECT_CLASSES: Final = frozenset({"transport", "telemetry", "cleanup"})

MAX_ARCHIVE_BYTES: Final = 768 * 1024**2
MAX_PLAN_BYTES: Final = 1024**2
MAX_READ_BYTES: Final = 12 * 1024**3
MAX_TRANSFER_BYTES: Final = 6 * 1024**3
MAX_SCRATCH_BYTES: Final = 8 * 1024**3
MAX_PROVIDER_BYTES: Final = 4 * 1024**2
REGISTERED_SNAPSHOT_READ_BYTES: Final = 12 * 1024**2
MAX_REQUEST_STREAM_BYTES: Final = 8 * 1024**2
MAX_RESPONSE_STREAM_BYTES: Final = 32 * 1024**2
MAX_REPORT_ARCHIVE_BYTES: Final = 72 * 1024**2
BODY_REPORT_MEMBER: Final = "body-study/body-study-report-v1.json"
BODY_FAILURE_MEMBER: Final = "body-study/body-failure-envelope-v1.json"
BODY_FAILURE_EVIDENCE_MEMBER: Final = "body-study/body-failure-evidence-v1.json"
BODY_RESULT_MEMBERS_BYTES: Final = 32 * 1024**2
BODY_REPORT_BYTES: Final = BODY_RESULT_MEMBERS_BYTES
BODY_FAILURE_BYTES: Final = 4 * 1024**2
BODY_PG_COMMAND_LIMIT: Final = 140
BODY_PG_COMMAND_OUTPUT_BYTES: Final = 64 * 1024
BODY_OBSERVATION_TOTAL_BYTES: Final = 16 * 1024**2
MAX_SOURCE_CLOSURE_BYTES: Final = 16 * 1024**2
MAX_DOCKER_READ_BYTES: Final = 2 * 1024**3
MAX_DOCKER_OPERATIONS: Final = 2_048
MAX_DOCKER_STREAM_FORECAST_BYTES: Final = 512 * 1024**2
# Host freshness follows the qualified bounded-source protocol.  The separate
# inner read ledger is deliberately tighter: it covers whole-candidate reads,
# not host source guards that bracket every Docker/model authority hop.
# The host exchange and two 550-second model windows have a finite guarded-hop
# budget.  Keep headroom for readiness/ACK and failure settlement while making
# the byte forecast a real bound rather than charging an unreachable 1024-hop
# maximum.
SOURCE_VERIFICATION_LIMIT: Final = 400
INNER_SOURCE_GUARD_LIMIT: Final = 512
INNER_SOURCE_READ_LIMIT: Final = 16
REGISTERED_INPUT_CHECKS: Final = 4
PARENT_ADMISSION_PINS: Final = 5
PARENT_ADMISSION_CHECKS: Final = 5
BODY_POLL_SECONDS: Final = 5.0
BODY_MODEL_POLL_SECONDS: Final = 30.0
BODY_OUTER_MARKER_PROGRAM: Final = (
    "from pathlib import Path;import hashlib,re;"
    "v=Path('/run/repomap-test-sandbox-owner').read_bytes().strip();"
    "assert re.fullmatch(b'[0-9a-f]{64}',v);"
    "print('sha256:'+hashlib.sha256(v).hexdigest())"
)

BODY_ARMS: Final = (
    "native_tools_baseline",
    "current_graph_baseline:postgresql",
    "postgres_native_fts:postgresql",
    "exact_vector:canonical-node-v1",
    "exact_vector:canonical-node-public-text-v2",
    "hybrid_rrf:canonical-node-public-text-v2",
)
BODY_REPRESENTATIONS: Final = ("canonical-node-v1", "canonical-node-public-text-v2")
BODY_OUTCOMES: Final = frozenset({
    "calibration_signal_present", "signal_not_demonstrated", "inconclusive_floor",
    "inadmissible", "incomplete_technical",
})
BODY_CLEANUP_STATES: Final = frozenset({"proven", "unknown", "failed"})


class BodyStudyRefusal(ValueError):
    """Public-safe validation failure; raw paths and payloads never escape."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def refuse(condition: object, code: str) -> None:
    if not condition:
        raise BodyStudyRefusal(code)


StudyRefusal = BodyStudyRefusal

MAX_BODY_JSON_BYTES: Final = 32 * 1024**2
MAX_BODY_JSON_DEPTH: Final = 32


def _body_json_depth(text: str) -> int:
    depth = peak = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            peak = max(peak, depth)
        elif char in "]}":
            depth -= 1
    return peak


def strict_json(raw: bytes, *, maximum_bytes: int = MAX_BODY_JSON_BYTES) -> Any:
    """Decode bounded canonical-enough JSON without old study identity state."""
    refuse(type(maximum_bytes) is int and 0 < maximum_bytes <= MAX_BODY_JSON_BYTES,
           "body json limit")
    refuse(type(raw) is bytes and len(raw) <= maximum_bytes, "body json bytes")
    try:
        text = raw.decode("utf-8")
        refuse(not text.startswith("\ufeff") and _body_json_depth(text) <= MAX_BODY_JSON_DEPTH,
               "body json framing")

        def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                refuse(key not in result, "body json duplicate")
                result[key] = value
            return result

        def reject_constant(_token: str) -> Any:
            raise BodyStudyRefusal("body json nonfinite")

        def finite_float(token: str) -> float:
            value = float(token)
            refuse(math.isfinite(value), "body json nonfinite")
            return value

        return json.loads(text, object_pairs_hook=no_duplicates,
                          parse_constant=reject_constant, parse_float=finite_float)
    except BodyStudyRefusal:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise BodyStudyRefusal("body json malformed") from None


# Copied adapters may use the old generic spelling internally.  These aliases
# are deliberately BODY values and are not permission to accept old plans.
STUDY_PHASE = BODY_PHASE
STUDY_ROLE = BODY_ROLE
STUDY_PROFILE = BODY_PROFILE
STUDY_SELECTOR = BODY_SELECTOR
STUDY_NODE_ID = BODY_NODE_ID
STUDY_MARKER = BODY_MARKER
STUDY_PLAN_SCHEMA = BODY_PLAN_SCHEMA
STUDY_INNER_PLAN_SCHEMA = BODY_INNER_PLAN_SCHEMA
STUDY_ADMISSION_SCHEMA = BODY_ADMISSION_SCHEMA
STUDY_RESERVATION_SCHEMA = BODY_RESERVATION_SCHEMA
STUDY_REPORT_SCHEMA = BODY_REPORT_SCHEMA
STUDY_SDK_SECONDS = BODY_SDK_SECONDS
STUDY_CONTROLLER_SECONDS = BODY_CONTROLLER_SECONDS
SDK_IMAGE_SHA256 = BODY_SDK_IMAGE_SHA256
PROVIDER_MANIFEST_SHA256 = "sha256:" + BODY_PROVIDER_SHA256
PROVIDER_PACKAGE_SHA256 = BODY_PACKAGE_SHA256
BODY_PROVIDER_MANIFEST_SHA256 = PROVIDER_MANIFEST_SHA256
INPUT_PIN_SHA256 = BODY_INPUT_PIN_SHA256
RUNTIME_CONFIG_SHA256 = BODY_RUNTIME_CONFIG_SHA256
RUNTIME_ARCHIVE_BYTES = BODY_RUNTIME_ARCHIVE_BYTES
RUNTIME_ARCHIVE_SHA256 = BODY_RUNTIME_ARCHIVE_SHA256

__all__ = [name for name in globals() if not name.startswith("_")]
