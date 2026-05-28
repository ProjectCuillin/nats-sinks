# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Map normalized NATS envelopes to Gotham RevDB object-create requests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PermanentSinkError, SerializationError
from nats_sinks.gotham.config import GothamSinkConfig


@dataclass(frozen=True, slots=True)
class GothamObjectWrite:
    """One prepared Gotham object-create request."""

    external_id: str
    request: dict[str, Any]
    size_bytes: int


@dataclass(frozen=True, slots=True)
class GothamPreparedBatch:
    """A bounded Gotham batch prepared for one sink write call."""

    objects: tuple[GothamObjectWrite, ...]
    size_bytes: int


def gotham_external_id(envelope: NatsEnvelope, *, config: GothamSinkConfig) -> str:
    """Return the deterministic external ID configured for Gotham writes."""

    if config.external_id_strategy == "idempotency_key":
        return envelope.idempotency_key()
    if config.external_id_strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise PermanentSinkError(
                "Gotham external_id_strategy='stream_sequence' requires stream metadata"
            )
        return f"stream-sequence:{envelope.stream}:{envelope.stream_sequence}"
    if config.external_id_strategy == "message_id":
        if not envelope.message_id:
            raise PermanentSinkError(
                "Gotham external_id_strategy='message_id' requires a message ID"
            )
        return f"message-id:{envelope.message_id}"
    digest = hashlib.sha256(envelope.data).hexdigest()
    return f"payload-sha256:{envelope.subject}:{digest}"


def gotham_object_request_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: GothamSinkConfig,
) -> GothamObjectWrite:
    """Build and size-check one Gotham object-create request."""

    external_id = gotham_external_id(envelope, config=config)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    properties: list[dict[str, Any]] = [
        _property(config.external_id_property_type, external_id),
        _property(config.subject_property_type, envelope.subject),
        _property(config.payload_property_type, normalized_payload.value),
    ]
    _append_optional_property(
        properties,
        config.payload_info_property_type,
        {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
    )
    if config.priority_property_type is not None and envelope.priority is not None:
        properties.append(_property(config.priority_property_type, envelope.priority))
    if config.classification_property_type is not None and envelope.classification is not None:
        properties.append(_property(config.classification_property_type, envelope.classification))
    if config.labels_property_type is not None and envelope.labels:
        properties.append(_property(config.labels_property_type, ",".join(envelope.labels)))
    if config.labels_list_property_type is not None and envelope.labels:
        properties.append(_property(config.labels_list_property_type, list(envelope.labels)))
    if config.include_metadata:
        _append_optional_property(
            properties,
            config.metadata_property_type,
            envelope.metadata_for_json_storage(),
        )
    if config.include_mission_metadata:
        _append_optional_property(
            properties,
            config.mission_metadata_property_type,
            envelope.mission_metadata_for_json_storage(),
        )
    if config.include_security_labels:
        _append_optional_property(
            properties,
            config.security_labels_property_type,
            envelope.security_labels_for_json_storage(),
        )
    if config.include_custody:
        _append_optional_property(
            properties,
            config.custody_property_type,
            envelope.custody_for_json_storage(),
        )

    request: dict[str, Any] = {
        "title": _object_title(external_id, config=config),
        "properties": properties,
        "validationMode": config.validation_mode,
    }
    if config.security_portion_markings:
        request["security"] = {"portionMarkings": list(config.security_portion_markings)}

    size_bytes = _json_size_bytes(request)
    if size_bytes > config.max_object_bytes:
        raise PermanentSinkError("Gotham object request exceeds max_object_bytes")
    return GothamObjectWrite(external_id=external_id, request=request, size_bytes=size_bytes)


def prepare_gotham_batch(
    messages: Sequence[NatsEnvelope],
    *,
    config: GothamSinkConfig,
) -> GothamPreparedBatch:
    """Build and size-check a Gotham batch."""

    objects: list[GothamObjectWrite] = []
    external_ids: set[str] = set()
    total_size = 0
    for message in messages:
        prepared = gotham_object_request_for_envelope(message, config=config)
        if prepared.external_id in external_ids:
            raise PermanentSinkError("Gotham batch contains duplicate external IDs")
        external_ids.add(prepared.external_id)
        total_size += prepared.size_bytes
        if total_size > config.max_batch_bytes:
            raise PermanentSinkError("Gotham batch exceeds max_batch_bytes")
        objects.append(prepared)
    return GothamPreparedBatch(objects=tuple(objects), size_bytes=total_size)


def _property(property_type: str, value: Any) -> dict[str, Any]:
    return {"propertyType": property_type, "value": value}


def _append_optional_property(
    properties: list[dict[str, Any]],
    property_type: str | None,
    value: Any,
) -> None:
    if property_type is not None and value is not None:
        properties.append(_property(property_type, value))


def _object_title(external_id: str, *, config: GothamSinkConfig) -> str:
    title = f"{config.object_title_prefix}{external_id}"
    if len(title) <= config.max_title_length:
        return title
    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:16]
    available = max(config.max_title_length - len(digest) - 1, 1)
    return f"{title[:available]}-{digest}"


def _json_size_bytes(value: Any) -> int:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("Gotham object request is not JSON serializable") from exc
    return len(rendered.encode("utf-8"))
