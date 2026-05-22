# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nats_sinks import NatsEnvelope, SerializationError, ValidationError
from nats_sinks.oracle.config import OracleIdempotencyConfig
from nats_sinks.oracle.mapping import envelope_to_row


def envelope(**overrides: object) -> NatsEnvelope:
    values = {
        "subject": "orders.created",
        "data": b'{"order_id":"O-1001","amount":42.5}',
        "headers": {"Nats-Msg-Id": "m-1"},
        "stream": "ORDERS",
        "consumer": "oracle",
        "stream_sequence": 42,
        "consumer_sequence": 7,
        "timestamp": None,
        "message_id": None,
        "redelivered": False,
        "pending": 0,
        "priority": None,
        "classification": None,
        "labels": (),
        "received_at": datetime(2026, 5, 16, 10, 17, tzinfo=UTC),
    }
    values.update(overrides)
    return NatsEnvelope(**values)  # type: ignore[arg-type]


def test_envelope_to_row_maps_payload_and_headers() -> None:
    row = envelope_to_row(envelope(), idempotency=OracleIdempotencyConfig())

    assert row["stream_name"] == "ORDERS"
    assert row["stream_sequence"] == 42
    assert row["message_id"] == "m-1"
    assert row["priority"] is None
    assert row["classification"] is None
    assert row["labels"] is None
    assert row["received_at_epoch_ns"] == 1778926620000000000
    assert row["stored_at_epoch_ns"] is not None
    assert json.loads(row["payload_json"])["order_id"] == "O-1001"
    assert json.loads(row["headers_json"])["Nats-Msg-Id"] == "m-1"
    assert json.loads(row["mission_metadata_json"]) is None
    metadata = json.loads(row["metadata_json"])
    assert metadata["nats"]["reserved_headers"]["Nats-Msg-Id"] == "m-1"
    assert metadata["message_metadata"] == {
        "priority": None,
        "classification": None,
        "labels": [],
    }
    assert metadata["mission_metadata"] is None


def test_envelope_to_row_maps_priority_classification_and_labels() -> None:
    row = envelope_to_row(
        envelope(priority="urgent", classification="restricted", labels=("billing", "urgent")),
        idempotency=OracleIdempotencyConfig(),
    )

    assert row["priority"] == "urgent"
    assert row["classification"] == "restricted"
    assert row["labels"] == "billing;urgent"
    metadata = json.loads(row["metadata_json"])
    assert metadata["message_metadata"] == {
        "priority": "urgent",
        "classification": "restricted",
        "labels": ["billing", "urgent"],
    }


def test_envelope_to_row_maps_mission_metadata_json_column() -> None:
    row = envelope_to_row(
        envelope(
            mission_metadata={
                "profile": "mission-event-v1",
                "mission_id": "M-1001",
                "f2t2ea_phase": "track",
            }
        ),
        idempotency=OracleIdempotencyConfig(),
    )

    assert json.loads(row["mission_metadata_json"]) == {
        "profile": "mission-event-v1",
        "mission_id": "M-1001",
        "f2t2ea_phase": "track",
    }
    metadata = json.loads(row["metadata_json"])
    assert metadata["mission_metadata"]["mission_id"] == "M-1001"


def test_payload_field_idempotency_uses_payload_value() -> None:
    row = envelope_to_row(
        envelope(headers={}),
        idempotency=OracleIdempotencyConfig(strategy="payload_field", payload_field="order_id"),
    )

    assert row["message_id"] == "O-1001"


def test_missing_nats_message_id_and_expected_headers_do_not_crash() -> None:
    row = envelope_to_row(
        envelope(headers={}),
        idempotency=OracleIdempotencyConfig(),
    )

    assert row["message_id"] is None
    metadata = json.loads(row["metadata_json"])
    assert metadata["message_id"] is None
    assert metadata["nats"]["reserved_headers"] == {}


def test_reserved_expected_stream_header_is_persisted_when_present() -> None:
    row = envelope_to_row(
        envelope(headers={"Nats-Expected-Stream": "ORDERS"}),
        idempotency=OracleIdempotencyConfig(),
    )

    metadata = json.loads(row["metadata_json"])
    assert metadata["nats"]["reserved_headers"]["Nats-Expected-Stream"] == "ORDERS"


def test_non_json_text_payload_is_stored_as_json_envelope() -> None:
    row = envelope_to_row(
        envelope(data=b"encrypted-text:v1:ciphertext"),
        idempotency=OracleIdempotencyConfig(),
    )

    payload = json.loads(row["payload_json"])
    assert payload["payload"] == "encrypted-text:v1:ciphertext"
    assert payload["_nats_sinks"]["payload_format"] == "text"
    assert payload["_nats_sinks"]["payload_encoding"] == "utf-8"


def test_empty_payload_is_stored_as_json_envelope() -> None:
    row = envelope_to_row(
        envelope(data=b""),
        idempotency=OracleIdempotencyConfig(),
    )

    payload = json.loads(row["payload_json"])
    assert payload["payload"] == ""
    assert payload["_nats_sinks"]["payload_format"] == "text"
    assert payload["_nats_sinks"]["size_bytes"] == 0


def test_json_only_payload_mode_rejects_non_json_text() -> None:
    with pytest.raises(SerializationError, match="not valid JSON"):
        envelope_to_row(
            envelope(data=b"encrypted-text:v1:ciphertext"),
            idempotency=OracleIdempotencyConfig(),
            payload_mode="json_only",
        )


def test_text_envelope_payload_mode_wraps_json_without_parsing() -> None:
    row = envelope_to_row(
        envelope(data=b'{"encrypted":"maybe-json"}'),
        idempotency=OracleIdempotencyConfig(),
        payload_mode="text_envelope",
    )

    payload = json.loads(row["payload_json"])
    assert payload["payload"] == '{"encrypted":"maybe-json"}'
    assert payload["_nats_sinks"]["payload_format"] == "text"


def test_stream_sequence_idempotency_requires_metadata() -> None:
    with pytest.raises(ValidationError, match="requires JetStream stream metadata"):
        envelope_to_row(
            envelope(stream=None, stream_sequence=None),
            idempotency=OracleIdempotencyConfig(),
        )
