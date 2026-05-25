# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nats_sinks.core.config import PreSinkPolicyConfig
from nats_sinks.core.encryption import is_encrypted_payload_envelope
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.policy import evaluate_pre_sink_policy, policy_violation_error


def _envelope(
    *,
    subject: str = "orders.created",
    data: bytes = b"{}",
    priority: str | None = "routine",
    classification: str | None = "NATO UNCLASSIFIED",
    labels: tuple[str, ...] = ("orders",),
    mission_metadata: dict[str, object] | None = None,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={},
        stream="ORDERS",
        consumer="sink",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id="m-1",
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=labels,
        mission_metadata=mission_metadata,
    )


def test_disabled_policy_accepts_every_message() -> None:
    result = evaluate_pre_sink_policy([_envelope(priority=None)], PreSinkPolicyConfig())

    assert result.accepted_indexes == (0,)
    assert result.rejected_indexes == ()


def test_matching_rules_are_combined_as_fail_closed_checks() -> None:
    config = PreSinkPolicyConfig.model_validate(
        {
            "enabled": True,
            "rules": [
                {"subject": ">", "require_classification": True},
                {"subject": "orders.*", "required_labels": ["orders", "audit"]},
            ],
        }
    )

    result = evaluate_pre_sink_policy([_envelope(labels=("orders",))], config)

    assert result.accepted_indexes == ()
    assert result.rejected_indexes == (0,)
    assert [violation.reason for violation in result.violations] == ["required_label_missing"]


def test_policy_accepts_when_all_matching_rules_pass() -> None:
    config = PreSinkPolicyConfig.model_validate(
        {
            "enabled": True,
            "rules": [
                {
                    "subject": "orders.*",
                    "require_priority": True,
                    "require_classification": True,
                    "required_labels": "orders;audit",
                    "require_mission_metadata": True,
                    "allowed_mission_metadata_keys": ["profile", "phase"],
                    "max_payload_bytes": 16,
                }
            ],
        }
    )

    result = evaluate_pre_sink_policy(
        [
            _envelope(
                labels=("orders", "audit"),
                mission_metadata={"profile": "mission", "phase": "find"},
            )
        ],
        config,
    )

    assert result.accepted_indexes == (0,)
    assert result.rejected_indexes == ()


def test_unmatched_subject_rejects_by_default() -> None:
    config = PreSinkPolicyConfig.model_validate(
        {"enabled": True, "rules": [{"subject": "orders.*", "require_priority": True}]}
    )

    result = evaluate_pre_sink_policy([_envelope(subject="alerts.created")], config)

    assert result.rejected_indexes == (0,)
    assert result.violations[0].reason == "subject_not_covered_by_policy"


def test_unmatched_subject_can_be_explicitly_allowed() -> None:
    config = PreSinkPolicyConfig.model_validate(
        {
            "enabled": True,
            "unmatched_subject_action": "allow",
            "rules": [{"subject": "orders.*", "require_priority": True}],
        }
    )

    result = evaluate_pre_sink_policy([_envelope(subject="alerts.created")], config)

    assert result.accepted_indexes == (0,)


def test_policy_detects_oversized_payload_and_disallowed_mission_key() -> None:
    config = PreSinkPolicyConfig.model_validate(
        {
            "enabled": True,
            "rules": [
                {
                    "subject": ">",
                    "max_payload_bytes": 2,
                    "allowed_mission_metadata_keys": ["profile"],
                }
            ],
        }
    )

    result = evaluate_pre_sink_policy(
        [_envelope(data=b"too-large", mission_metadata={"profile": "x", "phase": "target"})],
        config,
    )

    assert result.rejected_indexes == (0,)
    assert {violation.reason for violation in result.violations} == {
        "payload_too_large",
        "mission_metadata_key_not_allowed",
    }


def test_encrypted_payload_requirement_uses_standard_envelope_shape() -> None:
    encrypted = (
        b'{"_nats_sinks_encryption":{"algorithm":"aes-256-gcm",'
        b'"ciphertext":"AAAA","key_id":"k1","schema":"nats_sinks.encrypted_payload.v1",'
        b'"version":1}}'
    )
    config = PreSinkPolicyConfig.model_validate(
        {"enabled": True, "rules": [{"subject": ">", "require_encrypted_payload": True}]}
    )

    accepted = evaluate_pre_sink_policy([_envelope(data=encrypted)], config)
    rejected = evaluate_pre_sink_policy([_envelope(data=b"not encrypted")], config)

    assert is_encrypted_payload_envelope(encrypted)
    assert accepted.accepted_indexes == (0,)
    assert rejected.rejected_indexes == (0,)
    assert rejected.violations[0].reason == "encrypted_payload_required"


def test_policy_error_message_is_sanitized() -> None:
    violation_result = evaluate_pre_sink_policy(
        [_envelope(classification=None)],
        PreSinkPolicyConfig.model_validate(
            {"enabled": True, "rules": [{"subject": "orders.*", "require_classification": True}]}
        ),
    )

    message = str(policy_violation_error(violation_result.violations))

    assert "classification_required" in message
    assert "Nats-Msg-Id" not in message
    assert "{}" not in message
