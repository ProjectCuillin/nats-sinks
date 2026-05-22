# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validated mission event metadata support.

Mission metadata is a generic, destination-neutral JSON object carried alongside
the normal NATS payload.  The name comes from mission-oriented operations, but
the feature is intentionally broader: logistics, industrial telemetry,
platform monitoring, security operations, and defence workflows can all use the
same object to preserve context without adding fixed columns for every domain.

The runtime treats this metadata as hostile input.  Publishers may provide it
through a configured NATS header, and deployments may provide global or
subject-specific defaults.  In all cases the object is parsed with a real JSON
parser, duplicate keys are rejected, keys and values are bounded, secret-looking
field names are refused, and the validated object is frozen before it reaches a
sink.  Invalid metadata is a permanent validation failure and therefore follows
the framework's DLQ-before-ACK behavior.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from nats_sinks.core.errors import ValidationError
from nats_sinks.core.message_metadata import case_insensitive_header
from nats_sinks.core.subjects import matches_subject

if TYPE_CHECKING:
    from nats_sinks.core.config import MissionMetadataConfig

DEFAULT_MISSION_METADATA_HEADER = "Nats-Sinks-Mission-Metadata"
MISSION_METADATA_PROFILE_VERSION = 1
CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER_CODEPOINT = 127
DEFAULT_MAX_MISSION_METADATA_BYTES = 8192
MAX_MISSION_METADATA_BYTES = 262_144
MAX_MISSION_METADATA_DEPTH = 8
MAX_MISSION_METADATA_OBJECT_FIELDS = 128
MAX_MISSION_METADATA_ARRAY_ITEMS = 256
MAX_MISSION_METADATA_KEY_LENGTH = 128
MAX_MISSION_METADATA_STRING_LENGTH = 4096
MAX_MISSION_METADATA_INTEGER_ABS = 9_007_199_254_740_991
MISSION_METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MISSION_METADATA_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "private_key",
    "credential",
    "api_key",
    "key_material",
)


class _NonStandardMissionMetadataJsonConstantError(ValueError):
    """Raised when a mission metadata header uses Python-only JSON constants."""


def _json_duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys before Python can silently keep the last one."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python-only JSON constants in mission metadata headers."""

    raise _NonStandardMissionMetadataJsonConstantError(
        f"non-standard JSON constant is not allowed: {value}"
    )


def _contains_control_characters(value: str) -> bool:
    """Return whether text contains terminal/log-injection friendly controls."""

    return any(
        ord(character) < CONTROL_CHARACTER_LIMIT or ord(character) == DELETE_CHARACTER_CODEPOINT
        for character in value
    )


def _validate_key(key: object, *, source: str) -> str:
    """Validate and return one mission metadata object key."""

    if not isinstance(key, str):
        raise ValidationError(f"{source} contains a non-string key")
    if key != key.strip():
        raise ValidationError(f"{source} contains a key with leading or trailing whitespace")
    if not key:
        raise ValidationError(f"{source} contains an empty key")
    if len(key) > MAX_MISSION_METADATA_KEY_LENGTH:
        raise ValidationError(
            f"{source} key {key!r} exceeds {MAX_MISSION_METADATA_KEY_LENGTH} characters"
        )
    if _contains_control_characters(key):
        raise ValidationError(f"{source} key {key!r} contains control characters")
    if MISSION_METADATA_KEY_RE.fullmatch(key) is None:
        raise ValidationError(
            f"{source} key {key!r} must start with a letter and contain only letters, "
            "numbers, underscores, dots, colons, or hyphens"
        )
    key_lower = key.lower()
    if any(part in key_lower for part in MISSION_METADATA_SECRET_KEY_PARTS):
        raise ValidationError(f"{source} key {key!r} looks secret-like and is not allowed")
    return key


def _validate_string(value: str, *, source: str) -> str:
    """Validate one JSON string value."""

    if len(value) > MAX_MISSION_METADATA_STRING_LENGTH:
        raise ValidationError(
            f"{source} string value exceeds {MAX_MISSION_METADATA_STRING_LENGTH} characters"
        )
    if _contains_control_characters(value):
        raise ValidationError(f"{source} string value contains control characters")
    return value


