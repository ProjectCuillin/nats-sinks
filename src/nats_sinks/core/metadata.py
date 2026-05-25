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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

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
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

MessageCreatedTimestampSource = Literal["nats_time_stamp", "jetstream_timestamp", "missing"]


@dataclass(frozen=True, slots=True)
class MessageCreatedTimestamp:
    """Resolved message creation timestamp and parsing evidence.

    Publishers may provide `Nats-Time-Stamp`, while JetStream metadata can also
    expose a server-side timestamp.  Operators need to distinguish a clean
    publisher timestamp from a fallback or a missing value, especially when
    freshness and stale-event metrics are used during replay or delayed
    network conditions.
    """

    epoch_ns: int | None
    source: MessageCreatedTimestampSource
    raw_header: str | None = None
    malformed_header: bool = False


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
    delta = value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


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


def resolve_message_created_timestamp(envelope: NatsEnvelope) -> MessageCreatedTimestamp:
    """Resolve message creation time from trusted parser output.

    The function prefers a syntactically valid `Nats-Time-Stamp` header because
    that is the publisher-visible event creation hint.  If the header is
    missing or malformed, it falls back to the JetStream metadata timestamp
    when available.  A malformed header is still reported so metrics can count
    it without rejecting or repairing the message.
    """

    raw_header = _case_insensitive_lookup(envelope.headers, "Nats-Time-Stamp")
    if raw_header is not None:
        header_epoch_ns = parse_rfc3339_to_epoch_ns(raw_header)
        if header_epoch_ns is not None:
            return MessageCreatedTimestamp(
                epoch_ns=header_epoch_ns,
                source="nats_time_stamp",
                raw_header=raw_header,
            )
        fallback_epoch_ns = datetime_to_epoch_ns(envelope.timestamp)
        return MessageCreatedTimestamp(
            epoch_ns=fallback_epoch_ns,
            source="jetstream_timestamp" if fallback_epoch_ns is not None else "missing",
            raw_header=raw_header,
            malformed_header=True,
        )

    fallback_epoch_ns = datetime_to_epoch_ns(envelope.timestamp)
    return MessageCreatedTimestamp(
        epoch_ns=fallback_epoch_ns,
        source="jetstream_timestamp" if fallback_epoch_ns is not None else "missing",
    )


def _age_seconds(later_epoch_ns: int | None, earlier_epoch_ns: int | None) -> float | None:
    """Return a bounded age in seconds for metadata storage."""

    if later_epoch_ns is None or earlier_epoch_ns is None:
        return None
    return max(0.0, (later_epoch_ns - earlier_epoch_ns) / 1_000_000_000)


def _future_skew_seconds(
    created_epoch_ns: int | None, observed_epoch_ns: int | None
) -> float | None:
    """Return positive source clock skew for future-dated message timestamps."""

    if created_epoch_ns is None or observed_epoch_ns is None:
        return None
    return max(0.0, (created_epoch_ns - observed_epoch_ns) / 1_000_000_000)


def build_nats_metadata_snapshot(
    envelope: NatsEnvelope,
    *,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the standard JSON metadata document for destination storage."""

    stored_at = stored_at or datetime.now(UTC)
    headers = dict(envelope.headers)
    reserved_headers = _standard_headers(headers)
    created_timestamp = resolve_message_created_timestamp(envelope)
    received_at_epoch_ns = datetime_to_epoch_ns(envelope.received_at)
    stored_at_epoch_ns = datetime_to_epoch_ns(stored_at)

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
            "message_created_at_epoch_ns": created_timestamp.epoch_ns,
            "message_created_at_source": created_timestamp.source,
            "message_created_at_header_malformed": created_timestamp.malformed_header,
            "nats_time_stamp": created_timestamp.raw_header,
            "jetstream_timestamp_epoch_ns": datetime_to_epoch_ns(envelope.timestamp),
            "received_at": envelope.received_at.isoformat(),
            "received_at_epoch_ns": received_at_epoch_ns,
            "stored_at": stored_at.isoformat(),
            "stored_at_epoch_ns": stored_at_epoch_ns,
        },
        "freshness": {
            "event_age_at_receive_seconds": _age_seconds(
                received_at_epoch_ns,
                created_timestamp.epoch_ns,
            ),
            "event_age_at_store_seconds": _age_seconds(
                stored_at_epoch_ns,
                created_timestamp.epoch_ns,
            ),
            "source_clock_skew_seconds": _future_skew_seconds(
                created_timestamp.epoch_ns,
                received_at_epoch_ns,
            ),
            "message_created_at_source": created_timestamp.source,
            "message_created_at_missing": created_timestamp.epoch_ns is None,
            "message_created_at_header_malformed": created_timestamp.malformed_header,
        },
    }
