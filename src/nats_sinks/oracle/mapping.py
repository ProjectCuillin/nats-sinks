# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map NATS envelopes to Oracle bind rows.

The mapping layer converts framework envelopes into dictionaries consumed by
`cursor.executemany`.  It is intentionally separate from SQL generation and
connection handling so tests can validate payload conversion, header handling,
and idempotency behavior without an Oracle service.

Payloads are decoded as JSON because the first Oracle table shape stores JSON
payload content.  Valid JSON is stored unchanged.  Non-JSON text or bytes can
be wrapped by the shared nats-sinks JSON payload envelope, allowing encrypted
or opaque message bodies to remain durable without weakening delivery safety.
Serialization errors are framework permanent failures and may be sent to DLQ by
the core runner according to configuration.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.core.metadata import datetime_to_epoch_ns
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.oracle.config import OracleIdempotencyConfig
from nats_sinks.oracle.idempotency import validate_envelope_idempotency


def envelope_to_row(
    envelope: NatsEnvelope,
    *,
    idempotency: OracleIdempotencyConfig,
    payload_mode: PayloadStorageMode = "json_or_envelope",
) -> dict[str, Any]:
    """Convert one envelope into bind variables for Oracle SQL."""

    stored_at = datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=payload_mode)
    metadata = envelope.metadata_for_json_storage(stored_at=stored_at)
    timestamps = metadata["timestamps"]
    derived_message_id = validate_envelope_idempotency(
        envelope,
        idempotency,
        normalized_payload.value,
    )
    message_id = derived_message_id or envelope.message_id

    return {
        "stream_name": envelope.stream,
        "stream_sequence": envelope.stream_sequence,
        "subject": envelope.subject,
        "message_id": message_id,
        "priority": envelope.priority,
        "classification": envelope.classification,
        "labels": labels_to_storage_string(envelope.labels),
        "message_created_at_epoch_ns": timestamps["message_created_at_epoch_ns"],
        "jetstream_timestamp_epoch_ns": timestamps["jetstream_timestamp_epoch_ns"],
        "received_at_epoch_ns": datetime_to_epoch_ns(envelope.received_at),
        "stored_at_epoch_ns": datetime_to_epoch_ns(stored_at),
        "payload_json": json.dumps(
            normalized_payload.value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        "headers_json": json.dumps(
            dict(envelope.headers),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        "metadata_json": json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        "mission_metadata_json": json.dumps(
            envelope.mission_metadata_for_json_storage(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    }


def envelopes_to_rows(
    envelopes: Sequence[NatsEnvelope],
    *,
    idempotency: OracleIdempotencyConfig,
    payload_mode: PayloadStorageMode = "json_or_envelope",
) -> list[dict[str, Any]]:
    """Convert a batch of envelopes into Oracle bind rows."""

    return [
        envelope_to_row(envelope, idempotency=idempotency, payload_mode=payload_mode)
        for envelope in envelopes
    ]
