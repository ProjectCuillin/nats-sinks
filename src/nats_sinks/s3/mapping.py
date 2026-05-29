# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized envelopes to S3-compatible object writes."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import SerializationError
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.core.metadata import datetime_to_epoch_ns
from nats_sinks.s3.config import S3KeyStrategy, S3SinkConfig

S3_OBJECT_SCHEMA = "nats_sinks.s3.object.v1"
S3_OBJECT_SCHEMA_VERSION = 1
S3_SIDECAR_SCHEMA = "nats_sinks.s3.sidecar.v1"
S3_SIDECAR_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class S3PreparedObject:
    """Prepared object request data safe to pass to the S3 client boundary."""

    key: str
    body: bytes
    content_type: str
    content_encoding: str | None
    metadata: dict[str, str]


def s3_key_for_envelope(envelope: NatsEnvelope, *, config: S3SinkConfig) -> str:
    """Return the deterministic object key for one envelope."""

    raw_key = _raw_key_for_envelope(envelope, strategy=config.key_strategy)
    if config.key_prefix:
        raw_key = f"{config.key_prefix}:{raw_key}"
    object_name = f"{raw_key}{config.object_suffix}"
    key = f"{config.prefix}/{object_name}" if config.prefix else object_name
    _validate_object_key(key, max_key_bytes=config.max_key_bytes)
    return key


def s3_sidecar_key_for_object(key: str, *, config: S3SinkConfig) -> str:
    """Return the deterministic metadata sidecar key for an object key."""

    if key.endswith(config.object_suffix):
        sidecar_key = f"{key[: -len(config.object_suffix)]}{config.sidecar_suffix}"
    else:
        sidecar_key = f"{key}{config.sidecar_suffix}"
    _validate_object_key(sidecar_key, max_key_bytes=config.max_key_bytes)
    return sidecar_key


