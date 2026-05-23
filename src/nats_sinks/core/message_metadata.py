# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Application-level message metadata helpers.

NATS and JetStream already provide operational metadata such as stream
sequence, consumer sequence, headers, and server timestamps.  nats-sinks also
supports two small application-facing metadata fields that many production
pipelines need regardless of destination backend:

* `priority`, for routing or operational urgency labels such as `low`,
  `normal`, or `critical`.
* `classification`, for information-classification labels such as `public`,
  `internal`, or `restricted`.
* `labels`, for zero or more operator-defined tags such as `billing`,
  `customer-facing`, or `gdpr`.

These fields are intentionally normalized by the core runtime before a sink
receives the message.  Sinks then persist the same `NatsEnvelope.priority` and
`NatsEnvelope.classification` values without inventing destination-specific
rules.  Missing values remain `None`, which becomes JSON `null` in JSON sinks
and SQL `NULL` in relational sinks.
"""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_PRIORITY_HEADER = "Nats-Sinks-Priority"
DEFAULT_CLASSIFICATION_HEADER = "Nats-Sinks-Classification"
DEFAULT_LABELS_HEADER = "Nats-Sinks-Labels"
LABEL_SEPARATOR = ";"
ASCII_CONTROL_MAX = 31
ASCII_DELETE = 127


def contains_ascii_control_characters(value: str) -> bool:
    """Return whether text contains ASCII controls unsafe for metadata fields.

    The helper is intentionally small and shared by configuration, envelope, and
    security-label validation. Metadata values can appear in logs, metrics,
    database columns, file records, and routing decisions, so control
    characters are treated as malformed input at trust boundaries.
    """

    return any(
        ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE for character in value
    )


def normalise_metadata_value(value: object | None) -> str | None:
    """Return a safe metadata string or `None` for missing/empty values.

    Header values come from external publishers, so conversion is defensive.
    Empty strings and whitespace-only values intentionally become `None`.  This
    gives operators an explicit way to publish a message whose priority or
    classification is unknown, while still allowing defaults when a header is
    not provided at all.
    """

    if value is None:
        return None
    try:
        rendered = str(value).strip()
    except Exception:
        return None
    return rendered or None


def normalise_labels_value(value: object | None) -> tuple[str, ...]:
    """Return a stable tuple of labels from strings or iterable values.

    Labels can be provided as a semicolon-separated string in headers or JSON
    config, or as a JSON list in configuration.  Empty items are discarded and
    duplicate labels are removed while preserving the first occurrence.  The
    resulting tuple is immutable so sinks cannot accidentally mutate the
    envelope after the core has normalized it.
    """

    if value is None:
        return ()

    raw_items: list[object]
    if isinstance(value, str):
        raw_items = list(value.split(LABEL_SEPARATOR))
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    else:
        raw_items = [value]

    labels: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        rendered = normalise_metadata_value(item)
        if rendered is None:
            continue
        if rendered in seen:
            continue
        labels.append(rendered)
        seen.add(rendered)
    return tuple(labels)


def labels_to_storage_string(labels: object | None) -> str | None:
    """Render labels as semicolon-separated text for scalar sink columns."""

    normalized = normalise_labels_value(labels)
    if not normalized:
        return None
    return LABEL_SEPARATOR.join(normalized)


def case_insensitive_header(headers: Mapping[str, str], name: str) -> str | None:
    """Look up a header by name without depending on publisher casing."""

    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def resolve_metadata_field(
    headers: Mapping[str, str],
    *,
    header_name: str,
    default: str | None,
) -> str | None:
    """Resolve one metadata field from headers, default, or null.

    Resolution order is deliberately precise:

    1. If the configured header is present and non-empty, use its value.
    2. If the configured header is present but empty, return `None`.
    3. If the configured header is absent, use the configured default.
    4. If the default is missing or empty, return `None`.
    """

    header_value = case_insensitive_header(headers, header_name)
    if header_value is not None:
        return normalise_metadata_value(header_value)
    return normalise_metadata_value(default)


def resolve_metadata_labels(
    headers: Mapping[str, str],
    *,
    header_name: str,
    default: object | None,
) -> tuple[str, ...]:
    """Resolve labels from headers, subject/global defaults, or an empty tuple.

    The ordering mirrors priority and classification: a present header wins,
    an empty present header becomes no labels, and defaults are only considered
    when the header is absent.
    """

    header_value = case_insensitive_header(headers, header_name)
    if header_value is not None:
        return normalise_labels_value(header_value)
    return normalise_labels_value(default)
