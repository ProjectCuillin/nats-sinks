# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tamper-evident custody metadata helpers.

The custody feature gives operators a destination-neutral way to persist
evidence about the payload and normalized metadata that entered a sink.  It is
intentionally implemented in the core runtime because the evidence must be
computed before `sink.write_batch(...)` and because a failure to compute it must
follow the same fail-closed path as other pre-sink validation failures.

The metadata produced here is not encryption, not a digital signature, and not
proof that an attacker could never alter data.  It is a tamper-evidence helper:
operators can later recompute hashes over stored records and compare them with
the values persisted by each sink.  For stronger authenticity guarantees,
future versions can add signatures or HMACs while keeping this canonical record
shape as the public contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, cast

from nats_sinks.core.errors import ValidationError

if TYPE_CHECKING:
    from nats_sinks.core.config import CustodyConfig
    from nats_sinks.core.envelope import NatsEnvelope

CustodyHashAlgorithm = Literal["sha256", "sha512"]

CUSTODY_SCHEMA = "nats_sinks.custody.v1"
CUSTODY_SCHEMA_VERSION = 1
CUSTODY_HASH_INPUT_FORMAT = "canonical-json"
CUSTODY_PRIVACY_NOTE = "hashes_are_not_encryption"
CUSTODY_SUPPORTED_ALGORITHMS: frozenset[str] = frozenset({"sha256", "sha512"})
MAX_CUSTODY_KEY_ID_LENGTH = 128
MAX_PREVIOUS_HASH_LENGTH = 128
CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER_CODEPOINT = 127
PREVIOUS_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$|^[A-Fa-f0-9]{128}$")


def _contains_control_characters(value: str) -> bool:
    """Return whether text contains control characters unsafe for audit fields."""

    return any(
        ord(character) < CONTROL_CHARACTER_LIMIT or ord(character) == DELETE_CHARACTER_CODEPOINT
        for character in value
    )


def validate_custody_algorithm(value: str) -> CustodyHashAlgorithm:
    """Return an allow-listed hash algorithm name.

    Configuration validation already enforces the same allow list, but this
    helper protects direct Python API use and makes tests independent from
    Pydantic internals.
    """

    rendered = value.strip().lower().replace("_", "")
    if rendered not in CUSTODY_SUPPORTED_ALGORITHMS:
        allowed = ", ".join(sorted(CUSTODY_SUPPORTED_ALGORITHMS))
        raise ValidationError(f"custody.algorithm must be one of: {allowed}")
    return cast("CustodyHashAlgorithm", rendered)


def validate_custody_key_id(value: object | None) -> str | None:
    """Validate an optional non-secret custody key/version identifier.

    The first implementation does not perform keyed hashing.  The key
    identifier is still useful for future HMAC or signature extensions and for
    deployments that want to record the policy version used to create evidence.
    It is treated as metadata, not secret material.
    """

    if value is None:
        return None
    try:
        rendered = str(value).strip()
    except Exception as exc:
        raise ValidationError("custody.key_id must be renderable text") from exc
    if not rendered:
        return None
    if len(rendered) > MAX_CUSTODY_KEY_ID_LENGTH:
        raise ValidationError(
            f"custody.key_id must not exceed {MAX_CUSTODY_KEY_ID_LENGTH} characters"
        )
    if _contains_control_characters(rendered):
        raise ValidationError("custody.key_id must not contain control characters")
    return rendered


def _canonical_bytes(value: Any, *, source: str, max_bytes: int) -> bytes:
    """Serialize JSON-compatible input into deterministic UTF-8 bytes."""

    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{source} is not canonical JSON serializable") from exc
    data = rendered.encode("utf-8")
    if len(data) > max_bytes:
        raise ValidationError(f"{source} exceeds custody max_hash_input_bytes")
    return data


def canonical_json_bytes(value: Any, *, max_bytes: int) -> bytes:
    """Return canonical JSON bytes for public tests and extension code."""

    return _canonical_bytes(value, source="custody canonical input", max_bytes=max_bytes)


def _digest(data: bytes, *, algorithm: CustodyHashAlgorithm) -> str:
    """Hash already-canonicalized data with an allow-listed algorithm."""

    return hashlib.new(algorithm, data).hexdigest()


