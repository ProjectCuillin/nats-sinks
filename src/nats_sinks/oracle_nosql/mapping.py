# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized envelopes to Oracle NoSQL Database records."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import SerializationError
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.core.metadata import datetime_to_epoch_ns
from nats_sinks.oracle_nosql.config import OracleNoSqlKeyStrategy, OracleNoSqlSinkConfig

ORACLE_NOSQL_EVENT_SCHEMA = "nats_sinks.oracle_nosql.event.v1"
ORACLE_NOSQL_EVENT_SCHEMA_VERSION = 1


def oracle_nosql_key_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: OracleNoSqlSinkConfig,
) -> str:
    """Return the deterministic Oracle NoSQL Database primary key."""

    raw_key = _raw_key_for_envelope(envelope, strategy=config.key_strategy)
    key = f"{config.key_prefix}:{raw_key}" if config.key_prefix else raw_key
    try:
        key_size = len(key.encode("utf-8"))
    except UnicodeEncodeError as exc:  # pragma: no cover - Python str is valid Unicode.
        raise SerializationError("Oracle NoSQL Database key is not valid UTF-8") from exc
    if key_size > config.max_key_bytes:
        raise SerializationError(
            "Oracle NoSQL Database key exceeds configured max_key_bytes; choose a shorter "
            "key_prefix or strategy"
        )
    return key


def oracle_nosql_value_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: OracleNoSqlSinkConfig,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON-compatible value stored in the Oracle NoSQL row.

    The sink follows the same K/V style used by the Oracle Coherence Community
    Edition sink: the table primary key is stable, and the configured value
    field contains the complete normalized nats-sinks event object.
    """

    stored_at = stored_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    metadata = envelope.metadata_for_json_storage(stored_at=stored_at)
    metadata["custody"] = envelope.custody_for_json_storage()
    timestamps = metadata["timestamps"]
    value: dict[str, Any] = {
        "schema": ORACLE_NOSQL_EVENT_SCHEMA,
        "schema_version": ORACLE_NOSQL_EVENT_SCHEMA_VERSION,
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
    _validate_json_size(
        value,
        max_value_bytes=config.max_value_bytes,
        description="Oracle NoSQL Database value",
    )
    return value


def oracle_nosql_row_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: OracleNoSqlSinkConfig,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the full Oracle NoSQL Database row for one envelope."""

    stored_at = stored_at or datetime.now(UTC)
    row = {
        config.key_field: oracle_nosql_key_for_envelope(envelope, config=config),
        config.value_field: oracle_nosql_value_for_envelope(
            envelope,
            config=config,
            stored_at=stored_at,
        ),
        config.stored_at_field: datetime_to_epoch_ns(stored_at),
    }
    _validate_json_size(
        row,
        max_value_bytes=config.max_value_bytes,
        description="Oracle NoSQL Database row",
    )
    return row


def oracle_nosql_create_table_statement(*, config: OracleNoSqlSinkConfig) -> str:
    """Return safe generated DDL for the default Oracle NoSQL table model."""

    return (
        f"CREATE TABLE IF NOT EXISTS {config.table_name} "
        f"({config.key_field} STRING, "
        f"{config.value_field} JSON, "
        f"{config.stored_at_field} LONG, "
        f"PRIMARY KEY({config.key_field}))"
    )


def _raw_key_for_envelope(
    envelope: NatsEnvelope,
    *,
    strategy: OracleNoSqlKeyStrategy,
) -> str:
    if strategy == "idempotency_key":
        return envelope.idempotency_key()
    if strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise SerializationError(
                "Oracle NoSQL Database key_strategy='stream_sequence' requires stream metadata"
            )
        return f"stream-sequence:{envelope.stream}:{envelope.stream_sequence}"
    if strategy == "message_id":
        if not envelope.message_id:
            raise SerializationError(
                "Oracle NoSQL Database key_strategy='message_id' requires a message ID"
            )
        return f"message-id:{envelope.message_id}"
    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256:{envelope.subject}:{digest}"


def _validate_json_size(
    value: dict[str, Any],
    *,
    max_value_bytes: int,
    description: str,
) -> None:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"{description} is not JSON serializable") from exc
    if len(rendered.encode("utf-8")) > max_value_bytes:
        raise SerializationError(f"{description} exceeds configured max_value_bytes")
