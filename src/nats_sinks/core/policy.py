# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pre-sink policy enforcement.

The policy gate is a small, destination-neutral safety layer between core
normalization and sink writes. It exists for deployments that need a final
fail-closed check before data becomes durable in Oracle, local files, object
storage, or a future backend.

The design is intentionally conservative. Policies are ordinary validated JSON
configuration; they do not contain Python code, imports, regular expressions,
templates, or expression strings. Every rule is selected from an explicit
allow list and evaluated against the immutable `NatsEnvelope` already produced
by the core runtime. Violations are permanent framework errors and therefore
use the same DLQ-before-ACK behavior as malformed message metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nats_sinks.core.encryption import is_encrypted_payload_envelope
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PolicyViolationError
from nats_sinks.core.subjects import matches_subject

if TYPE_CHECKING:
    from nats_sinks.core.config import PreSinkPolicyConfig, PreSinkPolicyRuleConfig


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """One sanitized policy violation.

    The reason string is a stable, non-sensitive code. It intentionally avoids
    copying payload values, header values, mission metadata values, or unknown
    field names into logs or DLQ error text.
    """

    index: int
    subject: str
    rule_subject: str
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Result of evaluating one batch against the pre-sink policy."""

    accepted_indexes: tuple[int, ...]
    rejected_indexes: tuple[int, ...]
    violations: tuple[PolicyViolation, ...]

    @property
    def has_rejections(self) -> bool:
        """Return whether at least one message failed policy checks."""

        return bool(self.rejected_indexes)


def policy_violation_error(violations: Sequence[PolicyViolation]) -> PolicyViolationError:
    """Build one safe framework error for a rejected policy batch."""

    if not violations:
        return PolicyViolationError("pre-sink policy rejected message")
    first = violations[0]
    rejected_count = len({violation.index for violation in violations})
    return PolicyViolationError(
        f"pre-sink policy rejected {rejected_count} message(s); "
        f"first subject={first.subject!r} rule={first.rule_subject!r} reason={first.reason}"
    )


def evaluate_pre_sink_policy(
    envelopes: Sequence[NatsEnvelope],
    config: PreSinkPolicyConfig,
) -> PolicyEvaluation:
    """Evaluate a batch and return accepted and rejected message indexes.

    All matching rules are applied to a message. This lets operators combine a
    broad global policy such as `subject=">"` with narrower subject-specific
    checks. If the gate is enabled and no rule matches, the default behavior is
    to reject the message unless the operator explicitly sets
    `unmatched_subject_action` to `allow`.
    """

    if not config.enabled:
        return PolicyEvaluation(
            accepted_indexes=tuple(range(len(envelopes))),
            rejected_indexes=(),
            violations=(),
        )

    accepted: list[int] = []
    rejected: list[int] = []
    violations: list[PolicyViolation] = []

    for index, envelope in enumerate(envelopes):
        envelope_violations = _violations_for_envelope(index, envelope, config)
        if envelope_violations:
            rejected.append(index)
            violations.extend(envelope_violations)
        else:
            accepted.append(index)

    return PolicyEvaluation(
        accepted_indexes=tuple(accepted),
        rejected_indexes=tuple(rejected),
        violations=tuple(violations),
    )


def _violations_for_envelope(
    index: int,
    envelope: NatsEnvelope,
    config: PreSinkPolicyConfig,
) -> list[PolicyViolation]:
    """Return sanitized violations for one envelope."""

    matching_rules = [
        rule for rule in config.rules if matches_subject(rule.subject, envelope.subject)
    ]
    if not matching_rules:
        if config.unmatched_subject_action == "reject":
            return [
                PolicyViolation(
                    index=index,
                    subject=envelope.subject,
                    rule_subject="<unmatched>",
                    reason="subject_not_covered_by_policy",
                )
            ]
        return []

    violations: list[PolicyViolation] = []
    for rule in matching_rules:
        violations.extend(_violations_for_rule(index, envelope, rule))
    return violations


def _violations_for_rule(
    index: int,
    envelope: NatsEnvelope,
    rule: PreSinkPolicyRuleConfig,
) -> list[PolicyViolation]:
    """Evaluate one allow-listed rule against one envelope."""

    violations: list[PolicyViolation] = []

    if rule.require_priority and envelope.priority is None:
        violations.append(_violation(index, envelope, rule, "priority_required"))

    if rule.require_classification and envelope.classification is None:
        violations.append(_violation(index, envelope, rule, "classification_required"))

    if rule.required_labels:
        present_labels = set(envelope.labels)
        if not set(rule.required_labels).issubset(present_labels):
            violations.append(_violation(index, envelope, rule, "required_label_missing"))

    if rule.require_mission_metadata and envelope.mission_metadata is None:
        violations.append(_violation(index, envelope, rule, "mission_metadata_required"))

    if rule.require_encrypted_payload and not is_encrypted_payload_envelope(envelope.data):
        violations.append(_violation(index, envelope, rule, "encrypted_payload_required"))

    if rule.max_payload_bytes is not None and len(envelope.data) > rule.max_payload_bytes:
        violations.append(_violation(index, envelope, rule, "payload_too_large"))

    if rule.allowed_mission_metadata_keys is not None and envelope.mission_metadata is not None:
        allowed_keys = set(rule.allowed_mission_metadata_keys)
        if any(key not in allowed_keys for key in envelope.mission_metadata):
            violations.append(_violation(index, envelope, rule, "mission_metadata_key_not_allowed"))

    return violations


def _violation(
    index: int,
    envelope: NatsEnvelope,
    rule: PreSinkPolicyRuleConfig,
    reason: str,
) -> PolicyViolation:
    """Create one sanitized violation record."""

    return PolicyViolation(
        index=index,
        subject=envelope.subject,
        rule_subject=rule.subject,
        reason=reason,
    )
