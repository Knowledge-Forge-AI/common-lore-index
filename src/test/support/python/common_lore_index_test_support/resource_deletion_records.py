"""Compatibility surface for strict durable quarantine deletion records."""

from common_lore_index_test_support.resource_deletion_record_store import (
    DeletionRecordStore,
    deletion_registration_state,
)
from common_lore_index_test_support.resource_deletion_record_types import (
    BARRIER_SCHEMA,
    COMPLETION_SCHEMA,
    DeletionEvidence,
    DeletionRecordError,
    DeletionRecoveryState,
    INTENT_SCHEMA,
    MAX_TIMESTAMP_SECONDS,
    PROGRESS_SCHEMA,
    RESTORATION_SCHEMA,
    TOMBSTONE_SCHEMA,
)
from common_lore_index_test_support.resource_deletion_record_validation import quarantine_ttl_elapsed


__all__ = [
    "BARRIER_SCHEMA",
    "COMPLETION_SCHEMA",
    "DeletionEvidence",
    "DeletionRecordError",
    "DeletionRecordStore",
    "DeletionRecoveryState",
    "INTENT_SCHEMA",
    "MAX_TIMESTAMP_SECONDS",
    "PROGRESS_SCHEMA",
    "RESTORATION_SCHEMA",
    "TOMBSTONE_SCHEMA",
    "deletion_registration_state",
    "quarantine_ttl_elapsed",
]
