# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for issue #67 payload JSON strictness."""

from __future__ import annotations

import pytest

from nats_sinks import NatsEnvelope, SerializationError, normalize_payload_for_json_storage


def _envelope(data: bytes) -> NatsEnvelope:
    return NatsEnvelope(
        subject="sensor.events",
        data=data,
        headers={},
        stream="MISSION",
        consumer="test",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def test_bug_67_json_only_rejects_nonstandard_json_constants() -> None:
    """json_only mode should reject NaN rather than preserving invalid JSON."""

    with pytest.raises(SerializationError, match="valid JSON"):
        normalize_payload_for_json_storage(
            b'{"value":NaN}',
            subject="sensor.events",
            mode="json_only",
        )


def test_bug_67_json_or_envelope_wraps_nonstandard_json_constants_as_text() -> None:
    """Default mode should preserve invalid JSON text without pretending it is JSON."""

    normalized = normalize_payload_for_json_storage(
        b'{"value":NaN}',
        subject="sensor.events",
        mode="json_or_envelope",
    )

    assert normalized.original_format == "text"
    assert normalized.wrapped
    assert normalized.value["payload"] == '{"value":NaN}'


def test_bug_67_envelope_payload_as_json_rejects_nonstandard_constants() -> None:
    """NatsEnvelope.payload_as_json should reject Python JSON extensions."""

    with pytest.raises(SerializationError, match="valid JSON"):
        _envelope(b'{"value":Infinity}').payload_as_json()
