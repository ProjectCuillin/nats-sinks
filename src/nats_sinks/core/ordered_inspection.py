# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Read-only ordered-consumer inspection helpers.

Ordered consumers are useful for operator inspection because they provide an
ephemeral, in-order view of a JetStream subject. They are deliberately not used
for production sink writes in nats-sinks: the durable pull-consumer runner owns
commit-then-acknowledge processing, retries, DLQ handling, and sink idempotency.

This module keeps the inspection path small, bounded, redacted by default, and
separate from sink construction. It never ACKs messages and never calls a sink.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from nats_sinks.core.config import (
    MessageMetadataConfig,
    MissionMetadataConfig,
    SecurityLabelProfileConfig,
)
from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError

DEFAULT_MAX_MESSAGES = 10
MAX_ALLOWED_MESSAGES = 1000
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_ALLOWED_PAYLOAD_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_PENDING_MESSAGES = 128
MAX_PENDING_MESSAGES = 4096
DEFAULT_PENDING_BYTES = 8 * 1024 * 1024
MAX_PENDING_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_HEADERS = 32
MAX_ALLOWED_HEADERS = 128
DEFAULT_MAX_HEADER_VALUE_BYTES = 256
MAX_ALLOWED_HEADER_VALUE_BYTES = 2048
DEFAULT_OUTPUT_ROOT = Path(".local") / "nats-sinks" / "inspection"

_SENSITIVE_HEADER_TOKENS = (
    "authorization",
    "cookie",
    "credential",
    "jwt",
    "nkey",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True, slots=True)
class OrderedInspectionOptions:
    """Bounded controls for a read-only ordered-consumer inspection run."""

    max_messages: int = DEFAULT_MAX_MESSAGES
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    include_payload: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    pending_messages: int = DEFAULT_PENDING_MESSAGES
    pending_bytes: int = DEFAULT_PENDING_BYTES
    idle_heartbeat_seconds: float | None = None
    max_headers: int = DEFAULT_MAX_HEADERS
    max_header_value_bytes: int = DEFAULT_MAX_HEADER_VALUE_BYTES


