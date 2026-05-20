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
    SubjectPayloadEncryptor,
    decrypt_payload,
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
from nats_sinks.core.payload import (
    NormalizedPayload,
    PayloadOriginalFormat,
    PayloadStorageMode,
    normalize_payload_for_json_storage,
)
from nats_sinks.core.runner import JetStreamSinkRunner

__all__ = [
    "DEFAULT_CLASSIFICATION_HEADER",
    "DEFAULT_LABELS_HEADER",
    "DEFAULT_PRIORITY_HEADER",
    "ENCRYPTED_PAYLOAD_KEY",
    "NATS_RESERVED_HEADER_NAMES",
    "JetStreamSinkRunner",
    "NatsEnvelope",
    "NormalizedPayload",
    "PayloadEncryptor",
    "PayloadOriginalFormat",
    "PayloadStorageMode",
    "SubjectPayloadEncryptor",
    "build_nats_metadata_snapshot",
    "datetime_to_epoch_ns",
    "decrypt_payload",
    "normalize_payload_for_json_storage",
]
