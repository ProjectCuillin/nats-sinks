# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validated data-centric security label profile support.

The security label profile is an optional JSON object carried alongside each
normalized `NatsEnvelope`.  It is intended for deployments that need more than
the scalar `priority`, `classification`, and `labels` fields: releasability
lists, handling caveats, policy identifiers, originator information, and
retention categories can all be represented in one destination-neutral profile.

The profile is metadata, not authorization.  It can inform downstream policy
engines, audit workflows, routing, and retention decisions, but access control
must still be enforced by the destination system and surrounding platform.

Like mission metadata, publishers can provide the profile through a configured
NATS header, while operators can define global or subject-specific defaults in
configuration.  Every value is treated as hostile input: JSON parsing rejects
duplicate keys and non-standard constants, field names are allow-listed, string
sizes are bounded, optional allow lists can fail closed, and the final object is
frozen before any sink receives it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from nats_sinks.core.errors import ValidationError
from nats_sinks.core.message_metadata import (
    case_insensitive_header,
    normalise_labels_value,
    normalise_metadata_value,
)
from nats_sinks.core.mission_metadata import normalize_mission_metadata_object
from nats_sinks.core.payload import load_standard_json
from nats_sinks.core.subjects import matches_subject

if TYPE_CHECKING:
    from nats_sinks.core.config import SecurityLabelProfileConfig

DEFAULT_SECURITY_LABELS_HEADER = "Nats-Sinks-Security-Labels"
SECURITY_LABEL_PROFILE_NAME = "nats-sinks.security-label.v1"
DEFAULT_MAX_SECURITY_LABEL_BYTES = 8192
MAX_SECURITY_LABEL_BYTES = 262_144
MAX_SECURITY_LABEL_LIST_ITEMS = 128
MAX_SECURITY_LABEL_STRING_LENGTH = 512

SCALAR_FIELDS = frozenset(
    {
        "profile",
        "priority",
        "classification",
        "owner",
        "originator",
        "policy_id",
        "retention_category",
    }
)
LIST_FIELDS = frozenset({"labels", "releasability", "handling_caveats"})
ALLOWED_FIELDS = SCALAR_FIELDS | LIST_FIELDS | {"extensions"}


def _normalize_scalar(value: object, *, field: str) -> str | None:
    """Normalize one optional scalar profile value.

    Security labels frequently travel through message headers and configuration
    files, so the function avoids implicit trust in input type or string
    rendering.  Empty values become `None`, while overly large values fail
    closed before they can enter logs, metrics, file names, or SQL bind data.
    """

    normalized = normalise_metadata_value(value)
    if normalized is None:
        return None
    if len(normalized) > MAX_SECURITY_LABEL_STRING_LENGTH:
        raise ValidationError(
            f"security label field {field!r} exceeds {MAX_SECURITY_LABEL_STRING_LENGTH} characters"
        )
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise ValidationError(f"security label field {field!r} contains control characters")
    return normalized


def _normalize_list(value: object, *, field: str) -> list[str]:
    """Normalize a semicolon-separated string or JSON array into label values."""

    labels = list(normalise_labels_value(value))
    if len(labels) > MAX_SECURITY_LABEL_LIST_ITEMS:
        raise ValidationError(
            f"security label field {field!r} exceeds {MAX_SECURITY_LABEL_LIST_ITEMS} items"
        )
    for item in labels:
        if len(item) > MAX_SECURITY_LABEL_STRING_LENGTH:
            raise ValidationError(
                f"security label field {field!r} contains an item longer than "
                f"{MAX_SECURITY_LABEL_STRING_LENGTH} characters"
            )
        if "\x00" in item or "\n" in item or "\r" in item:
            raise ValidationError(f"security label field {field!r} contains control characters")
    return labels


def _require_allowed_value(
    value: str | None,
    *,
    field: str,
    allowed: Sequence[str],
) -> None:
    """Fail closed when a configured allow list does not contain a scalar value."""

    if value is None or not allowed:
        return
    if value not in set(allowed):
        allowed_values = ", ".join(sorted(allowed))
        raise ValidationError(
            f"security label field {field!r} must be one of the configured values: {allowed_values}"
        )