def _stable_metadata_for_hashing(envelope: NatsEnvelope) -> dict[str, Any]:
    """Return normalized metadata without sink-local storage timestamps.

    Destination sinks set their own `stored_at` timestamp when writing.  A
    custody metadata hash must be computed before the sink write, so it must
    avoid sink-local fields that do not exist yet.  Message creation,
    JetStream, and nats-sinks receipt timestamps remain part of the evidence.
    """

    metadata = envelope.metadata_for_json_storage(stored_at=envelope.received_at)
    timestamps = metadata.get("timestamps")
    if isinstance(timestamps, dict):
        timestamps = dict(timestamps)
        timestamps.pop("stored_at", None)
        timestamps.pop("stored_at_epoch_ns", None)
        metadata["timestamps"] = timestamps
    metadata.pop("custody", None)
    return metadata


def _previous_record_hash(
    envelope: NatsEnvelope,
    *,
    enabled: bool,
    header: str,
) -> str | None:
    """Read and validate an optional previous-record hash from headers."""

    if not enabled:
        return None
    wanted = header.lower()
    value: str | None = None
    for key, item in envelope.headers.items():
        if key.lower() == wanted:
            value = item.strip()
            break
    if value is None or value == "":
        return None
    if len(value) > MAX_PREVIOUS_HASH_LENGTH or PREVIOUS_HASH_RE.fullmatch(value) is None:
        raise ValidationError(
            "custody previous_record_hash must be a sha256 or sha512 hexadecimal digest"
        )
    return value.lower()


def compute_custody_metadata(
    envelope: NatsEnvelope,
    *,
    config: CustodyConfig,
) -> dict[str, Any]:
    """Compute the immutable custody metadata object for one envelope."""

    algorithm = validate_custody_algorithm(config.algorithm)
    key_id = validate_custody_key_id(config.key_id)
    max_bytes = config.max_hash_input_bytes

    normalized_payload = envelope.payload_for_json_storage(mode="json_or_envelope")
    payload_hash = None
    if config.hash_payload:
        payload_hash = _digest(
            _canonical_bytes(
                normalized_payload.value,
                source="custody payload hash input",
                max_bytes=max_bytes,
            ),
            algorithm=algorithm,
        )

    normalized_metadata = _stable_metadata_for_hashing(envelope)
    metadata_hash = None
    if config.hash_metadata:
        metadata_hash = _digest(
            _canonical_bytes(
                normalized_metadata,
                source="custody metadata hash input",
                max_bytes=max_bytes,
            ),
            algorithm=algorithm,
        )

    previous_hash = _previous_record_hash(
        envelope,
        enabled=config.include_previous_hash,
        header=config.previous_hash_header,
    )

    record_material = {
        "schema": CUSTODY_SCHEMA,
        "schema_version": CUSTODY_SCHEMA_VERSION,
        "algorithm": algorithm,
        "key_id": key_id,
        "payload_hash": payload_hash,
        "metadata_hash": metadata_hash,
        "previous_record_hash": previous_hash,
        "subject": envelope.subject,
        "stream": envelope.stream,
        "stream_sequence": envelope.stream_sequence,
        "message_id": envelope.message_id,
    }
    record_hash = _digest(
        _canonical_bytes(
            record_material,
            source="custody record hash input",
            max_bytes=max_bytes,
        ),
        algorithm=algorithm,
    )

    return {
        "schema": CUSTODY_SCHEMA,
        "schema_version": CUSTODY_SCHEMA_VERSION,
        "algorithm": algorithm,
        "hash_input_format": CUSTODY_HASH_INPUT_FORMAT,
        "key_id": key_id,
        "payload_hash": payload_hash,
        "metadata_hash": metadata_hash,
        "record_hash": record_hash,
        "previous_record_hash": previous_hash,
        "hash_payload": config.hash_payload,
        "hash_metadata": config.hash_metadata,
        "privacy": CUSTODY_PRIVACY_NOTE,
    }


def attach_custody_metadata(
    envelopes: Sequence[NatsEnvelope],
    *,
    config: CustodyConfig,
) -> list[NatsEnvelope]:
    """Return envelopes with custody metadata attached when enabled."""

    if not config.enabled:
        return list(envelopes)
    return [
        replace(envelope, custody=compute_custody_metadata(envelope, config=config))
        for envelope in envelopes
    ]


def freeze_custody_metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return an immutable custody metadata mapping for envelope storage."""

    if value is None:
        return None
    return cast("Mapping[str, Any]", _freeze_json_value(value))


def thaw_custody_metadata(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a mutable JSON-compatible custody metadata copy for sinks."""

    if value is None:
        return None
    return cast("dict[str, Any]", _thaw_json_value(value))


def _freeze_json_value(value: object) -> object:
    """Recursively freeze JSON-compatible mappings and sequences."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: object) -> object:
    """Convert immutable custody containers back to JSON-compatible values."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json_value(item) for item in value]
    return value
