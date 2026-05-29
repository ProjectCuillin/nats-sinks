# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized NATS envelopes to HTTP request bodies.

The HTTP sink sends JSON bodies only.  This module performs no network I/O; it
builds deterministic, size-checked request documents so unit tests can exercise
payload normalization, metadata capture, and idempotency-key decisions without
opening sockets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PermanentSinkError, SerializationError
from nats_sinks.http.config import HttpSinkConfig


@dataclass(frozen=True, slots=True)
class HttpPreparedRequestBody:
    """Prepared JSON body for one HTTP request."""

    value: Any
    body: bytes
    idempotency_key: str | None


def http_idempotency_key(envelope: NatsEnvelope, *, config: HttpSinkConfig) -> str | None:
    """Return the idempotency key configured for HTTP writes."""

    if not config.idempotency.enabled:
        return None
    if config.idempotency.strategy == "idempotency_key":
        return envelope.idempotency_key()
    if config.idempotency.strategy == "stream_sequence":
        return _stream_sequence_key(envelope, config=config)
    if config.idempotency.strategy == "message_id":
        return _message_id_key(envelope, config=config)
    return _payload_sha256_key(envelope)


def _stream_sequence_key(envelope: NatsEnvelope, *, config: HttpSinkConfig) -> str | None:
    if not envelope.stream or envelope.stream_sequence is None:
        if config.idempotency.required:
            raise PermanentSinkError(
                "HTTP idempotency strategy 'stream_sequence' requires stream metadata"
            )
        return None
    return f"stream-sequence:{envelope.stream}:{envelope.stream_sequence}"


def _message_id_key(envelope: NatsEnvelope, *, config: HttpSinkConfig) -> str | None:
    if not envelope.message_id:
        if config.idempotency.required:
            raise PermanentSinkError("HTTP idempotency strategy 'message_id' requires a message ID")
        return None
    return f"message-id:{envelope.message_id}"


def _payload_sha256_key(envelope: NatsEnvelope) -> str:
    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256:{envelope.subject}:{digest}"


def http_envelope_value(
    envelope: NatsEnvelope,
    *,
    config: HttpSinkConfig,
) -> dict[str, Any]:
    """Build the standard HTTP envelope request body value."""

    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    value: dict[str, Any] = {
        "schema": "nats_sinks.http.message.v1",
        "schema_version": 1,
        "idempotency_key": http_idempotency_key(envelope, config=config),
        "subject": envelope.subject,
        "stream": envelope.stream,
        "stream_sequence": envelope.stream_sequence,
        "consumer": envelope.consumer,
        "consumer_sequence": envelope.consumer_sequence,
        "message_id": envelope.message_id,
        "priority": envelope.priority,
        "classification": envelope.classification,
        "labels": ",".join(envelope.labels) if envelope.labels else None,
        "labels_list": list(envelope.labels),
        "payload": normalized_payload.value,
        "payload_info": {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
    }
    if config.include_metadata:
        value["metadata"] = envelope.metadata_for_json_storage()
    if config.include_mission_metadata:
        value["mission_metadata"] = envelope.mission_metadata_for_json_storage()
    if config.include_security_labels:
        value["security_labels"] = envelope.security_labels_for_json_storage()
    if config.include_custody:
        value["custody"] = envelope.custody_for_json_storage()
    return value


def http_body_value(
    envelope: NatsEnvelope,
    *,
    config: HttpSinkConfig,
) -> Any:
    """Return the configured JSON request body value for one envelope."""

    if config.body_format == "payload":
        return envelope.payload_for_json_storage(mode=config.payload_mode).value
    if config.body_format == "envelope":
        return http_envelope_value(envelope, config=config)
    raise PermanentSinkError("unsupported HTTP body format")


def prepare_http_body(
    envelope: NatsEnvelope,
    *,
    config: HttpSinkConfig,
) -> HttpPreparedRequestBody:
    """Serialize and size-check one HTTP request body."""

    value = http_body_value(envelope, config=config)
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("HTTP request body is not JSON serializable") from exc
    body = rendered.encode("utf-8")
    if len(body) > config.max_request_bytes:
        raise PermanentSinkError("HTTP request body exceeds max_request_bytes")
    return HttpPreparedRequestBody(
        value=value,
        body=body,
        idempotency_key=http_idempotency_key(envelope, config=config),
    )