def s3_object_value_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: S3SinkConfig,
    stored_at: datetime | None = None,
) -> Any:
    """Build the JSON-compatible object value for one envelope."""

    stored_at = stored_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    if config.object_format == "payload":
        return normalized_payload.value

    metadata = envelope.metadata_for_json_storage(stored_at=stored_at)
    metadata["custody"] = envelope.custody_for_json_storage()
    timestamps = metadata["timestamps"]
    return {
        "schema": S3_OBJECT_SCHEMA,
        "schema_version": S3_OBJECT_SCHEMA_VERSION,
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


def s3_sidecar_value_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: S3SinkConfig,
    object_key: str,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the optional JSON metadata sidecar object."""

    stored_at = stored_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    return {
        "schema": S3_SIDECAR_SCHEMA,
        "schema_version": S3_SIDECAR_SCHEMA_VERSION,
        "object_key": object_key,
        "key_strategy": config.key_strategy,
        "duplicate_policy": config.duplicate_policy,
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
        "stored_at_epoch_ns": datetime_to_epoch_ns(stored_at),
        "metadata": envelope.metadata_for_json_storage(stored_at=stored_at),
        "mission_metadata": envelope.mission_metadata_for_json_storage(),
        "security_labels": envelope.security_labels_for_json_storage(),
        "custody": envelope.custody_for_json_storage(),
        "payload_info": {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
    }


def prepare_s3_object(
    envelope: NatsEnvelope,
    *,
    config: S3SinkConfig,
    stored_at: datetime | None = None,
) -> S3PreparedObject:
    """Prepare the primary S3 object body and metadata."""

    key = s3_key_for_envelope(envelope, config=config)
    value = s3_object_value_for_envelope(envelope, config=config, stored_at=stored_at)
    body = _json_bytes(value, max_object_bytes=config.max_object_bytes, description="S3 object")
    body = _maybe_compress(body, config=config)
    _validate_body_size(body, max_object_bytes=config.max_object_bytes, description="S3 object")
    return S3PreparedObject(
        key=key,
        body=body,
        content_type=config.content_type,
        content_encoding="gzip" if config.compression == "gzip" else None,
        metadata=s3_object_metadata(envelope, config=config),
    )


def prepare_s3_sidecar_object(
    envelope: NatsEnvelope,
    *,
    config: S3SinkConfig,
    object_key: str,
    stored_at: datetime | None = None,
) -> S3PreparedObject:
    """Prepare the optional metadata sidecar object."""

    sidecar_key = s3_sidecar_key_for_object(object_key, config=config)
    value = s3_sidecar_value_for_envelope(
        envelope,
        config=config,
        object_key=object_key,
        stored_at=stored_at,
    )
    body = _json_bytes(
        value,
        max_object_bytes=config.max_object_bytes,
        description="S3 sidecar object",
    )
    return S3PreparedObject(
        key=sidecar_key,
        body=body,
        content_type="application/json",
        content_encoding=None,
        metadata=_base_object_metadata(config=config, schema=S3_SIDECAR_SCHEMA),
    )


def s3_object_metadata(envelope: NatsEnvelope, *, config: S3SinkConfig) -> dict[str, str]:
    """Return low-sensitivity object metadata for the primary object."""

    if config.metadata_mode == "none":
        return {}
    metadata = _base_object_metadata(config=config, schema=S3_OBJECT_SCHEMA)
    if envelope.message_id:
        metadata["nats-sinks-message-id-sha256"] = hashlib.sha256(
            envelope.message_id.encode("utf-8")
        ).hexdigest()
    _validate_metadata_size(metadata, max_metadata_bytes=config.max_metadata_bytes)
    return metadata


def _base_object_metadata(*, config: S3SinkConfig, schema: str) -> dict[str, str]:
    schema_version = (
        S3_SIDECAR_SCHEMA_VERSION if schema == S3_SIDECAR_SCHEMA else S3_OBJECT_SCHEMA_VERSION
    )
    metadata = {
        "nats-sinks-schema": schema,
        "nats-sinks-schema-version": str(schema_version),
        "nats-sinks-key-strategy": config.key_strategy,
    }
    metadata.update(config.object_metadata)
    _validate_metadata_size(metadata, max_metadata_bytes=config.max_metadata_bytes)
    return metadata


def _raw_key_for_envelope(envelope: NatsEnvelope, *, strategy: S3KeyStrategy) -> str:
    if strategy == "idempotency_key":
        return f"idempotency-key/{_safe_key_part(envelope.idempotency_key())}"
    if strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise SerializationError("S3 key_strategy='stream_sequence' requires stream metadata")
        return f"stream-sequence/{_safe_key_part(envelope.stream)}/{envelope.stream_sequence}"
    if strategy == "message_id":
        if not envelope.message_id:
            raise SerializationError("S3 key_strategy='message_id' requires a message ID")
        return f"message-id/{_safe_key_part(envelope.message_id)}"
    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256/{_safe_key_part(envelope.subject)}/{digest}"


def _safe_key_part(value: str) -> str:
    """Return a deterministic path-safe object-key segment."""

    result = []
    for character in value:
        if character.isalnum() or character in {"-", "_", "."}:
            result.append(character)
        else:
            result.append("_")
    rendered = "".join(result).strip("._-")
    return rendered or "value"


def _json_bytes(value: Any, *, max_object_bytes: int, description: str) -> bytes:
    try:
        body = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"{description} is not JSON serializable") from exc
    _validate_body_size(body, max_object_bytes=max_object_bytes, description=description)
    return body


def _maybe_compress(body: bytes, *, config: S3SinkConfig) -> bytes:
    if config.compression == "none":
        return body
    return gzip.compress(body, mtime=0)


def _validate_body_size(body: bytes, *, max_object_bytes: int, description: str) -> None:
    if len(body) > max_object_bytes:
        raise SerializationError(f"{description} exceeds configured max_object_bytes")


def _validate_object_key(key: str, *, max_key_bytes: int) -> None:
    if key.startswith("/") or "//" in key:
        raise SerializationError("S3 object key contains unsafe path separators")
    if any(segment in {"", ".", ".."} for segment in key.split("/")):
        raise SerializationError("S3 object key contains unsafe path segments")
    try:
        key_size = len(key.encode("utf-8"))
    except UnicodeEncodeError as exc:  # pragma: no cover - Python str is valid Unicode.
        raise SerializationError("S3 object key is not valid UTF-8") from exc
    if key_size > max_key_bytes:
        raise SerializationError(
            "S3 object key exceeds configured max_key_bytes; choose a shorter prefix or strategy"
        )


def _validate_metadata_size(metadata: dict[str, str], *, max_metadata_bytes: int) -> None:
    size = sum(
        len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in metadata.items()
    )
    if size > max_metadata_bytes:
        raise SerializationError("S3 object metadata exceeds configured max_metadata_bytes")
