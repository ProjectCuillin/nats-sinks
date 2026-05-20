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
    EncryptionConfig,
    EncryptionRuleConfig,
    MessageMetadataConfig,
    MessageMetadataFieldConfig,
    MessageMetadataLabelsConfig,
    MessageMetadataRuleConfig,
)
from nats_sinks.core.encryption import (
    ENCRYPTED_PAYLOAD_KEY,
    PayloadEncryptor,
    SubjectPayloadEncryptor,
    decrypt_payload,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    AckError,
    ConfigurationError,
    DeadLetterError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
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
from nats_sinks.core.payload import (
    NormalizedPayload,
    PayloadOriginalFormat,
    PayloadStorageMode,
    normalize_payload_for_json_storage,
)
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.file import FileSink
from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink

__all__ = [
    "DEFAULT_CLASSIFICATION_HEADER",
    "DEFAULT_LABELS_HEADER",
    "DEFAULT_PRIORITY_HEADER",
    "ENCRYPTED_PAYLOAD_KEY",
    "NATS_RESERVED_HEADER_NAMES",
    "AckError",
    "ConfigurationError",
    "DeadLetterError",
    "DestinationUnavailableError",
    "EncryptionConfig",
    "EncryptionRuleConfig",
    "FileSink",
    "FlushableSink",
    "HealthCheckableSink",
    "JetStreamSinkRunner",
    "MessageMetadataConfig",
    "MessageMetadataFieldConfig",
    "MessageMetadataLabelsConfig",
    "MessageMetadataRuleConfig",
    "NatsEnvelope",
    "NatsSinksError",
    "NormalizedPayload",
    "PayloadEncryptor",
    "PayloadOriginalFormat",
    "PayloadStorageMode",
    "PermanentSinkError",
    "RetryExhaustedError",
    "SchemaAwareSink",
    "SerializationError",
    "Sink",
    "SinkError",
    "SubjectPayloadEncryptor",
    "TemporarySinkError",
    "ValidationError",
    "build_nats_metadata_snapshot",
    "datetime_to_epoch_ns",
    "decrypt_payload",
    "normalize_payload_for_json_storage",
]

__version__ = "0.2.1"
