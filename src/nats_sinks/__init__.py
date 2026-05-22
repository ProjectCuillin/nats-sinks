# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Public API for nats-sinks.

The top-level package intentionally exports only the stable framework surface
that application developers should import directly.  The expected user
experience is small and explicit: import `JetStreamSinkRunner`, choose a sink
such as `OracleSink`, and let the runner own JetStream delivery semantics.

Nothing in this module opens sockets, reads configuration files, imports the
Oracle driver, or performs any other side effect.  Import safety matters for
CLIs, test suites, type checkers, and documentation generators.  Optional sink
drivers are imported by their sink implementations only when they are started.

The most important public invariant is commit-then-acknowledge processing:
framework users may construct sinks, but ACK decisions remain in the core
runner and are never delegated to destination code.
"""

from nats_sinks.core.config import (
    CustodyConfig,
    EncryptionConfig,
    EncryptionRuleConfig,
    MessageMetadataConfig,
    MessageMetadataFieldConfig,
    MessageMetadataLabelsConfig,
    MessageMetadataRuleConfig,
    MissionMetadataConfig,
    MissionMetadataRuleConfig,
    PreSinkPolicyConfig,
    PreSinkPolicyRuleConfig,
    PriorityLaneConfig,
    PriorityLanesConfig,
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
from nats_sinks.core.errors import (
    AckError,
    ConfigurationError,
    DeadLetterError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
    PolicyViolationError,
    RetryExhaustedError,
    SerializationError,
    SinkError,
    TemporarySinkError,
    ValidationError,
)
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
    METRIC_SPECS,
    InMemoryMetrics,
    JsonFileMetrics,
    MetricNames,
    NoopMetrics,
    load_metrics_snapshot,
    metric_rows_from_snapshot,
    qualified_metric_name,
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
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.file import FileSink
from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink

__all__ = [
    "CUSTODY_SCHEMA",
    "CUSTODY_SUPPORTED_ALGORITHMS",
    "DEFAULT_CLASSIFICATION_HEADER",
    "DEFAULT_LABELS_HEADER",
    "DEFAULT_METRIC_NAMESPACE",
    "DEFAULT_MISSION_METADATA_HEADER",
    "DEFAULT_PRIORITY_HEADER",
    "ENCRYPTED_PAYLOAD_KEY",
    "METRIC_SPECS",
    "MISSION_METADATA_PROFILE_VERSION",
    "NATS_RESERVED_HEADER_NAMES",
    "AckError",
    "ConfigurationError",
    "CustodyConfig",
    "DeadLetterError",
    "DestinationUnavailableError",
    "EncryptionConfig",
    "EncryptionRuleConfig",
    "FileSink",
    "FlushableSink",
    "HealthCheckableSink",
    "InMemoryMetrics",
    "JetStreamSinkRunner",
    "JsonFileMetrics",
    "MessageMetadataConfig",
    "MessageMetadataFieldConfig",
    "MessageMetadataLabelsConfig",
    "MessageMetadataRuleConfig",
    "MetricNames",
    "MissionMetadataConfig",
    "MissionMetadataRuleConfig",
    "NatsEnvelope",
    "NatsSinksError",
    "NoopMetrics",
    "NormalizedPayload",
    "PayloadEncryptor",
    "PayloadKeyRegistry",
    "PayloadOriginalFormat",
    "PayloadStorageMode",
    "PermanentSinkError",
    "PolicyEvaluation",
    "PolicyViolation",
    "PolicyViolationError",
    "PreSinkPolicyConfig",
    "PreSinkPolicyRuleConfig",
    "PriorityLaneConfig",
    "PriorityLanesConfig",
    "RetryExhaustedError",
    "SchemaAwareSink",
    "SerializationError",
    "Sink",
    "SinkError",
    "SubjectPayloadEncryptor",
    "TemporarySinkError",
    "ValidationError",
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
    "parse_mission_metadata_header",
    "qualified_metric_name",
    "write_metrics_snapshot",
]

__version__ = "0.4.0"