def _validate_json_value(value: object, *, source: str, depth: int) -> Any:
    """Validate a JSON-compatible value and return a normalized copy."""

    if depth > MAX_MISSION_METADATA_DEPTH:
        raise ValidationError(f"{source} exceeds nesting depth {MAX_MISSION_METADATA_DEPTH}")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _validate_string(value, source=source)
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_MISSION_METADATA_INTEGER_ABS:
            raise ValidationError(
                f"{source} integer value exceeds {MAX_MISSION_METADATA_INTEGER_ABS}"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{source} float value must be finite")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_MISSION_METADATA_OBJECT_FIELDS:
            raise ValidationError(
                f"{source} object exceeds {MAX_MISSION_METADATA_OBJECT_FIELDS} fields"
            )
        normalized: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = _validate_key(raw_key, source=source)
            child_source = f"{source}.{key}"
            normalized[key] = _validate_json_value(raw_item, source=child_source, depth=depth + 1)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > MAX_MISSION_METADATA_ARRAY_ITEMS:
            raise ValidationError(
                f"{source} array exceeds {MAX_MISSION_METADATA_ARRAY_ITEMS} items"
            )
        return [_validate_json_value(item, source=f"{source}[]", depth=depth + 1) for item in value]
    raise ValidationError(f"{source} contains unsupported value type {type(value).__name__}")


def _serialized_size(value: Mapping[str, Any]) -> int:
    """Return canonical UTF-8 JSON size for a validated metadata object."""

    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("mission metadata could not be serialized as JSON") from exc
    return len(rendered.encode("utf-8"))


def normalize_mission_metadata_object(
    value: object,
    *,
    max_bytes: int = DEFAULT_MAX_MISSION_METADATA_BYTES,
    allowed_profiles: Sequence[str] = (),
    source: str = "mission metadata",
) -> dict[str, Any]:
    """Validate a mission metadata object and return a JSON-compatible copy.

    The function accepts only JSON object roots.  Empty objects are allowed
    because a deployment may choose to store a metadata shell before publishers
    have started filling it.  If `allowed_profiles` is configured, the object
    must include a `profile` field whose value is one of those configured
    strings.
    """

    if not isinstance(value, Mapping):
        raise ValidationError(f"{source} must be a JSON object")
    normalized = cast(
        "dict[str, Any]",
        _validate_json_value(value, source=source, depth=0),
    )
    if allowed_profiles:
        profile = normalized.get("profile")
        if not isinstance(profile, str) or profile not in set(allowed_profiles):
            allowed = ", ".join(sorted(allowed_profiles))
            raise ValidationError(
                f"{source} profile must be one of the configured values: {allowed}"
            )
    size = _serialized_size(normalized)
    if size > max_bytes:
        raise ValidationError(f"{source} exceeds the configured {max_bytes} byte limit")
    return normalized


def parse_mission_metadata_header(
    value: str,
    *,
    max_bytes: int,
    allowed_profiles: Sequence[str],
) -> dict[str, Any]:
    """Parse and validate the configured mission metadata header value."""

    if len(value.encode("utf-8")) > max_bytes:
        raise ValidationError(
            f"mission metadata header exceeds the configured {max_bytes} byte limit"
        )
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_json_duplicate_key_guard,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValidationError("mission metadata header is not valid JSON") from exc
    except _NonStandardMissionMetadataJsonConstantError as exc:
        raise ValidationError("mission metadata header is not valid JSON") from exc
    except ValueError as exc:
        raise ValidationError(f"mission metadata header is ambiguous: {exc}") from exc
    return normalize_mission_metadata_object(
        parsed,
        max_bytes=max_bytes,
        allowed_profiles=allowed_profiles,
        source="mission metadata header",
    )


def freeze_mission_metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return an immutable view of validated mission metadata."""

    if value is None:
        return None
    return cast("Mapping[str, Any]", _freeze_json_value(value))


def _freeze_json_value(value: object) -> object:
    """Recursively freeze JSON-compatible mappings and lists."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def thaw_mission_metadata(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a mutable JSON-compatible copy suitable for serialization."""

    if value is None:
        return None
    return cast("dict[str, Any]", _thaw_json_value(value))


def _thaw_json_value(value: object) -> object:
    """Convert immutable mission metadata back to JSON-compatible containers."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json_value(item) for item in value]
    return value


def resolve_mission_metadata(
    headers: Mapping[str, str],
    *,
    subject: str,
    config: MissionMetadataConfig,
) -> Mapping[str, Any] | None:
    """Resolve mission metadata from a header, subject default, or global default.

    A present header is authoritative.  An empty header explicitly means "no
    mission metadata" for that message.  Defaults are considered only when the
    header is absent, and subject rules are evaluated before the global default.
    """

    if not config.enabled:
        return None

    header_value = case_insensitive_header(headers, config.header)
    if header_value is not None:
        if not header_value.strip():
            return None
        return freeze_mission_metadata(
            parse_mission_metadata_header(
                header_value,
                max_bytes=config.max_bytes,
                allowed_profiles=config.allowed_profiles,
            )
        )

    for rule in config.rules:
        if matches_subject(rule.subject, subject):
            return freeze_mission_metadata(rule.metadata)
    return freeze_mission_metadata(config.default)
