# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json

import pytest

from nats_sinks import NatsEnvelope, SerializationError, normalize_payload_for_json_storage


def envelope(data: bytes) -> NatsEnvelope:
    return NatsEnvelope(
        subject="secure.events",
        data=data,
        headers={},
        stream="SECURE",
        consumer="oracle",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def test_valid_json_payload_is_preserved_for_json_storage() -> None:
    normalized = normalize_payload_for_json_storage(
        b'{"order_id":"O-1001","amount":42.5}',
        subject="orders.created",
    )

    assert normalized.original_format == "json"
    assert not normalized.wrapped
    assert normalized.value == {"order_id": "O-1001", "amount": 42.5}


def test_non_json_text_payload_is_wrapped_in_json_envelope() -> None:
    normalized = normalize_payload_for_json_storage(
        b"encrypted-text:v1:sample-ciphertext",
        subject="secure.events",
    )

    assert normalized.original_format == "text"
    assert normalized.wrapped
    assert normalized.value["payload"] == "encrypted-text:v1:sample-ciphertext"
    assert normalized.value["_nats_sinks"]["payload_format"] == "text"
    assert normalized.value["_nats_sinks"]["payload_encoding"] == "utf-8"
    assert normalized.value["_nats_sinks"]["size_bytes"] == len(
        b"encrypted-text:v1:sample-ciphertext"
    )


def test_empty_payload_is_wrapped_without_crashing() -> None:
    normalized = normalize_payload_for_json_storage(b"", subject="empty.events")

    assert normalized.original_format == "text"
    assert normalized.wrapped
    assert normalized.value["payload"] == ""
    assert normalized.value["_nats_sinks"]["size_bytes"] == 0


def test_malformed_json_looking_text_is_preserved_as_text() -> None:
    normalized = normalize_payload_for_json_storage(b'{"order_id":', subject="orders.bad")

    assert normalized.original_format == "text"
    assert normalized.value["payload"] == '{"order_id":'


def test_binary_payload_is_wrapped_as_base64_in_json_envelope() -> None:
    data = b"\xff\x00\xfe"
    normalized = normalize_payload_for_json_storage(data, subject="binary.events")

    assert normalized.original_format == "bytes"
    assert normalized.wrapped
    assert normalized.value["payload"] == base64.b64encode(data).decode("ascii")
    assert normalized.value["_nats_sinks"]["payload_format"] == "bytes"
    assert normalized.value["_nats_sinks"]["payload_encoding"] == "base64"


def test_json_only_mode_rejects_non_json_text_without_logging_payload() -> None:
    with pytest.raises(SerializationError) as exc_info:
        normalize_payload_for_json_storage(
            b"encrypted-text:v1:sample-ciphertext",
            subject="secure.events",
            mode="json_only",
        )

    assert "secure.events" in str(exc_info.value)
    assert "encrypted-text" not in str(exc_info.value)


def test_text_envelope_mode_wraps_valid_json_without_parsing() -> None:
    normalized = normalize_payload_for_json_storage(
        b'{"encrypted":"maybe"}',
        subject="secure.events",
        mode="text_envelope",
    )

    assert normalized.original_format == "text"
    assert normalized.value["payload"] == '{"encrypted":"maybe"}'


def test_bytes_envelope_mode_wraps_everything_as_base64() -> None:
    normalized = normalize_payload_for_json_storage(
        b'{"encrypted":"maybe"}',
        subject="secure.events",
        mode="bytes_envelope",
    )

    assert normalized.original_format == "bytes"
    assert json.loads(base64.b64decode(normalized.value["payload"]).decode("utf-8")) == {
        "encrypted": "maybe"
    }


def test_envelope_exposes_shared_payload_storage_helper() -> None:
    normalized = envelope(b"encrypted-text:v1:sample-ciphertext").payload_for_json_storage()

    assert normalized.value["_nats_sinks"]["payload_format"] == "text"
