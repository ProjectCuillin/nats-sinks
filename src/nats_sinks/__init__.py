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

from nats_sinks.core.advisory import (
    DEFAULT_ADVISORY_SUBJECTS,
    JetStreamAdvisory,
    JetStreamAdvisoryMonitor,
    advisory_kind_from_subject,
    observe_jetstream_advisory_message,
    parse_jetstream_advisory,
    validate_advisory_subject,
)
from nats_sinks.core.authenticity import (
    MESSAGE_AUTHENTICITY_SCHEMA,
    SUPPORTED_MESSAGE_AUTHENTICITY_ALGORITHMS,
    MessageAuthenticityEvaluation,
    MessageAuthenticityViolation,
    canonical_message_authenticity_bytes,
    canonical_message_authenticity_document,
    evaluate_message_authenticity,
    hmac_sha256_signature_b64,
)
from nats_sinks.core.config import (
    ConsumerManagementConfig,
    CustodyConfig,
    EncryptionConfig,
    EncryptionRuleConfig,
    JetStreamAdvisoryConfig,
    MessageAuthenticityConfig,
    MessageAuthenticityRuleConfig,
    MessageMetadataConfig,
    MessageMetadataFieldConfig,
    MessageMetadataLabelsConfig,
    MessageMetadataRuleConfig,
    MetricsConfig,
    MissionMetadataConfig,
    MissionMetadataRuleConfig,
    PreSinkPolicyConfig,
    PreSinkPolicyRuleConfig,
    PriorityLaneConfig,
    PriorityLanesConfig,
    SecurityLabelProfileConfig,
    SecurityLabelRuleConfig,
    SinkPluginConfig,
    SizePolicyConfig,
)
from nats_sinks.core.consumer_management import (
    ConsumerDrift,
    ConsumerManagementResult,
    build_consumer_config,
    detect_consumer_drift,
    ensure_jetstream_consumer,
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
    SizePolicyViolationError,
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
from nats_sinks.core.security_labels import (
    DEFAULT_SECURITY_LABELS_HEADER,
    SECURITY_LABEL_PROFILE_NAME,
    parse_security_label_header,
)
from nats_sinks.core.size_policy import (
    SizePolicyEvaluation,
    SizePolicyViolation,
    evaluate_size_policy,
)
from nats_sinks.file import FileSink
from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink
from nats_sinks.sinks.connectors import (
    SINK_CONNECTOR_API_VERSION,
    SINK_CONNECTOR_ENTRY_POINT_GROUP,
    SinkConnector,
    SinkConnectorStatus,
    load_entry_point_connectors,
    normalize_connector_name,
)
from nats_sinks.spool import SpoolReplayResult, SpoolSink, SpoolSinkConfig, replay_spool_to_sink

__all__ = [
    "CUSTODY_SCHEMA",
    "CUSTODY_SUPPORTED_ALGORITHMS",
    "DEFAULT_ADVISORY_SUBJECTS",
    "DEFAULT_CLASSIFICATION_HEADER",
    "DEFAULT_LABELS_HEADER",
    "DEFAULT_METRIC_NAMESPACE",
    "DEFAULT_MISSION_METADATA_HEADER",
    "DEFAULT_PRIORITY_HEADER",
    "DEFAULT_SECURITY_LABELS_HEADER",
    "ENCRYPTED_PAYLOAD_KEY",
    "MESSAGE_AUTHENTICITY_SCHEMA",
    "METRIC_SPECS",
    "MISSION_METADATA_PROFILE_VERSION",
    "NATS_RESERVED_HEADER_NAMES",
    "SECURITY_LABEL_PROFILE_NAME",
    "SINK_CONNECTOR_API_VERSION",
    "SINK_CONNECTOR_ENTRY_POINT_GROUP",
    "SUPPORTED_MESSAGE_AUTHENTICITY_ALGORITHMS",
    "AckError",
    "ConfigurationError",
    "ConsumerDrift",
    "ConsumerManagementConfig",
    "ConsumerManagementResult",
    "CustodyConfig",
    "DeadLetterError",
    "DestinationUnavailableError",
    "EncryptionConfig",
    "EncryptionRuleConfig",
    "FileSink",
    "FlushableSink",
    "HealthCheckableSink",
    "InMemoryMetrics",
    "JetStreamAdvisory",
    "JetStreamAdvisoryConfig",
    "JetStreamAdvisoryMonitor",
    "JetStreamSinkRunner",
    "JsonFileMetrics",
    "MessageAuthenticityConfig",
    "MessageAuthenticityEvaluation",
    "MessageAuthenticityRuleConfig",
    "MessageAuthenticityViolation",
    "MessageMetadataConfig",
    "MessageMetadataFieldConfig",
    "MessageMetadataLabelsConfig",
    "MessageMetadataRuleConfig",
    "MetricNames",
    "MetricsConfig",
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
    "SecurityLabelProfileConfig",
    "SecurityLabelRuleConfig",
    "SerializationError",
    "Sink",
    "SinkConnector",
    "SinkConnectorStatus",
    "SinkError",
    "SinkPluginConfig",
    "SizePolicyConfig",
    "SizePolicyEvaluation",
    "SizePolicyViolation",
    "SizePolicyViolationError",
    "SpoolReplayResult",
    "SpoolSink",
    "SpoolSinkConfig",
    "SubjectPayloadEncryptor",
    "TemporarySinkError",
    "ValidationError",
    "advisory_kind_from_subject",
    "attach_custody_metadata",
    "build_consumer_config",
    "build_nats_metadata_snapshot",
    "canonical_json_bytes",
    "canonical_message_authenticity_bytes",
    "canonical_message_authenticity_document",
    "compute_custody_metadata",
    "datetime_to_epoch_ns",
    "decrypt_payload",
    "detect_consumer_drift",
    "ensure_jetstream_consumer",
    "evaluate_message_authenticity",
    "evaluate_pre_sink_policy",
    "evaluate_size_policy",
    "hmac_sha256_signature_b64",
    "is_encrypted_payload_envelope",
    "load_entry_point_connectors",
    "load_metrics_snapshot",
    "metric_rows_from_snapshot",
    "normalize_connector_name",
    "normalize_payload_for_json_storage",
    "observe_jetstream_advisory_message",
    "parse_jetstream_advisory",
    "parse_mission_metadata_header",
    "parse_security_label_header",
    "qualified_metric_name",
    "replay_spool_to_sink",
    "validate_advisory_subject",
    "write_metrics_snapshot",
]

__version__ = "0.4.1"
