# SPDX-License-Identifier: Apache-2.0
"""Oracle idempotency helpers.

At-least-once delivery means Oracle writes must tolerate duplicate processing.
This module validates the configured idempotency strategy for each envelope and
extracts derived keys when the user chooses a JSON payload field.

The recommended production strategy is `stream_sequence`, using the JetStream
stream name and stream sequence as the durable key.  `message_id` and
`payload_field` are useful when producers already provide stable business keys,
but both require more discipline from upstream publishers.
"""

from __future__ import annotations

from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ValidationError
from nats_sinks.oracle.config import OracleIdempotencyConfig


def extract_payload_field(payload: Any, path: str) -> str:
    """Extract a dotted JSON field path for payload_field idempotency."""

    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValidationError(f"idempotency payload field {path!r} is missing")
        current = current[part]
    if current is None:
        raise ValidationError(f"idempotency payload field {path!r} is null")
    return str(current)


def validate_envelope_idempotency(
    envelope: NatsEnvelope,
    config: OracleIdempotencyConfig,
    payload: Any,
) -> str | None:
    """Validate and return an optional derived message_id value."""

    if config.strategy == "stream_sequence":
        if envelope.stream is None or envelope.stream_sequence is None:
            raise ValidationError("stream_sequence idempotency requires JetStream stream metadata")
        return None
    if config.strategy == "message_id":
        if not envelope.message_id:
            raise ValidationError("message_id idempotency requires a NATS message ID header")
        return None
    if not config.payload_field:
        raise ValidationError("payload_field idempotency requires a configured payload field")
    return extract_payload_field(payload, config.payload_field)
