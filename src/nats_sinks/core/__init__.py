# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Core runtime components.

The core package contains the delivery machinery that every sink shares:
message normalization, configuration, batching, retry and DLQ decisions,
metrics hooks, lifecycle helpers, and the `JetStreamSinkRunner`.

The boundary is deliberate.  Core code may know about NATS and JetStream
acknowledgement behavior; sink code may know about destination writes.  Keeping
that boundary sharp prevents accidental early ACKs and makes future sinks such
as Postgres, HTTP, file, S3, and Kafka easier to certify against the same
contract.
"""

from nats_sinks.core.encryption import (
    ENCRYPTED_PAYLOAD_KEY,
    PayloadEncryptor,
    PayloadKeyRegistry,
    SubjectPayloadEncryptor,
    decrypt_payload,
    is_encrypted_payload_envelope,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.message_metadata import (
    DEFAULT_CLASSIFICATION_HEADER,
    DEFAULT_LABELS_HEADER,
    DEFAULT_PRIORITY_HEADER,
)
from nats_sinks.core.metadata import (
    NATS_RESERVED_HEADER_NAMES,
    build_nats_metadata_snapshot,
    datetime_to_epoch_ns,
)
from nats_sinks.core.metrics import (
    DEFAULT_METRIC_NAMESPACE,
    LEGACY_METRIC_ALIASES,
    METRIC_SPECS,
    InMemoryMetrics,
    JsonFileMetrics,
    MetricNames,
    NoopMetrics,
    load_metrics_snapshot,
    metric_rows_from_snapshot,
    qualified_metric_name,
    validate_metric_namespace,
    write_metrics_snapshot,
)
from nats_sinks.core.mission_metadata import (
    DEFAULT_MISSION_METADATA_HEADER,
    MISSION_METADATA_PROFILE_VERSION,
    parse_mission_metadata_header,
)
from nats_sinks.core.payload import (
    NormalizedPayload,
    PayloadOriginalFormat,
    PayloadStorageMode,
    normalize_payload_for_json_storage,
)
from nats_sinks.core.policy import PolicyEvaluation, PolicyViolation, evaluate_pre_sink_policy
from nats_sinks.core.priority import PriorityLaneAssignment, order_by_priority_lanes
from nats_sinks.core.runner import JetStreamSinkRunner

__all__ = [
    "DEFAULT_CLASSIFICATION_HEADER",
    "DEFAULT_LABELS_HEADER",
    "DEFAULT_METRIC_NAMESPACE",
    "DEFAULT_MISSION_METADATA_HEADER",
    "DEFAULT_PRIORITY_HEADER",
    "ENCRYPTED_PAYLOAD_KEY",
    "LEGACY_METRIC_ALIASES",
    "METRIC_SPECS",
    "MISSION_METADATA_PROFILE_VERSION",
    "NATS_RESERVED_HEADER_NAMES",
    "InMemoryMetrics",
    "JetStreamSinkRunner",
    "JsonFileMetrics",
    "MetricNames",
    "NatsEnvelope",
    "NoopMetrics",
    "NormalizedPayload",
    "PayloadEncryptor",
    "PayloadKeyRegistry",
    "PayloadOriginalFormat",
    "PayloadStorageMode",
    "PolicyEvaluation",
    "PolicyViolation",
    "PriorityLaneAssignment",
    "SubjectPayloadEncryptor",
    "build_nats_metadata_snapshot",
    "datetime_to_epoch_ns",
    "decrypt_payload",
    "evaluate_pre_sink_policy",
    "is_encrypted_payload_envelope",
    "load_metrics_snapshot",
    "metric_rows_from_snapshot",
    "normalize_payload_for_json_storage",
    "order_by_priority_lanes",
    "parse_mission_metadata_header",
    "qualified_metric_name",
    "validate_metric_namespace",
    "write_metrics_snapshot",
]
