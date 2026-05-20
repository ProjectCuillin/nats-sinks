# SPDX-License-Identifier: Apache-2.0
"""NATS client message normalization.

This module is the adapter between `nats-py` message objects and the stable
`NatsEnvelope` object used by sinks.  Destination sinks must not receive raw
NATS messages because raw messages expose acknowledgement methods.  By
normalizing at the core boundary, the framework prevents sink implementations
from accidentally ACKing before durable destination success.

The adapter is intentionally tolerant of nats-py metadata differences across
versions and tests.  It extracts the fields needed for idempotency and
operations, converts unknown values conservatively, and leaves the raw metadata
attached for diagnostic use without making sinks depend on it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, SupportsBytes, cast

from nats_sinks.core.config import MessageMetadataConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.message_metadata import resolve_metadata_field, resolve_metadata_labels


def _get_nested(value: object, *names: str) -> object | None:
    current = value
    for name in names:
        try:
            current = getattr(current, name, None)
        except Exception:
            return None
        if current is None:
            return None
    return current


def _as_int(value: object | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, bytearray, int)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: object, *, default: str = "") -> str:
    """Render untrusted message metadata without letting `__str__` crash processing."""

    try:
        return str(value)
    except Exception:
        return default


def _safe_optional_text(value: object) -> str | None:
    """Render optional metadata, preserving missing values as `None`."""

    if value is None:
        return None
    rendered = _safe_text(value)
    return rendered or None


def _safe_getattr(value: object, name: str, default: object = None) -> object:
    """Read an attribute from an external client object without trusting it."""

    try:
        return getattr(value, name, default)
    except Exception:
        return default


def _safe_bytes(value: object) -> bytes:
    """Return payload bytes when possible, otherwise use an empty payload.

    Real `nats-py` messages expose `data` as bytes.  This fallback is mainly for
    tests, old client versions, or unexpected message-like objects.  Returning an
    empty byte string keeps the processing loop alive while still letting sinks
    apply their own validation and DLQ policy.
    """

    if value is None:
        return b""

    try:
        if isinstance(value, bytes):
            data = value
        elif isinstance(value, bytearray):
            data = bytes(value)
        elif isinstance(value, memoryview):
            data = value.tobytes()
        elif isinstance(value, str):
            data = value.encode()
        else:
            data = bytes(cast(SupportsBytes, value))
    except Exception:
        return b""
    return data


def _iter_header_items(headers: object) -> Iterable[tuple[object, object]]:
    if isinstance(headers, Mapping):
        return headers.items()
    try:
        mapping = dict(cast(Iterable[tuple[object, object]], headers))
    except Exception:
        return ()
    return tuple(mapping.items())


def _headers(raw_message: Any) -> Mapping[str, str]:
    headers = _safe_getattr(raw_message, "headers")
    if headers is None:
        return {}

    normalised: dict[str, str] = {}
    for key, value in _iter_header_items(headers):
        if value is None:
            continue
        rendered_key = _safe_text(key)
        if not rendered_key:
            continue
        normalised[rendered_key] = _safe_text(value)
    return normalised


def envelope_from_nats_message(
    raw_message: Any,
    *,
    message_metadata: MessageMetadataConfig | None = None,
) -> NatsEnvelope:
    """Convert a nats-py message-like object into a stable envelope."""

    metadata = None
    metadata = _safe_getattr(raw_message, "metadata")
    headers = _headers(raw_message)
    metadata_config = message_metadata or MessageMetadataConfig()

    stream_sequence = _as_int(
        _get_nested(metadata, "sequence", "stream") or _safe_getattr(metadata, "stream_sequence")
    )
    consumer_sequence = _as_int(
        _get_nested(metadata, "sequence", "consumer")
        or _safe_getattr(metadata, "consumer_sequence")
    )
    delivered = _as_int(_safe_getattr(metadata, "num_delivered"))
    timestamp = _safe_getattr(metadata, "timestamp")
    if timestamp is not None and not isinstance(timestamp, datetime):
        timestamp = None

    subject = _safe_text(_safe_getattr(raw_message, "subject", ""))

    return NatsEnvelope(
        subject=subject,
        data=_safe_bytes(_safe_getattr(raw_message, "data", b"")),
        headers=headers,
        stream=_safe_optional_text(_safe_getattr(metadata, "stream")),
        consumer=_safe_optional_text(_safe_getattr(metadata, "consumer")),
        stream_sequence=stream_sequence,
        consumer_sequence=consumer_sequence,
        timestamp=timestamp,
        message_id=None,
        redelivered=None if delivered is None else delivered > 1,
        pending=_as_int(_safe_getattr(metadata, "num_pending")),
        priority=resolve_metadata_field(
            headers,
            header_name=metadata_config.priority.header,
            default=metadata_config.priority_default_for_subject(subject),
        ),
        classification=resolve_metadata_field(
            headers,
            header_name=metadata_config.classification.header,
            default=metadata_config.classification_default_for_subject(subject),
        ),
        labels=resolve_metadata_labels(
            headers,
            header_name=metadata_config.labels.header,
            default=metadata_config.labels_default_for_subject(subject),
        ),
        reply=_safe_optional_text(_safe_getattr(raw_message, "reply")),
        domain=_safe_optional_text(_safe_getattr(metadata, "domain")),
        received_at=datetime.now(UTC),
        raw_metadata=metadata,
    )