def _require_allowed_list_values(
    values: Sequence[str],
    *,
    field: str,
    allowed: Sequence[str],
) -> None:
    """Fail closed when a configured allow list does not contain every list item."""

    if not allowed:
        return
    allowed_set = set(allowed)
    unknown = [value for value in values if value not in allowed_set]
    if unknown:
        allowed_values = ", ".join(sorted(allowed))
        raise ValidationError(
            f"security label field {field!r} contains values outside the configured "
            f"allow list: {', '.join(unknown)}. Allowed values: {allowed_values}"
        )


def _validate_allowed_fields(value: Mapping[str, Any], *, source: str) -> None:
    """Reject unknown root fields so the profile remains reviewable."""

    unknown = sorted(set(value) - ALLOWED_FIELDS)
    if unknown:
        raise ValidationError(
            f"{source} contains unsupported security label fields: {', '.join(unknown)}"
        )


def normalize_security_label_profile(
    value: object,
    *,
    max_bytes: int = DEFAULT_MAX_SECURITY_LABEL_BYTES,
    allowed_priorities: Sequence[str] = (),
    allowed_classifications: Sequence[str] = (),
    allowed_releasability: Sequence[str] = (),
    allowed_handling_caveats: Sequence[str] = (),
    allowed_retention_categories: Sequence[str] = (),
    fallback_priority: str | None = None,
    fallback_classification: str | None = None,
    fallback_labels: Sequence[str] = (),
    source: str = "security label profile",
) -> dict[str, Any]:
    """Validate and normalize one security label profile object.

    Missing `priority`, `classification`, and `labels` values are filled from
    the already-normalized message metadata.  That keeps the scalar fields and
    structured profile aligned without forcing publishers to duplicate the same
    values in several headers.
    """

    if not isinstance(value, Mapping):
        raise ValidationError(f"{source} must be a JSON object")

    normalized_generic = normalize_mission_metadata_object(
        value,
        max_bytes=max_bytes,
        source=source,
    )
    _validate_allowed_fields(normalized_generic, source=source)

    profile_name = _normalize_scalar(
        normalized_generic.get("profile", SECURITY_LABEL_PROFILE_NAME),
        field="profile",
    )
    if profile_name != SECURITY_LABEL_PROFILE_NAME:
        raise ValidationError(f"{source} profile must be {SECURITY_LABEL_PROFILE_NAME!r}")

    priority = _normalize_scalar(
        normalized_generic.get("priority", fallback_priority),
        field="priority",
    )
    classification = _normalize_scalar(
        normalized_generic.get("classification", fallback_classification),
        field="classification",
    )
    owner = _normalize_scalar(normalized_generic.get("owner"), field="owner")
    originator = _normalize_scalar(normalized_generic.get("originator"), field="originator")
    policy_id = _normalize_scalar(normalized_generic.get("policy_id"), field="policy_id")
    retention_category = _normalize_scalar(
        normalized_generic.get("retention_category"),
        field="retention_category",
    )

    labels = _normalize_list(normalized_generic.get("labels", fallback_labels), field="labels")
    releasability = _normalize_list(
        normalized_generic.get("releasability", ()),
        field="releasability",
    )
    handling_caveats = _normalize_list(
        normalized_generic.get("handling_caveats", ()),
        field="handling_caveats",
    )

    _require_allowed_value(priority, field="priority", allowed=allowed_priorities)
    _require_allowed_value(
        classification,
        field="classification",
        allowed=allowed_classifications,
    )
    _require_allowed_value(
        retention_category,
        field="retention_category",
        allowed=allowed_retention_categories,
    )
    _require_allowed_list_values(
        releasability,
        field="releasability",
        allowed=allowed_releasability,
    )
    _require_allowed_list_values(
        handling_caveats,
        field="handling_caveats",
        allowed=allowed_handling_caveats,
    )

    result: dict[str, Any] = {
        "profile": SECURITY_LABEL_PROFILE_NAME,
        "priority": priority,
        "classification": classification,
        "labels": labels,
        "releasability": releasability,
        "handling_caveats": handling_caveats,
        "owner": owner,
        "originator": originator,
        "policy_id": policy_id,
        "retention_category": retention_category,
    }
    extensions = normalized_generic.get("extensions")
    if extensions is not None:
        if not isinstance(extensions, Mapping):
            raise ValidationError(f"{source}.extensions must be a JSON object")
        result["extensions"] = normalize_mission_metadata_object(
            extensions,
            max_bytes=max_bytes,
            source=f"{source}.extensions",
        )
    return normalize_mission_metadata_object(result, max_bytes=max_bytes, source=source)


