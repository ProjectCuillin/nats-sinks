# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Spool record serialization, encryption, and replay reconstruction.

Spool files are destination records, not raw NATS messages.  Each file contains
one normalized `NatsEnvelope` plus enough metadata to replay the event into a
future sink without asking JetStream for the original message again.  The
record format is versioned so future releases can add fields without silently
changing replay semantics.

By default the whole replay record is encrypted with the same AEAD primitives
used by framework payload encryption.  A tiny plaintext wrapper remains on
disk so operators can sort by priority and identify records without exposing
payloads, headers, mission metadata, labels, or credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from nats_sinks.core.config import EncryptionConfig
from nats_sinks.core.encryption import PayloadEncryptor
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import SerializationError
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.spool.config import SpoolSinkConfig

SPOOL_RECORD_SCHEMA = "nats_sinks.spool.record.v1"
SPOOL_WRAPPER_SCHEMA = "nats_sinks.spool.wrapper.v1"
SPOOL_RECORD_VERSION = 1
SPOOL_WRAPPER_VERSION = 1

_PRIORITY_RANKS = {
    "flash": 0,
    "immediate": 1,
    "urgent": 2,
    "high": 3,
    "routine": 5,
    "normal": 5,
    "medium": 5,
    "low": 7,
    "deferred": 9,
}
_DEFAULT_PRIORITY_RANK = 6


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool = False) -> bytes:
    """Serialize a spool JSON object in a deterministic UTF-8 form."""

    if pretty:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return f"{rendered}\n".encode()


def _b64_encode(value: bytes) -> str:
    """Encode binary envelope data for JSON storage."""

    return base64.b64encode(value).decode("ascii")


