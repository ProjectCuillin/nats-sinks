# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Dead-letter message construction.

Dead-letter publication is part of delivery semantics, so DLQ payload building
lives in core rather than in a destination sink.  Permanent failures may be
published to a configured subject, and the original JetStream message is ACKed
only after that publication succeeds.

DLQ payloads are JSON documents.  Source payloads are encoded as base64 when
included so invalid text or invalid JSON from the original message can still be
carried safely.  Operators may disable payload, header, or error inclusion when
privacy requirements demand less diagnostic detail.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ValidationError


def _safe_idempotency_key(envelope: NatsEnvelope) -> tuple[str | None, str | None]:
    """Return the best idempotency key without failing DLQ construction."""

    try:
        return envelope.idempotency_key(), None
    except ValidationError:
        return None, "payload_omitted"


def build_dead_letter_payload(
    envelope: NatsEnvelope,
    error: BaseException,
    *,
    include_payload: bool,
    include_headers: bool,
    include_error: bool,
) -> bytes:
    """Build a JSON DLQ payload without assuming the source payload is text."""

    idempotency_key, idempotency_key_unavailable_reason = _safe_idempotency_key(envelope)
    body: dict[str, Any] = {
        "subject": envelope.subject,
        "stream": envelope.stream,
        "consumer": envelope.consumer,
        "stream_sequence": envelope.stream_sequence,
        "consumer_sequence": envelope.consumer_sequence,
        "message_id": envelope.message_id,
        "priority": envelope.priority,
        "classification": envelope.classification,
        "labels": list(envelope.labels),
        "redelivered": envelope.redelivered,
        "pending": envelope.pending,
        "idempotency_key": idempotency_key,
        "idempotency_key_unavailable_reason": idempotency_key_unavailable_reason,
        "payload": {
            "present": envelope.payload_present,
            "omitted": envelope.payload_omitted,
            "omitted_reason": envelope.payload_omitted_reason,
            "original_size_bytes": envelope.original_payload_size_bytes,
            "delivered_size_bytes": len(envelope.data),
            "nats_msg_size_header": next(
                (
                    value
                    for key, value in envelope.headers.items()
                    if key.casefold() == "nats-msg-size"
                ),
                None,
            ),
            "nats_msg_size_header_malformed": envelope.payload_size_header_malformed,
        },
    }
    if include_error:
        body["error_type"] = type(error).__name__
        body["error"] = str(error)
    if envelope.timestamp is not None:
        body["timestamp"] = envelope.timestamp.astimezone(UTC).isoformat()
    if include_headers:
        body["headers"] = dict(envelope.headers)
    if include_payload and envelope.payload_present:
        body["payload_base64"] = base64.b64encode(envelope.data).decode("ascii")
    elif include_payload and envelope.payload_omitted:
        body["payload_unavailable_reason"] = envelope.payload_omitted_reason or "payload_omitted"
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
