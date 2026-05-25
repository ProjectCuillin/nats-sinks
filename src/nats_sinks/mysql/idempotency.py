# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle MySQL idempotency helpers.

The helper functions mirror the Oracle Database sink behavior while keeping
the Oracle MySQL module self-contained.  They validate that the selected
idempotency strategy has the metadata it needs before rows reach the database
driver, so malformed messages fail through framework errors rather than as
ambiguous SQL failures.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ValidationError
from nats_sinks.mysql.config import MySqlIdempotencyConfig


def extract_payload_field(payload: Any, path: str) -> str:
    """Extract a scalar dotted JSON path from a normalized payload."""

    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValidationError(f"idempotency payload field {path!r} is missing")
        current = current[part]
    if current is None:
        raise ValidationError(f"idempotency payload field {path!r} is null")
    if isinstance(current, Mapping) or (
        isinstance(current, Sequence) and not isinstance(current, str | bytes | bytearray)
    ):
        raise ValidationError(f"idempotency payload field {path!r} must resolve to a scalar")
    if isinstance(current, float) and not math.isfinite(current):
        raise ValidationError(f"idempotency payload field {path!r} must be finite")
    try:
        rendered = str(current).strip()
    except Exception as exc:
        raise ValidationError(f"idempotency payload field {path!r} cannot be rendered") from exc
    if not rendered:
        raise ValidationError(f"idempotency payload field {path!r} is empty")
    return rendered


def validate_envelope_idempotency(
    envelope: NatsEnvelope,
    config: MySqlIdempotencyConfig,
    payload: Any,
) -> str | None:
    """Validate one envelope and return a derived message ID when configured."""

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