@dataclass(frozen=True, slots=True)
class OrderedInspectionRecord:
    """One sanitized message observation from an ordered inspection run."""

    subject: str
    stream: str | None
    consumer: str | None
    stream_sequence: int | None
    consumer_sequence: int | None
    timestamp: datetime | None
    received_at: datetime
    priority: str | None
    classification: str | None
    labels: tuple[str, ...]
    headers: Mapping[str, str]
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Render a JSON-compatible record that is safe for local inspection files."""

        return {
            "inspection_only": True,
            "subject": self.subject,
            "stream": self.stream,
            "consumer": self.consumer,
            "stream_sequence": self.stream_sequence,
            "consumer_sequence": self.consumer_sequence,
            "timestamp": _datetime_or_none(self.timestamp),
            "received_at": _datetime_or_none(self.received_at),
            "priority": self.priority,
            "classification": self.classification,
            "labels": list(self.labels),
            "headers": dict(self.headers),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class OrderedInspectionResult:
    """Summary of a bounded ordered inspection run."""

    records: tuple[OrderedInspectionRecord, ...]
    payload_bytes_seen: int
    stopped_reason: str

    @property
    def messages_seen(self) -> int:
        """Return the number of records emitted by the inspection."""

        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        """Render a JSON-compatible summary for script-friendly callers."""

        return {
            "inspection_only": True,
            "messages_seen": self.messages_seen,
            "payload_bytes_seen": self.payload_bytes_seen,
            "stopped_reason": self.stopped_reason,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class OrderedConsumerCapabilityResult:
    """Compatibility result for the public NATS ordered-consumer API boundary."""

    supported: bool
    checked_api: str
    reason: str


def detect_ordered_consumer_capability(jetstream: object) -> OrderedConsumerCapabilityResult:
    """Detect ordered-consumer support through documented public client attributes.

    The inspection command must fail closed when support is missing or
    ambiguous. Returning a structured result keeps that decision explicit while
    avoiding private NATS client APIs, dynamic imports, or exception text that
    may include environment-specific details.
    """

    checked_api = "JetStreamContext.subscribe"
    subscribe = getattr(jetstream, "subscribe", None)
    if subscribe is None:
        return OrderedConsumerCapabilityResult(
            supported=False,
            checked_api=checked_api,
            reason="JetStream context does not expose subscribe",
        )
    if not callable(subscribe):
        return OrderedConsumerCapabilityResult(
            supported=False,
            checked_api=checked_api,
            reason="JetStream subscribe attribute is not callable",
        )
    try:
        signature = inspect.signature(subscribe)
    except (TypeError, ValueError):
        return OrderedConsumerCapabilityResult(
            supported=False,
            checked_api=checked_api,
            reason="JetStream subscribe signature is unavailable",
        )
    if "ordered_consumer" not in signature.parameters:
        return OrderedConsumerCapabilityResult(
            supported=False,
            checked_api=checked_api,
            reason="JetStream subscribe API does not expose ordered_consumer",
        )
    return OrderedConsumerCapabilityResult(
        supported=True,
        checked_api=checked_api,
        reason="JetStream subscribe API exposes ordered_consumer",
    )


def ordered_consumer_supported(jetstream: object) -> bool:
    """Return true when the active client exposes `ordered_consumer` subscribe support."""

    return detect_ordered_consumer_capability(jetstream).supported


def ordered_consumer_capability_error(capability: OrderedConsumerCapabilityResult) -> str:
    """Render a sanitized fail-closed message for ordered-inspection callers."""

    return (
        "ordered-consumer inspection requires a nats-py JetStream subscribe API "
        f"with an ordered_consumer option; {capability.reason}"
    )


def validate_ordered_inspection_options(options: OrderedInspectionOptions) -> None:
    """Validate hard bounds before a live inspection can connect to NATS."""

    _validate_int_range(
        options.max_messages,
        name="max_messages",
        minimum=1,
        maximum=MAX_ALLOWED_MESSAGES,
    )
    _validate_int_range(
        options.max_payload_bytes,
        name="max_payload_bytes",
        minimum=0,
        maximum=MAX_ALLOWED_PAYLOAD_BYTES,
    )
    _validate_float_range(
        options.timeout_seconds,
        name="timeout_seconds",
        minimum=0.01,
        maximum=MAX_TIMEOUT_SECONDS,
    )
    _validate_int_range(
        options.pending_messages,
        name="pending_messages",
        minimum=1,
        maximum=MAX_PENDING_MESSAGES,
    )
    _validate_int_range(
        options.pending_bytes,
        name="pending_bytes",
        minimum=1,
        maximum=MAX_PENDING_BYTES,
    )
    if options.idle_heartbeat_seconds is not None:
        _validate_float_range(
            options.idle_heartbeat_seconds,
            name="idle_heartbeat_seconds",
            minimum=0.1,
            maximum=MAX_TIMEOUT_SECONDS,
        )
    _validate_int_range(
        options.max_headers,
        name="max_headers",
        minimum=0,
        maximum=MAX_ALLOWED_HEADERS,
    )
    _validate_int_range(
        options.max_header_value_bytes,
        name="max_header_value_bytes",
        minimum=1,
        maximum=MAX_ALLOWED_HEADER_VALUE_BYTES,
    )


def resolve_inspection_output_path(
    output_path: Path,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Resolve an inspection JSONL path under the approved local output root."""

    if output_path.name in {"", ".", ".."}:
        raise ConfigurationError("inspection output path must name a JSONL file")
    if output_path.suffix != ".jsonl":
        raise ConfigurationError("inspection output path must end with .jsonl")

    root = output_root.expanduser().resolve(strict=False)
    candidate = output_path.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    destination = candidate.resolve(strict=False)
    if not destination.is_relative_to(root):
        raise ConfigurationError("inspection output path must stay inside the output root")
    return destination


async def collect_ordered_inspection_records(
    jetstream: object,
    *,
    subject: str,
    stream: str,
    options: OrderedInspectionOptions | None = None,
    message_metadata: MessageMetadataConfig | None = None,
    mission_metadata: MissionMetadataConfig | None = None,
    security_labels: SecurityLabelProfileConfig | None = None,
) -> OrderedInspectionResult:
    """Collect bounded, redacted records through an ordered consumer.

    The function never acknowledges messages. `nats-py` ordered consumers use an
    ephemeral consumer with no ACK policy; nats-sinks still treats this as an
    inspection-only view and never routes records to sinks.
    """

    inspection_options = options or OrderedInspectionOptions()
    validate_ordered_inspection_options(inspection_options)
    capability = detect_ordered_consumer_capability(jetstream)
    if not capability.supported:
        raise ConfigurationError(ordered_consumer_capability_error(capability))

    jetstream_client = cast(Any, jetstream)
    subscription = await jetstream_client.subscribe(
        subject,
        stream=stream,
        ordered_consumer=True,
        manual_ack=False,
        idle_heartbeat=inspection_options.idle_heartbeat_seconds,
        pending_msgs_limit=inspection_options.pending_messages,
        pending_bytes_limit=inspection_options.pending_bytes,
    )
    records: list[OrderedInspectionRecord] = []
    payload_bytes_seen = 0
    stopped_reason = "max_messages"
    try:
        for _ in range(inspection_options.max_messages):
            try:
                raw_message = await subscription.next_msg(
                    timeout=inspection_options.timeout_seconds
                )
            except Exception as exc:
                if _is_timeout_error(exc):
                    stopped_reason = "timeout"
                    break
                raise

            envelope = envelope_from_nats_message(
                raw_message,
                message_metadata=message_metadata,
                mission_metadata=mission_metadata,
                security_labels=security_labels,
            )
            next_total = payload_bytes_seen + len(envelope.data)
            if next_total > inspection_options.max_payload_bytes:
                stopped_reason = "max_payload_bytes"
                break
            payload_bytes_seen = next_total
            records.append(_inspection_record(envelope, options=inspection_options))
        else:
            stopped_reason = "max_messages"
    finally:
        await _close_subscription(subscription)

    return OrderedInspectionResult(
        records=tuple(records),
        payload_bytes_seen=payload_bytes_seen,
        stopped_reason=stopped_reason,
    )


