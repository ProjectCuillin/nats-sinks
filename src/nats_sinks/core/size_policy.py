# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Core payload and metadata size-policy enforcement.

The size policy is a destination-neutral guardrail evaluated before a sink
receives a message.  It gives operators a single place to bound message bodies,
headers, application metadata, mission metadata, and normalized record size
without having to duplicate the same checks in Oracle, file, object-storage, or
future sinks.

The policy intentionally reports only reason codes and byte counts.  It never
copies payload bytes, header values, labels, mission metadata values, table
names, connection strings, or other operational details into exceptions.  A
violation is a permanent validation failure and therefore follows the existing
DLQ-before-ACK behavior in the runner.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import SizePolicyViolationError

if TYPE_CHECKING:
    from nats_sinks.core.config import SizePolicyConfig


@dataclass(frozen=True, slots=True)
class SizePolicyViolation:
    """One sanitized size-policy violation for one envelope.

    `actual` and `limit` are byte or count values. They are safe enough for
    operator logs and DLQ error text, while the potentially sensitive values
    that caused the violation stay out of the error string.
    """

    index: int
    subject: str
    reason: str
    actual: int
    limit: int


@dataclass(frozen=True, slots=True)
class SizePolicyEvaluation:
    """Result of evaluating a batch against the configured size policy."""

    accepted_indexes: tuple[int, ...]
    rejected_indexes: tuple[int, ...]
    violations: tuple[SizePolicyViolation, ...]

    @property
    def has_rejections(self) -> bool:
        """Return whether at least one message failed the size policy."""

        return bool(self.rejected_indexes)


def size_policy_violation_error(
    violations: Sequence[SizePolicyViolation],
) -> SizePolicyViolationError:
    """Build one safe framework error for a rejected size-policy batch."""

    if not violations:
        return SizePolicyViolationError("size policy rejected message")
    first = violations[0]
    rejected_count = len({violation.index for violation in violations})
    return SizePolicyViolationError(
        f"size policy rejected {rejected_count} message(s); "
        f"first subject={first.subject!r} reason={first.reason} "
        f"actual={first.actual} limit={first.limit}"
    )


def evaluate_size_policy(
    envelopes: Sequence[NatsEnvelope],
    config: SizePolicyConfig,
) -> SizePolicyEvaluation:
    """Evaluate one already-normalized batch against the size policy.

    The function accepts `NatsEnvelope` objects because raw NATS messages are an
    external-client boundary.  By this point, headers and metadata have already
    been normalized into immutable framework structures, and payload encryption
    may already have transformed `data` into the exact bytes a sink would store.
    """

    if not config.enabled:
        return SizePolicyEvaluation(
            accepted_indexes=tuple(range(len(envelopes))),
            rejected_indexes=(),
            violations=(),
        )

    accepted: list[int] = []
    rejected: list[int] = []
    violations: list[SizePolicyViolation] = []

    batch_size = len(envelopes)
    batch_too_large = batch_size > config.max_batch_messages
    for index, envelope in enumerate(envelopes):
        envelope_violations = []
        if batch_too_large:
            envelope_violations.append(
                _violation(
                    index,
                    envelope,
                    "batch_message_count_too_large",
                    actual=batch_size,
                    limit=config.max_batch_messages,
                )
            )
        envelope_violations.extend(_violations_for_envelope(index, envelope, config))
        if envelope_violations:
            rejected.append(index)
            violations.extend(envelope_violations)
        else:
            accepted.append(index)

    return SizePolicyEvaluation(
        accepted_indexes=tuple(accepted),
        rejected_indexes=tuple(rejected),
        violations=tuple(violations),
    )


def _violations_for_envelope(
    index: int,
    envelope: NatsEnvelope,
    config: SizePolicyConfig,
) -> list[SizePolicyViolation]:
    """Return sanitized size violations for one envelope."""

    violations: list[SizePolicyViolation] = []

    _append_if_over(
        violations,
        index,
        envelope,
        "payload_too_large",
        actual=len(envelope.data),
        limit=config.max_payload_bytes,
    )
    _append_if_over(
        violations,
        index,
        envelope,
        "header_count_too_large",
        actual=len(envelope.headers),
        limit=config.max_header_count,
    )

    header_name_bytes, header_value_bytes, total_header_bytes = _header_sizes(envelope.headers)
    _append_if_over(
        violations,
        index,
        envelope,
        "header_name_too_large",
        actual=header_name_bytes,
        limit=config.max_header_name_bytes,
    )
    _append_if_over(
        violations,
        index,
        envelope,
        "header_value_too_large",
        actual=header_value_bytes,
        limit=config.max_header_value_bytes,
    )
    _append_if_over(
        violations,
        index,
        envelope,
        "headers_too_large",
        actual=total_header_bytes,
        limit=config.max_headers_bytes,
    )
    _append_if_over(
        violations,
        index,
        envelope,
        "label_count_too_large",
        actual=len(envelope.labels),
        limit=config.max_label_count,
    )

    label_bytes = max((len(label.encode("utf-8")) for label in envelope.labels), default=0)
    labels_total_bytes = sum(len(label.encode("utf-8")) for label in envelope.labels)
    _append_if_over(
        violations,
        index,
        envelope,
        "label_too_large",
        actual=label_bytes,
        limit=config.max_label_bytes,
    )
    _append_if_over(
        violations,
        index,
        envelope,
        "labels_too_large",
        actual=labels_total_bytes,
        limit=config.max_labels_bytes,
    )

    mission_metadata_size = _json_size(envelope.mission_metadata_for_json_storage())
    _append_if_over(
        violations,
        index,
        envelope,
        "mission_metadata_too_large",
        actual=mission_metadata_size,
        limit=config.max_mission_metadata_bytes,
    )

    standard_metadata_size = _json_size(envelope.metadata_for_json_storage())
    _append_if_over(
        violations,
        index,
        envelope,
        "standard_metadata_too_large",
        actual=standard_metadata_size,
        limit=config.max_standard_metadata_bytes,
    )

    normalized_record_size = len(envelope.data) + standard_metadata_size
    _append_if_over(
        violations,
        index,
        envelope,
        "normalized_record_too_large",
        actual=normalized_record_size,
        limit=config.max_normalized_record_bytes,
    )

    return violations


def _append_if_over(
    violations: list[SizePolicyViolation],
    index: int,
    envelope: NatsEnvelope,
    reason: str,
    *,
    actual: int,
    limit: int,
) -> None:
    """Append a violation when `actual` is greater than `limit`."""

    if actual > limit:
        violations.append(_violation(index, envelope, reason, actual=actual, limit=limit))


def _violation(
    index: int,
    envelope: NatsEnvelope,
    reason: str,
    *,
    actual: int,
    limit: int,
) -> SizePolicyViolation:
    """Create one sanitized size-policy violation."""

    return SizePolicyViolation(
        index=index,
        subject=envelope.subject,
        reason=reason,
        actual=actual,
        limit=limit,
    )


def _header_sizes(headers: Mapping[str, str]) -> tuple[int, int, int]:
    """Return maximum header-name bytes, maximum value bytes, and total bytes."""

    max_name = 0
    max_value = 0
    total = 0
    for name, value in headers.items():
        name_size = len(name.encode("utf-8"))
        value_size = len(value.encode("utf-8"))
        max_name = max(max_name, name_size)
        max_value = max(max_value, value_size)
        total += name_size + value_size
    return max_name, max_value, total


def _json_size(value: object) -> int:
    """Return compact UTF-8 JSON size for already-normalized framework data."""

    if value is None:
        return 0
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(rendered.encode("utf-8"))