def _b64_decode(value: object, *, field: str) -> bytes:
    """Decode a JSON base64 field with a safe framework error."""

    if not isinstance(value, str):
        raise SerializationError(f"spool record field {field} must be a base64 string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise SerializationError(f"spool record field {field} is not valid base64") from exc


def _datetime_to_epoch_ns(value: datetime | None) -> int | None:
    """Convert optional aware timestamps to integer epoch nanoseconds."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1_000_000_000)


def _epoch_ns_to_datetime(value: object, *, field: str) -> datetime | None:
    """Convert optional integer epoch nanoseconds back into aware UTC datetimes."""

    if value is None:
        return None
    if not isinstance(value, int):
        raise SerializationError(f"spool record field {field} must be an integer or null")
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)


def priority_rank(priority: str | None) -> int:
    """Return a bounded numeric priority rank for replay ordering.

    The rank is intentionally coarse and non-secret.  The full priority string
    is stored inside the encrypted record; the wrapper exposes only this number
    so disconnected nodes can drain high-priority records first without reading
    every ciphertext.
    """

    if priority is None:
        return _DEFAULT_PRIORITY_RANK
    return _PRIORITY_RANKS.get(priority.strip().casefold(), _DEFAULT_PRIORITY_RANK)


def spool_filename_for_envelope(envelope: NatsEnvelope) -> str:
    """Return the deterministic filename for one envelope.

    The filename is based on the framework idempotency key rather than subject,
    message ID, or payload text directly.  This keeps filenames bounded and
    avoids leaking operational message details into local directory listings.
    """

    digest = hashlib.sha256(envelope.idempotency_key().encode("utf-8")).hexdigest()
    return f"{digest}.spool.json"


def build_plain_record(
    envelope: NatsEnvelope,
    *,
    config: SpoolSinkConfig,
    spooled_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the plaintext replay record before optional encryption."""

    spooled_at = spooled_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    return {
        "schema": SPOOL_RECORD_SCHEMA,
        "schema_version": SPOOL_RECORD_VERSION,
        "spooled_at_epoch_ns": _datetime_to_epoch_ns(spooled_at),
        "idempotency_key": envelope.idempotency_key(),
        "envelope": {
            "subject": envelope.subject,
            "data_b64": _b64_encode(envelope.data),
            "headers": dict(envelope.headers),
            "stream": envelope.stream,
            "consumer": envelope.consumer,
            "stream_sequence": envelope.stream_sequence,
            "consumer_sequence": envelope.consumer_sequence,
            "timestamp_epoch_ns": _datetime_to_epoch_ns(envelope.timestamp),
            "message_id": envelope.message_id,
            "redelivered": envelope.redelivered,
            "pending": envelope.pending,
            "priority": envelope.priority,
            "classification": envelope.classification,
            "labels": list(envelope.labels),
            "labels_string": labels_to_storage_string(envelope.labels),
            "mission_metadata": envelope.mission_metadata_for_json_storage(),
            "custody": envelope.custody_for_json_storage(),
            "reply": envelope.reply,
            "domain": envelope.domain,
            "received_at_epoch_ns": _datetime_to_epoch_ns(envelope.received_at),
        },
        "payload_preview": {
            "value": normalized_payload.value,
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
        "metadata": (
            envelope.metadata_for_json_storage(stored_at=spooled_at)
            if config.include_metadata
            else None
        ),
    }


def wrap_record(
    plain_record: Mapping[str, Any],
    *,
    config: SpoolSinkConfig,
    encryptor: PayloadEncryptor | None = None,
) -> dict[str, Any]:
    """Return the JSON object written to disk for one spool record."""

    idempotency_key = plain_record.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise SerializationError("spool record idempotency key is missing")
    envelope = plain_record.get("envelope")
    priority = envelope.get("priority") if isinstance(envelope, Mapping) else None
    spooled_at = plain_record.get("spooled_at_epoch_ns")
    wrapper: dict[str, Any] = {
        "schema": SPOOL_WRAPPER_SCHEMA,
        "schema_version": SPOOL_WRAPPER_VERSION,
        "encrypted": config.encryption.enabled,
        "idempotency_key_sha256": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
        "priority_rank": priority_rank(priority if isinstance(priority, str) else None),
        "spooled_at_epoch_ns": spooled_at,
    }

    plain_bytes = canonical_json_bytes(plain_record, pretty=False)
    if config.encryption.enabled:
        active_encryptor = encryptor or PayloadEncryptor(config.encryption)
        encrypted_bytes = active_encryptor.encrypt_bytes(plain_bytes)
        encrypted = json.loads(encrypted_bytes.decode("utf-8"))
        wrapper["encrypted_record"] = encrypted
    else:
        wrapper["record"] = plain_record
    return wrapper


def unwrap_record(
    wrapper: Mapping[str, Any],
    *,
    encryption: EncryptionConfig,
) -> dict[str, Any]:
    """Decrypt and validate one on-disk spool wrapper."""

    if wrapper.get("schema") != SPOOL_WRAPPER_SCHEMA:
        raise SerializationError("spool wrapper schema is not supported")
    if wrapper.get("schema_version") != SPOOL_WRAPPER_VERSION:
        raise SerializationError("spool wrapper version is not supported")
    encrypted = wrapper.get("encrypted")
    loaded: Mapping[str, Any]
    if encrypted is True:
        encrypted_record = wrapper.get("encrypted_record")
        if not isinstance(encrypted_record, Mapping):
            raise SerializationError("encrypted spool wrapper is missing encrypted_record")
        plaintext = PayloadEncryptor(encryption).decrypt_payload(encrypted_record)
        loaded = _loads_record_json(plaintext)
    elif encrypted is False:
        raw_record = wrapper.get("record")
        if not isinstance(raw_record, Mapping):
            raise SerializationError("plaintext spool wrapper is missing record")
        loaded = raw_record
    else:
        raise SerializationError("spool wrapper encrypted flag must be true or false")

    record = cast("dict[str, Any]", dict(loaded))
    if record.get("schema") != SPOOL_RECORD_SCHEMA:
        raise SerializationError("spool record schema is not supported")
    if record.get("schema_version") != SPOOL_RECORD_VERSION:
        raise SerializationError("spool record version is not supported")
    return record


def envelope_from_plain_record(record: Mapping[str, Any]) -> NatsEnvelope:
    """Rebuild a `NatsEnvelope` for replay into a destination sink."""

    envelope = record.get("envelope")
    if not isinstance(envelope, Mapping):
        raise SerializationError("spool record envelope must be an object")
    subject = envelope.get("subject")
    if not isinstance(subject, str) or not subject:
        raise SerializationError("spool record envelope.subject must be a non-empty string")
    headers = envelope.get("headers")
    if not isinstance(headers, Mapping):
        raise SerializationError("spool record envelope.headers must be an object")
    labels = envelope.get("labels")
    if labels is None:
        labels = ()
    if not isinstance(labels, list | tuple):
        raise SerializationError("spool record envelope.labels must be a list")

    return NatsEnvelope(
        subject=subject,
        data=_b64_decode(envelope.get("data_b64"), field="envelope.data_b64"),
        headers={str(key): str(value) for key, value in headers.items()},
        stream=_optional_str(envelope.get("stream"), field="envelope.stream"),
        consumer=_optional_str(envelope.get("consumer"), field="envelope.consumer"),
        stream_sequence=_optional_int(
            envelope.get("stream_sequence"),
            field="envelope.stream_sequence",
        ),
        consumer_sequence=_optional_int(
            envelope.get("consumer_sequence"),
            field="envelope.consumer_sequence",
        ),
        timestamp=_epoch_ns_to_datetime(
            envelope.get("timestamp_epoch_ns"),
            field="envelope.timestamp_epoch_ns",
        ),
        message_id=_optional_str(envelope.get("message_id"), field="envelope.message_id"),
        redelivered=_optional_bool(envelope.get("redelivered"), field="envelope.redelivered"),
        pending=_optional_int(envelope.get("pending"), field="envelope.pending"),
        priority=_optional_str(envelope.get("priority"), field="envelope.priority"),
        classification=_optional_str(
            envelope.get("classification"),
            field="envelope.classification",
        ),
        labels=tuple(str(item) for item in labels),
        mission_metadata=_optional_mapping(
            envelope.get("mission_metadata"),
            field="envelope.mission_metadata",
        ),
        custody=_optional_mapping(envelope.get("custody"), field="envelope.custody"),
        reply=_optional_str(envelope.get("reply"), field="envelope.reply"),
        domain=_optional_str(envelope.get("domain"), field="envelope.domain"),
        received_at=_epoch_ns_to_datetime(
            envelope.get("received_at_epoch_ns"),
            field="envelope.received_at_epoch_ns",
        )
        or datetime.now(UTC),
    )


def _loads_record_json(value: bytes) -> dict[str, Any]:
    """Load a plaintext spool record while rejecting ambiguous JSON constants."""

    try:
        loaded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SerializationError("spool record is not valid JSON") from exc
    if not isinstance(loaded, dict):
        raise SerializationError("spool record root must be a JSON object")
    return loaded


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys so replay sees exactly one value for each field."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject NaN and Infinity in spool records."""

    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _optional_str(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SerializationError(f"spool record field {field} must be a string or null")
    return value


def _optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise SerializationError(f"spool record field {field} must be an integer or null")
    return value


def _optional_bool(value: object, *, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SerializationError(f"spool record field {field} must be a boolean or null")
    return value


def _optional_mapping(value: object, *, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SerializationError(f"spool record field {field} must be an object or null")
    return dict(value)