def render_ordered_inspection_text(result: OrderedInspectionResult) -> str:
    """Render concise human output without headers or payload data."""

    lines = [
        "Ordered inspection result",
        "inspection_only=true",
        f"messages_seen={result.messages_seen}",
        f"payload_bytes_seen={result.payload_bytes_seen}",
        f"stopped_reason={result.stopped_reason}",
    ]
    for record in result.records:
        labels = ";".join(record.labels)
        lines.append(
            "record "
            f"stream_sequence={_value_or_null(record.stream_sequence)} "
            f"consumer_sequence={_value_or_null(record.consumer_sequence)} "
            f"subject={record.subject!r} "
            f"priority={_value_or_null(record.priority)} "
            f"classification={_value_or_null(record.classification)} "
            f"labels={labels!r} "
            f"payload_bytes={record.payload['bytes']} "
            f"payload_redacted={str(record.payload['redacted']).lower()}"
        )
    return "\n".join(lines)


def render_ordered_inspection_jsonl(records: Sequence[OrderedInspectionRecord]) -> str:
    """Render sanitized inspection records as newline-delimited JSON."""

    return "\n".join(
        json.dumps(record.to_dict(), sort_keys=False, allow_nan=False) for record in records
    )


def write_ordered_inspection_jsonl(
    records: Sequence[OrderedInspectionRecord],
    output_path: Path,
) -> None:
    """Write sanitized JSONL records to a local inspection file."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_ordered_inspection_jsonl(records)
    output_path.write_text(f"{text}\n" if text else "", encoding="utf-8")


def _inspection_record(
    envelope: NatsEnvelope,
    *,
    options: OrderedInspectionOptions,
) -> OrderedInspectionRecord:
    return OrderedInspectionRecord(
        subject=envelope.subject,
        stream=envelope.stream,
        consumer=envelope.consumer,
        stream_sequence=envelope.stream_sequence,
        consumer_sequence=envelope.consumer_sequence,
        timestamp=envelope.timestamp,
        received_at=envelope.received_at,
        priority=envelope.priority,
        classification=envelope.classification,
        labels=envelope.labels,
        headers=_redacted_headers(
            envelope.headers,
            max_headers=options.max_headers,
            max_value_bytes=options.max_header_value_bytes,
        ),
        payload=_payload_summary(envelope.data, include_payload=options.include_payload),
    )


def _payload_summary(data: bytes, *, include_payload: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "redacted": not include_payload,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if not include_payload:
        return payload
    try:
        payload["encoding"] = "utf-8"
        payload["data"] = data.decode("utf-8")
    except UnicodeDecodeError:
        payload["encoding"] = "base64"
        payload["data"] = base64.b64encode(data).decode("ascii")
    return payload


def _redacted_headers(
    headers: Mapping[str, str],
    *,
    max_headers: int,
    max_value_bytes: int,
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for index, key in enumerate(sorted(headers)):
        if index >= max_headers:
            rendered["<truncated>"] = f"{len(headers) - max_headers} header(s) omitted"
            break
        value = headers[key]
        if _sensitive_header_name(key):
            rendered[key] = "<redacted>"
            continue
        rendered[key] = _bounded_header_value(value, max_value_bytes=max_value_bytes)
    return rendered


def _bounded_header_value(value: str, *, max_value_bytes: int) -> str:
    value_bytes = value.encode("utf-8")
    if len(value_bytes) <= max_value_bytes:
        return value
    bounded = value_bytes[:max_value_bytes].decode("utf-8", errors="replace")
    return f"{bounded}<truncated>"


def _sensitive_header_name(name: str) -> bool:
    normalized = name.casefold()
    return any(token in normalized for token in _SENSITIVE_HEADER_TOKENS)


async def _close_subscription(subscription: object) -> None:
    unsubscribe = getattr(subscription, "unsubscribe", None)
    if not callable(unsubscribe):
        return
    result = unsubscribe()
    if inspect.isawaitable(result):
        await result


def _is_timeout_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError"


def _validate_int_range(value: int, *, name: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or value < minimum or value > maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")


def _validate_float_range(value: float, *, name: str, minimum: float, maximum: float) -> None:
    if value < minimum or value > maximum:
        raise ConfigurationError(f"{name} must be between {minimum:g} and {maximum:g}")


def _datetime_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _value_or_null(value: object | None) -> str:
    if value is None:
        return "null"
    return str(value)
