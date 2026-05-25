# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nats_sinks.core.config import SizePolicyConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.size_policy import evaluate_size_policy, size_policy_violation_error


def _envelope(
    *,
    data: bytes = b"{}",
    headers: dict[str, str] | None = None,
    labels: tuple[str, ...] = (),
    mission_metadata: dict[str, object] | None = None,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="orders.created",
        data=data,
        headers=headers or {},
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id="m-1",
        redelivered=False,
        pending=0,
        labels=labels,
        mission_metadata=mission_metadata,
    )


def _single_reason(config: SizePolicyConfig, envelope: NatsEnvelope) -> str:
    result = evaluate_size_policy([envelope], config)
    assert result.rejected_indexes == (0,)
    return result.violations[0].reason


def test_disabled_size_policy_allows_messages_without_evaluation_rejections() -> None:
    result = evaluate_size_policy(
        [_envelope(data=b"x" * 1024)],
        SizePolicyConfig(enabled=False, max_payload_bytes=1),
    )

    assert result.accepted_indexes == (0,)
    assert result.rejected_indexes == ()
    assert result.violations == ()


def test_size_policy_rejects_payload_bytes_over_limit() -> None:
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_payload_bytes=3),
            _envelope(data=b"abcd"),
        )
        == "payload_too_large"
    )


def test_size_policy_rejects_header_count_and_sizes() -> None:
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_header_count=1),
            _envelope(headers={"a": "1", "b": "2"}),
        )
        == "header_count_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_header_name_bytes=2),
            _envelope(headers={"long": "1"}),
        )
        == "header_name_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_header_value_bytes=2),
            _envelope(headers={"x": "long"}),
        )
        == "header_value_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(
                enabled=True,
                max_header_name_bytes=1,
                max_header_value_bytes=1,
                max_headers_bytes=3,
            ),
            _envelope(headers={"a": "1", "b": "2"}),
        )
        == "headers_too_large"
    )


def test_size_policy_rejects_label_count_and_sizes() -> None:
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_label_count=1),
            _envelope(labels=("one", "two")),
        )
        == "label_count_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_label_bytes=3),
            _envelope(labels=("long",)),
        )
        == "label_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_label_bytes=3, max_labels_bytes=6),
            _envelope(labels=("one", "two", "six")),
        )
        == "labels_too_large"
    )


def test_size_policy_rejects_mission_metadata_standard_metadata_and_record_size() -> None:
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_mission_metadata_bytes=4),
            _envelope(mission_metadata={"profile": "standard"}),
        )
        == "mission_metadata_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(enabled=True, max_standard_metadata_bytes=10),
            _envelope(headers={"x": "y"}),
        )
        == "standard_metadata_too_large"
    )
    assert (
        _single_reason(
            SizePolicyConfig(
                enabled=True,
                max_payload_bytes=2,
                max_normalized_record_bytes=2,
            ),
            _envelope(data=b"{}"),
        )
        == "normalized_record_too_large"
    )


def test_size_policy_rejects_batches_over_message_count_limit() -> None:
    result = evaluate_size_policy(
        [_envelope(), _envelope()],
        SizePolicyConfig(enabled=True, max_batch_messages=1),
    )

    assert result.rejected_indexes == (0, 1)
    assert {violation.reason for violation in result.violations} == {
        "batch_message_count_too_large"
    }


def test_size_policy_violation_error_is_sanitized() -> None:
    envelope = _envelope(data=b"secret-payload", headers={"Authorization": "secret-token"})
    result = evaluate_size_policy(
        [envelope],
        SizePolicyConfig(enabled=True, max_payload_bytes=1),
    )

    rendered = str(size_policy_violation_error(result.violations))

    assert "payload_too_large" in rendered
    assert "actual=" in rendered
    assert "limit=" in rendered
    assert "secret-payload" not in rendered
    assert "secret-token" not in rendered
    assert "Authorization" not in rendered
