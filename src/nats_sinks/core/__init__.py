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

from nats_sinks.core.advisory import (
    DEFAULT_ADVISORY_SUBJECTS,
    JetStreamAdvisory,
    JetStreamAdvisoryMonitor,
    advisory_kind_from_subject,
    observe_jetstream_advisory_message,
    parse_jetstream_advisory,
    validate_advisory_subject,
)
from nats_sinks.core.custody import (
    CUSTODY_SCHEMA,
    CUSTODY_SUPPORTED_ALGORITHMS,
    attach_custody_metadata,
    canonical_json_bytes,
    compute_custody_metadata,
)
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
    "CUSTODY_SCHEMA",
    "CUSTODY_SUPPORTED_ALGORITHMS",
    "DEFAULT_ADVISORY_SUBJECTS",
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
    "JetStreamAdvisory",
    "JetStreamAdvisoryMonitor",
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
    "advisory_kind_from_subject",
    "attach_custody_metadata",
    "build_nats_metadata_snapshot",
    "canonical_json_bytes",
    "compute_custody_metadata",
    "datetime_to_epoch_ns",
    "decrypt_payload",
    "evaluate_pre_sink_policy",
    "is_encrypted_payload_envelope",
    "load_metrics_snapshot",
    "metric_rows_from_snapshot",
    "normalize_payload_for_json_storage",
    "observe_jetstream_advisory_message",
    "order_by_priority_lanes",
    "parse_jetstream_advisory",
    "parse_mission_metadata_header",
    "qualified_metric_name",
    "validate_advisory_subject",
    "validate_metric_namespace",
    "write_metrics_snapshot",
]
