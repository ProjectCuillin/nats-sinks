# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nats_sinks import NatsEnvelope, SerializationError, ValidationError
from nats_sinks.mysql.config import MySqlIdempotencyConfig
from nats_sinks.mysql.mapping import envelope_to_row


def envelope(**overrides: object) -> NatsEnvelope:
    values = {
        "subject": "orders.created",
        "data": b'{"order_id":"O-1001","amount":42.5}',
        "headers": {"Nats-Msg-Id": "m-1"},
        "stream": "ORDERS",
        "consumer": "mysql",
        "stream_sequence": 42,
        "consumer_sequence": 7,
        "timestamp": None,
        "message_id": None,
        "redelivered": False,
        "pending": 0,
        "priority": None,
        "classification": None,
        "labels": (),
        "custody": None,
        "received_at": datetime(2026, 5, 16, 10, 17, tzinfo=UTC),
    }
    values.update(overrides)
    return NatsEnvelope(**values)  # type: ignore[arg-type]


def test_envelope_to_row_maps_payload_headers_and_metadata() -> None:
    row = envelope_to_row(envelope(), idempotency=MySqlIdempotencyConfig())

    assert row["stream_name"] == "ORDERS"
    assert row["stream_sequence"] == 42
    assert row["message_id"] == "m-1"
    assert row["priority"] is None
    assert row["classification"] is None
    assert row["labels"] is None
    assert row["received_at_epoch_ns"] == 1778926620000000000
    assert json.loads(row["payload_json"])["order_id"] == "O-1001"
    assert json.loads(row["headers_json"])["Nats-Msg-Id"] == "m-1"
    metadata = json.loads(row["metadata_json"])
    assert metadata["message_id"] == "m-1"
    assert metadata["message_metadata"] == {
        "priority": None,
        "classification": None,
        "labels": [],
    }


def test_envelope_to_row_maps_priority_classification_labels_and_mission_metadata() -> None:
    row = envelope_to_row(
        envelope(
            priority="urgent",
            classification="NATO SECRET",
            labels=("sensor", "exercise"),
            mission_metadata={"profile": "mission-event-v1", "f2t2ea_phase": "track"},
        ),
        idempotency=MySqlIdempotencyConfig(),
    )

    assert row["priority"] == "urgent"
    assert row["classification"] == "NATO SECRET"
    assert row["labels"] == "sensor;exercise"
    assert json.loads(row["mission_metadata_json"])["f2t2ea_phase"] == "track"
    metadata = json.loads(row["metadata_json"])
    assert metadata["mission_metadata"]["profile"] == "mission-event-v1"


def test_payload_field_idempotency_uses_payload_value() -> None:
    row = envelope_to_row(
        envelope(headers={}),
        idempotency=MySqlIdempotencyConfig(strategy="payload_field", payload_field="order_id"),
    )

    assert row["message_id"] == "O-1001"


def test_non_json_text_payload_is_stored_as_json_envelope() -> None:
    row = envelope_to_row(
        envelope(data=b"encrypted-text:v1:ciphertext"),
        idempotency=MySqlIdempotencyConfig(),
    )

    payload = json.loads(row["payload_json"])
    assert payload["payload"] == "encrypted-text:v1:ciphertext"
    assert payload["_nats_sinks"]["payload_format"] == "text"


def test_empty_payload_is_stored_as_json_envelope() -> None:
    row = envelope_to_row(
        envelope(data=b""),
        idempotency=MySqlIdempotencyConfig(),
    )

    payload = json.loads(row["payload_json"])
    assert payload["payload"] == ""
    assert payload["_nats_sinks"]["size_bytes"] == 0


def test_json_only_payload_mode_rejects_non_json_text() -> None:
    with pytest.raises(SerializationError, match="not valid JSON"):
        envelope_to_row(
            envelope(data=b"encrypted-text:v1:ciphertext"),
            idempotency=MySqlIdempotencyConfig(),
            payload_mode="json_only",
        )


def test_stream_sequence_idempotency_requires_metadata() -> None:
    with pytest.raises(ValidationError, match="requires JetStream stream metadata"):
        envelope_to_row(
            envelope(stream=None, stream_sequence=None),
            idempotency=MySqlIdempotencyConfig(),
        )
