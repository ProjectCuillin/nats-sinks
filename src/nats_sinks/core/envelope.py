# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Stable internal representation of a NATS message.

`NatsEnvelope` is the object passed from the core runtime to destination sinks.
It is immutable, carries only normalized message data and metadata, and exposes
helpers for decoding payloads and deriving idempotency keys.  The class
deliberately omits ACK, NAK, TERM, and raw consumer operations so sinks cannot
control JetStream acknowledgement behavior.

Payload helpers raise framework-defined serialization errors with subject-level
context but never include the raw payload in exception messages.  Payloads and
headers are potentially sensitive, so logs and errors should describe what
failed without leaking business data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from nats_sinks.core.custody import freeze_custody_metadata, thaw_custody_metadata
from nats_sinks.core.errors import SerializationError
from nats_sinks.core.message_metadata import (
    case_insensitive_header,
    contains_ascii_control_characters,
    normalise_labels_value,
    normalise_metadata_value,
)
from nats_sinks.core.mission_metadata import (
    freeze_mission_metadata,
    thaw_mission_metadata,
)
from nats_sinks.core.payload import (
    NormalizedPayload,
    PayloadStorageMode,
    load_standard_json,
    normalize_payload_for_json_storage,
)
from nats_sinks.core.security_labels import (
    freeze_security_label_profile,
    thaw_security_label_profile,
)


def _safe_text(value: object) -> str | None:
    """Render untrusted header values without allowing arbitrary `__str__` failures."""

    try:
        return str(value)
    except Exception:
        return None


def _normalise_headers(headers: Mapping[str, object] | None) -> Mapping[str, str]:
    normalised: dict[str, str] = {}
    if not headers:
        return MappingProxyType(normalised)

    for key, value in headers.items():
        if value is None:
            continue
        rendered_key = _safe_text(key)
        if rendered_key is None:
            continue
        rendered_key = rendered_key.strip()
        if not rendered_key or contains_ascii_control_characters(rendered_key):
            continue
        rendered: str | None
        if isinstance(value, (list, tuple)):
            rendered_items = [_safe_text(item) for item in value]
            rendered = ",".join(item for item in rendered_items if item is not None)
        else:
            rendered = _safe_text(value)
        if rendered is None:
            continue
        normalised[rendered_key] = rendered
    return MappingProxyType(normalised)


@dataclass(frozen=True, slots=True)
class NatsEnvelope:
    """Immutable message envelope passed to sinks.

    Sinks receive this normalized object rather than raw nats-py messages. This keeps
    destination code away from JetStream acknowledgement primitives.
    """

    subject: str
    data: bytes
    headers: Mapping[str, str]
    stream: str | None
    consumer: str | None
    stream_sequence: int | None
    consumer_sequence: int | None
    timestamp: datetime | None
    message_id: str | None
    redelivered: bool | None
    pending: int | None
    priority: str | None = None
    classification: str | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)
    mission_metadata: Mapping[str, Any] | None = None
    security_labels: Mapping[str, Any] | None = None
    custody: Mapping[str, Any] | None = None
    reply: str | None = None
    domain: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_metadata: object | None = None

    def __post_init__(self) -> None:
        headers = _normalise_headers(self.headers)
        object.__setattr__(self, "headers", headers)

        if self.message_id is None:
            message_id = (
                case_insensitive_header(headers, "Nats-Msg-Id")
                or case_insensitive_header(headers, "Nats-Message-Id")
                or case_insensitive_header(headers, "message-id")
            )
            object.__setattr__(self, "message_id", message_id)

        object.__setattr__(self, "priority", normalise_metadata_value(self.priority))
        object.__setattr__(
            self,
            "classification",
            normalise_metadata_value(self.classification),
        )
        object.__setattr__(self, "labels", normalise_labels_value(self.labels))
        object.__setattr__(
            self,
            "mission_metadata",
            freeze_mission_metadata(self.mission_metadata),
        )
        object.__setattr__(
            self,
            "security_labels",
            freeze_security_label_profile(self.security_labels),
        )
        object.__setattr__(self, "custody", freeze_custody_metadata(self.custody))

    def idempotency_key(self) -> str:
        """Return a stable best-effort idempotency key for this message."""

        if self.stream and self.stream_sequence is not None:
            return f"stream-sequence:{self.stream}:{self.stream_sequence}"
        if self.message_id:
            return f"message-id:{self.message_id}"
        digest = hashlib.sha256(self.data).hexdigest()
        return f"payload-sha256:{self.subject}:{digest}"

    def payload_as_text(self, encoding: str = "utf-8") -> str:
        """Decode the payload as text with a clear framework error on failure."""

        try:
            return self.data.decode(encoding)
        except UnicodeDecodeError as exc:
            msg = f"message payload for subject {self.subject!r} is not valid {encoding}"
            raise SerializationError(msg) from exc

    def payload_as_json(self) -> Any:
        """Decode the payload as JSON without logging the payload content."""

        try:
            return load_standard_json(self.payload_as_text())
        except (ValueError, TypeError) as exc:
            msg = f"message payload for subject {self.subject!r} is not valid JSON"
            raise SerializationError(msg) from exc

    def payload_for_json_storage(
        self,
        *,
        mode: PayloadStorageMode = "json_or_envelope",
    ) -> NormalizedPayload:
        """Return a JSON-compatible payload using the framework storage contract.

        This helper is intended for sinks that store payloads in JSON-capable
        destinations.  Valid JSON is preserved by default.  Non-JSON text or
        bytes are wrapped in the documented nats-sinks JSON payload envelope so
        the original body can still be persisted without weakening the
        commit-then-acknowledge contract.
        """

        return normalize_payload_for_json_storage(self.data, subject=self.subject, mode=mode)

    def metadata_for_json_storage(self, *, stored_at: datetime | None = None) -> dict[str, Any]:
        """Return the standard JSON-compatible NATS metadata snapshot.

        The snapshot captures all headers, known NATS-reserved headers when
        present, JetStream sequence metadata, and epoch timing fields. Missing
        optional NATS metadata remains `None` or absent rather than causing sink
        failures.
        """

        from nats_sinks.core.metadata import build_nats_metadata_snapshot  # noqa: PLC0415

        return build_nats_metadata_snapshot(self, stored_at=stored_at)

    def mission_metadata_for_json_storage(self) -> dict[str, Any] | None:
        """Return the optional mission metadata object as JSON-compatible data.

        The envelope keeps metadata immutable so sinks cannot accidentally
        mutate the core-normalized object.  Sinks call this helper when they
        need a normal dictionary for JSON serialization, database binding, or
        file output.
        """

        return thaw_mission_metadata(self.mission_metadata)

    def security_labels_for_json_storage(self) -> dict[str, Any] | None:
        """Return the optional data-centric security label profile.

        Security labels are normalized by the core and then frozen on the
        envelope.  Sinks call this helper when serializing the structured
        profile to Oracle JSON columns, file JSON records, or future backends.
        The profile is metadata only; it does not replace authorization in the
        destination system.
        """

        return thaw_security_label_profile(self.security_labels)

    def custody_for_json_storage(self) -> dict[str, Any] | None:
        """Return optional tamper-evident custody metadata for sink storage.

        Custody metadata is computed by the core before sink delivery and then
        frozen on the envelope.  Sinks call this helper to serialize the
        evidence next to the destination record without mutating the envelope or
        recomputing hashes with destination-specific behavior.
        """

        return thaw_custody_metadata(self.custody)
