# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized envelopes to Oracle Coherence JSON values."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from nats_sinks.coherence.config import CoherenceKeyStrategy, CoherenceSinkConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import SerializationError
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.core.metadata import datetime_to_epoch_ns

COHERENCE_EVENT_SCHEMA = "nats_sinks.coherence.event.v1"
COHERENCE_EVENT_SCHEMA_VERSION = 1


def coherence_key_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: CoherenceSinkConfig,
) -> str:
    """Return the deterministic Oracle Coherence key for one envelope."""

    raw_key = _raw_key_for_envelope(envelope, strategy=config.key_strategy)
    key = f"{config.key_prefix}:{raw_key}" if config.key_prefix else raw_key
    try:
        key_size = len(key.encode("utf-8"))
    except UnicodeEncodeError as exc:  # pragma: no cover - Python str is valid Unicode.
        raise SerializationError("Oracle Coherence key is not valid UTF-8") from exc
    if key_size > config.max_key_bytes:
        raise SerializationError(
            "Oracle Coherence key exceeds configured max_key_bytes; choose a shorter "
            "key_prefix or strategy"
        )
    return key


def coherence_value_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: CoherenceSinkConfig,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the complete JSON-compatible value written to Coherence.

    The value preserves the same core payload and metadata contract used by the
    relational and file sinks while remaining a single K/V value.  The value is
    serialized during mapping so size limits and JSON non-finites fail before a
    client write is attempted.
    """

    stored_at = stored_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    metadata = envelope.metadata_for_json_storage(stored_at=stored_at)
    metadata["custody"] = envelope.custody_for_json_storage()
    timestamps = metadata["timestamps"]
    value: dict[str, Any] = {
        "schema": COHERENCE_EVENT_SCHEMA,
        "schema_version": COHERENCE_EVENT_SCHEMA_VERSION,
        "subject": envelope.subject,
        "stream": envelope.stream,
        "stream_sequence": envelope.stream_sequence,
        "consumer": envelope.consumer,
        "consumer_sequence": envelope.consumer_sequence,
        "message_id": envelope.message_id,
        "priority": envelope.priority,
        "classification": envelope.classification,
        "labels": labels_to_storage_string(envelope.labels),
        "labels_list": list(envelope.labels),
        "message_created_at_epoch_ns": timestamps["message_created_at_epoch_ns"],
        "jetstream_timestamp_epoch_ns": timestamps["jetstream_timestamp_epoch_ns"],
        "received_at_epoch_ns": datetime_to_epoch_ns(envelope.received_at),
        "stored_at_epoch_ns": datetime_to_epoch_ns(stored_at),
        "headers": dict(envelope.headers),
        "metadata": metadata,
        "mission_metadata": envelope.mission_metadata_for_json_storage(),
        "security_labels": envelope.security_labels_for_json_storage(),
        "custody": envelope.custody_for_json_storage(),
        "payload": normalized_payload.value,
        "payload_info": {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
    }
    _validate_value_size(value, max_value_bytes=config.max_value_bytes)
    return value


def _raw_key_for_envelope(envelope: NatsEnvelope, *, strategy: CoherenceKeyStrategy) -> str:
    if strategy == "idempotency_key":
        return envelope.idempotency_key()
    if strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise SerializationError(
                "Oracle Coherence key_strategy='stream_sequence' requires stream metadata"
            )
        return f"stream-sequence:{envelope.stream}:{envelope.stream_sequence}"
    if strategy == "message_id":
        if not envelope.message_id:
            raise SerializationError(
                "Oracle Coherence key_strategy='message_id' requires a message ID"
            )
        return f"message-id:{envelope.message_id}"
    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256:{envelope.subject}:{digest}"


def _validate_value_size(value: dict[str, Any], *, max_value_bytes: int) -> None:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("Oracle Coherence value is not JSON serializable") from exc
    if len(rendered.encode("utf-8")) > max_value_bytes:
        raise SerializationError("Oracle Coherence value exceeds configured max_value_bytes")
