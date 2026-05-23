# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic NATS metadata capture for all destination sinks.

The core runtime normalizes NATS messages before any sink sees them.  This
module builds a JSON-compatible metadata snapshot from that normalized envelope
so every backend can persist the same operational context: all message headers,
known NATS-reserved headers when present, JetStream sequence data, and timing
points for message creation, receipt by nats-sinks, and destination storage.

NATS intentionally allows reserved `Nats-` headers to evolve over time.  The
known-header list documents the headers nats-sinks names explicitly today, but
the metadata snapshot also captures any header with a `Nats-` prefix so newer
server fields are preserved before the code knows their exact meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nats_sinks.core.envelope import NatsEnvelope

NATS_RESERVED_HEADER_NAMES: tuple[str, ...] = (
    "Nats-Msg-Id",
    "Nats-Expected-Stream",
    "Nats-Expected-Last-Msg-Id",
    "Nats-Expected-Last-Sequence",
    "Nats-Expected-Last-Subject-Sequence",
    "Nats-Expected-Last-Subject-Sequence-Subject",
    "Nats-Rollup",
    "Nats-TTL",
    "Nats-Stream",
    "Nats-Subject",
    "Nats-Sequence",
    "Nats-Last-Sequence",
    "Nats-Time-Stamp",
    "Nats-Num-Pending",
    "Nats-UpTo-Sequence",
    "Nats-Stream-Source",
    "Nats-Trace-Dest",
    "Nats-Trace-Only",
    "Nats-Trace-Hop",
    "Nats-Trace-Origin-Account",
    "Nats-Schedule",
    "Nats-Schedule-TTL",
    "Nats-Schedule-Target",
    "Nats-Schedule-Time-Zone",
)

NON_NATS_STANDARD_HEADER_NAMES: tuple[str, ...] = (
    "traceparent",
    "Accept-Encoding",
)


def datetime_to_epoch_ns(value: datetime | None) -> int | None:
    """Convert a datetime to Unix epoch nanoseconds.

    Naive datetimes are treated as UTC because nats-py may expose JetStream
    metadata timestamps without timezone information.  Returning `None` for
    missing timestamps keeps optional NATS metadata optional all the way to the
    destination backend.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return int(value.timestamp() * 1_000_000_000)


def parse_rfc3339_to_epoch_ns(value: str | None) -> int | None:
    """Parse NATS RFC3339-style timestamp headers to epoch nanoseconds."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return datetime_to_epoch_ns(parsed)


def _case_insensitive_lookup(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _standard_headers(headers: Mapping[str, str]) -> dict[str, str]:
    standard: dict[str, str] = {}
    for name in (*NATS_RESERVED_HEADER_NAMES, *NON_NATS_STANDARD_HEADER_NAMES):
        value = _case_insensitive_lookup(headers, name)
        if value is not None:
            standard[name] = value
    for key, value in headers.items():
        if key.lower().startswith("nats-") and key not in standard:
            standard[key] = value
    return standard


def build_nats_metadata_snapshot(
    envelope: NatsEnvelope,
    *,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the standard JSON metadata document for destination storage."""

    stored_at = stored_at or datetime.now(UTC)
    headers = dict(envelope.headers)
    reserved_headers = _standard_headers(headers)
    nats_time_stamp = _case_insensitive_lookup(headers, "Nats-Time-Stamp")
    message_created_at_epoch_ns = parse_rfc3339_to_epoch_ns(
        nats_time_stamp
    ) or datetime_to_epoch_ns(envelope.timestamp)

    return {
        "metadata_version": 1,
        "subject": envelope.subject,
        "reply": envelope.reply,
        "message_id": envelope.message_id,
        "message_metadata": {
            "priority": envelope.priority,
            "classification": envelope.classification,
            "labels": list(envelope.labels),
        },
        "mission_metadata": envelope.mission_metadata_for_json_storage(),
        "security_labels": envelope.security_labels_for_json_storage(),
        "headers": headers,
        "nats": {
            "reserved_headers": reserved_headers,
            "reserved_headers_present": sorted(reserved_headers),
        },
        "jetstream": {
            "stream": envelope.stream,
            "consumer": envelope.consumer,
            "domain": envelope.domain,
            "stream_sequence": envelope.stream_sequence,
            "consumer_sequence": envelope.consumer_sequence,
            "redelivered": envelope.redelivered,
            "pending": envelope.pending,
            "timestamp": envelope.timestamp.isoformat() if envelope.timestamp else None,
            "timestamp_epoch_ns": datetime_to_epoch_ns(envelope.timestamp),
        },
        "timestamps": {
            "message_created_at_epoch_ns": message_created_at_epoch_ns,
            "nats_time_stamp": nats_time_stamp,
            "jetstream_timestamp_epoch_ns": datetime_to_epoch_ns(envelope.timestamp),
            "received_at": envelope.received_at.isoformat(),
            "received_at_epoch_ns": datetime_to_epoch_ns(envelope.received_at),
            "stored_at": stored_at.isoformat(),
            "stored_at_epoch_ns": datetime_to_epoch_ns(stored_at),
        },
    }