def parse_security_label_header(
    value: str,
    *,
    max_bytes: int,
    allowed_priorities: Sequence[str],
    allowed_classifications: Sequence[str],
    allowed_releasability: Sequence[str],
    allowed_handling_caveats: Sequence[str],
    allowed_retention_categories: Sequence[str],
    fallback_priority: str | None,
    fallback_classification: str | None,
    fallback_labels: Sequence[str],
) -> dict[str, Any]:
    """Parse and validate the configured security label header value."""

    if len(value.encode("utf-8")) > max_bytes:
        raise ValidationError(
            f"security label header exceeds the configured {max_bytes} byte limit"
        )
    try:
        parsed = load_standard_json(value)
    except ValueError as exc:
        raise ValidationError("security label header is not valid JSON") from exc
    return normalize_security_label_profile(
        parsed,
        max_bytes=max_bytes,
        allowed_priorities=allowed_priorities,
        allowed_classifications=allowed_classifications,
        allowed_releasability=allowed_releasability,
        allowed_handling_caveats=allowed_handling_caveats,
        allowed_retention_categories=allowed_retention_categories,
        fallback_priority=fallback_priority,
        fallback_classification=fallback_classification,
        fallback_labels=fallback_labels,
        source="security label header",
    )


def freeze_security_label_profile(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return an immutable security label profile."""

    if value is None:
        return None
    return cast("Mapping[str, Any]", _freeze_json_value(value))


def thaw_security_label_profile(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a mutable JSON-compatible profile copy for sinks."""

    if value is None:
        return None
    return cast("dict[str, Any]", _thaw_json_value(value))


def _freeze_json_value(value: object) -> object:
    """Recursively freeze JSON-compatible values."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: object) -> object:
    """Convert immutable profile containers back to JSON-compatible containers."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json_value(item) for item in value]
    return value


def resolve_security_label_profile(  # noqa: PLR0911
    headers: Mapping[str, str],
    *,
    subject: str,
    priority: str | None,
    classification: str | None,
    labels: Sequence[str],
    config: SecurityLabelProfileConfig,
) -> Mapping[str, Any] | None:
    """Resolve security labels from a header, subject default, or global default."""

    if not config.enabled:
        return None

    header_value = case_insensitive_header(headers, config.header)
    if header_value is not None:
        if not header_value.strip():
            return None
        return freeze_security_label_profile(
            parse_security_label_header(
                header_value,
                max_bytes=config.max_bytes,
                allowed_priorities=config.allowed_priorities,
                allowed_classifications=config.allowed_classifications,
                allowed_releasability=config.allowed_releasability,
                allowed_handling_caveats=config.allowed_handling_caveats,
                allowed_retention_categories=config.allowed_retention_categories,
                fallback_priority=priority,
                fallback_classification=classification,
                fallback_labels=labels,
            )
        )

    for rule in config.rules:
        if matches_subject(rule.subject, subject):
            if rule.profile is None:
                return None
            return freeze_security_label_profile(
                normalize_security_label_profile(
                    rule.profile,
                    max_bytes=config.max_bytes,
                    allowed_priorities=config.allowed_priorities,
                    allowed_classifications=config.allowed_classifications,
                    allowed_releasability=config.allowed_releasability,
                    allowed_handling_caveats=config.allowed_handling_caveats,
                    allowed_retention_categories=config.allowed_retention_categories,
                    fallback_priority=priority,
                    fallback_classification=classification,
                    fallback_labels=labels,
                    source=f"security label rule {rule.subject}",
                )
            )

    if config.default is None:
        return None
    return freeze_security_label_profile(
        normalize_security_label_profile(
            config.default,
            max_bytes=config.max_bytes,
            allowed_priorities=config.allowed_priorities,
            allowed_classifications=config.allowed_classifications,
            allowed_releasability=config.allowed_releasability,
            allowed_handling_caveats=config.allowed_handling_caveats,
            allowed_retention_categories=config.allowed_retention_categories,
            fallback_priority=priority,
            fallback_classification=classification,
            fallback_labels=labels,
            source="security label default",
        )
    )
