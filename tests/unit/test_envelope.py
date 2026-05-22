# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nats_sinks import NatsEnvelope, SerializationError


def envelope(**overrides: object) -> NatsEnvelope:
    values = {
        "subject": "orders.created",
        "data": b'{"order_id":"O-1001"}',
        "headers": {"Nats-Msg-Id": "m-1"},
        "stream": "ORDERS",
        "consumer": "oracle",
        "stream_sequence": 42,
        "consumer_sequence": 7,
        "timestamp": None,
        "message_id": None,
        "redelivered": False,
        "pending": 0,
    }
    values.update(overrides)
    return NatsEnvelope(**values)  # type: ignore[arg-type]


def test_envelope_is_immutable_and_normalizes_headers() -> None:
    item = envelope(headers={"Nats-Msg-Id": ["m-1"]})

    assert item.message_id == "m-1"
    with pytest.raises(TypeError):
        item.headers["x"] = "y"  # type: ignore[index]


def test_envelope_normalizes_message_metadata_fields() -> None:
    item = envelope(
        priority=" urgent ", classification="restricted", labels=("billing", " urgent ")
    )

    assert item.priority == "urgent"
    assert item.classification == "restricted"
    assert item.labels == ("billing", "urgent")


def test_envelope_normalizes_semicolon_labels_and_removes_duplicates() -> None:
    item = envelope(labels="alpha; beta ;alpha;;gamma")

    assert item.labels == ("alpha", "beta", "gamma")


def test_envelope_empty_message_metadata_is_null() -> None:
    item = envelope(
        headers={
            "Nats-Sinks-Priority": "   ",
            "Nats-Sinks-Classification": "",
        },
        priority="",
        classification=" ",
    )

    assert item.priority is None
    assert item.classification is None


def test_idempotency_key_prefers_stream_sequence() -> None:
    assert envelope().idempotency_key() == "stream-sequence:ORDERS:42"


def test_idempotency_key_falls_back_to_message_id() -> None:
    item = envelope(stream=None, stream_sequence=None)
    assert item.idempotency_key() == "message-id:m-1"


def test_payload_as_json_reports_clear_error() -> None:
    item = envelope(data=b"not-json")
    with pytest.raises(SerializationError, match="not valid JSON"):
        item.payload_as_json()


def test_payload_as_text_reports_clear_error() -> None:
    item = envelope(data=b"\xff")
    with pytest.raises(SerializationError, match="not valid utf-8"):
        item.payload_as_text()


def test_metadata_snapshot_captures_optional_nats_headers_and_epoch_times() -> None:
    item = envelope(
        headers={
            "Nats-Msg-Id": "m-1",
            "Nats-Expected-Stream": "ORDERS",
            "Nats-Time-Stamp": "2026-05-16T10:15:30Z",
            "X-App": "kept",
        },
        timestamp=datetime(2026, 5, 16, 10, 16, tzinfo=UTC),
        received_at=datetime(2026, 5, 16, 10, 17, tzinfo=UTC),
        domain="",
        reply="_INBOX.reply",
    )

    metadata = item.metadata_for_json_storage(stored_at=datetime(2026, 5, 16, 10, 18, tzinfo=UTC))

    assert metadata["headers"]["X-App"] == "kept"
    assert metadata["message_metadata"] == {
        "priority": None,
        "classification": None,
        "labels": [],
    }
    assert metadata["nats"]["reserved_headers"]["Nats-Msg-Id"] == "m-1"
    assert metadata["nats"]["reserved_headers"]["Nats-Expected-Stream"] == "ORDERS"
    assert metadata["jetstream"]["stream_sequence"] == 42
    assert metadata["timestamps"]["message_created_at_epoch_ns"] == 1778926530000000000
    assert metadata["timestamps"]["received_at_epoch_ns"] == 1778926620000000000
    assert metadata["timestamps"]["stored_at_epoch_ns"] == 1778926680000000000


def test_metadata_snapshot_handles_missing_reserved_headers() -> None:
    metadata = envelope(headers={}).metadata_for_json_storage()

    assert metadata["message_id"] is None
    assert metadata["message_metadata"]["priority"] is None
    assert metadata["message_metadata"]["classification"] is None
    assert metadata["message_metadata"]["labels"] == []
    assert metadata["nats"]["reserved_headers"] == {}
