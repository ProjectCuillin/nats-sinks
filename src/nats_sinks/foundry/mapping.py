# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized NATS envelopes to Foundry stream records.

Foundry stream push examples use a list of records whose business value sits
under a ``value`` key.  This module builds that shape without performing HTTP
I/O, so tests can prove the mapping is bounded, deterministic, and free of NATS
acknowledgement primitives.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PermanentSinkError, SerializationError
from nats_sinks.foundry.config import FoundrySinkConfig


@dataclass(frozen=True, slots=True)
class FoundryPreparedBatch:
    """A bounded Foundry request body prepared for one sink write call."""

    records: tuple[dict[str, Any], ...]
    size_bytes: int


def foundry_record_key(envelope: NatsEnvelope, *, config: FoundrySinkConfig) -> str:
    """Return the deterministic record key configured for Foundry writes."""

    if config.record_key_strategy == "idempotency_key":
        return envelope.idempotency_key()
    if config.record_key_strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise PermanentSinkError(
                "Foundry record_key_strategy='stream_sequence' requires stream metadata"
            )
        return f"stream-sequence:{envelope.stream}:{envelope.stream_sequence}"
    if config.record_key_strategy == "message_id":
        if not envelope.message_id:
            raise PermanentSinkError(
                "Foundry record_key_strategy='message_id' requires a message ID"
            )
        return f"message-id:{envelope.message_id}"

    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256:{envelope.subject}:{digest}"


def foundry_value_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: FoundrySinkConfig,
) -> dict[str, Any]:
    """Build the JSON value written into a Foundry stream record."""

    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    value: dict[str, Any] = {
        config.record_key_field: foundry_record_key(envelope, config=config),
        config.subject_field: envelope.subject,
        config.payload_field: normalized_payload.value,
        config.payload_info_field: {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
        config.priority_field: envelope.priority,
        config.classification_field: envelope.classification,
        config.labels_field: ",".join(envelope.labels) if envelope.labels else None,
        config.labels_list_field: list(envelope.labels),
    }
    if config.include_metadata:
        value[config.metadata_field] = envelope.metadata_for_json_storage()
    if config.include_mission_metadata:
        value[config.mission_metadata_field] = envelope.mission_metadata_for_json_storage()
    if config.include_security_labels:
        value[config.security_labels_field] = envelope.security_labels_for_json_storage()
    if config.include_custody:
        value[config.custody_field] = envelope.custody_for_json_storage()
    return value


def foundry_record_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: FoundrySinkConfig,
) -> dict[str, Any]:
    """Build one Foundry stream push record."""

    value = foundry_value_for_envelope(envelope, config=config)
    if config.record_wrapper == "value":
        return {"value": value}
    raise PermanentSinkError("unsupported Foundry record wrapper")


def prepare_foundry_batch(
    messages: Sequence[NatsEnvelope],
    *,
    config: FoundrySinkConfig,
) -> FoundryPreparedBatch:
    """Build and size-check one Foundry batch."""

    records: list[dict[str, Any]] = []
    record_keys: set[str] = set()
    total_size = 0
    for message in messages:
        record = foundry_record_for_envelope(message, config=config)
        value = record.get("value")
        if not isinstance(value, dict):
            raise SerializationError("Foundry record value must be a JSON object")
        record_key = value[config.record_key_field]
        if not isinstance(record_key, str):
            raise SerializationError("Foundry record key must be a string")
        if record_key in record_keys:
            raise PermanentSinkError("Foundry batch contains duplicate record keys")
        record_keys.add(record_key)

        size_bytes = _json_size_bytes(record)
        if size_bytes > config.max_record_bytes:
            raise PermanentSinkError("Foundry record exceeds max_record_bytes")
        total_size += size_bytes
        if total_size > config.max_batch_bytes:
            raise PermanentSinkError("Foundry batch exceeds max_batch_bytes")
        records.append(record)
    return FoundryPreparedBatch(records=tuple(records), size_bytes=total_size)


def _json_size_bytes(value: Any) -> int:
    """Return the UTF-8 JSON size of a value using strict JSON semantics."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("Foundry record is not JSON serializable") from exc
    return len(rendered.encode("utf-8"))
