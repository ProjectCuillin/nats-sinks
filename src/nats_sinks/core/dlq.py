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


def build_dead_letter_payload(
    envelope: NatsEnvelope,
    error: BaseException,
    *,
    include_payload: bool,
    include_headers: bool,
    include_error: bool,
) -> bytes:
    """Build a JSON DLQ payload without assuming the source payload is text."""

    body: dict[str, Any] = {
        "subject": envelope.subject,
        "stream": envelope.stream,
        "consumer": envelope.consumer,
        "stream_sequence": envelope.stream_sequence,
        "consumer_sequence": envelope.consumer_sequence,
        "message_id": envelope.message_id,
        "redelivered": envelope.redelivered,
        "pending": envelope.pending,
        "idempotency_key": envelope.idempotency_key(),
    }
    if include_error:
        body["error_type"] = type(error).__name__
        body["error"] = str(error)
    if envelope.timestamp is not None:
        body["timestamp"] = envelope.timestamp.astimezone(UTC).isoformat()
    if include_headers:
        body["headers"] = dict(envelope.headers)
    if include_payload:
        body["payload_base64"] = base64.b64encode(envelope.data).decode("ascii")
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
