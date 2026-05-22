# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nats_sinks.core.config import CustodyConfig
from nats_sinks.core.custody import (
    CUSTODY_SCHEMA,
    attach_custody_metadata,
    canonical_json_bytes,
    compute_custody_metadata,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ValidationError


def envelope(**overrides: object) -> NatsEnvelope:
    values = {
        "subject": "orders.created",
        "data": b'{"order_id":"O-1001","amount":42.5}',
        "headers": {"Nats-Msg-Id": "m-1", "Traceparent": "00-abcd"},
        "stream": "ORDERS",
        "consumer": "oracle",
        "stream_sequence": 42,
        "consumer_sequence": 7,
        "timestamp": datetime(2026, 5, 21, 9, 0, tzinfo=UTC),
        "message_id": None,
        "redelivered": False,
        "pending": 0,
        "priority": "urgent",
        "classification": "nato-restricted",
        "labels": ("sensor", "audit"),
        "received_at": datetime(2026, 5, 21, 9, 0, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return NatsEnvelope(**values)  # type: ignore[arg-type]


def test_canonical_json_bytes_are_stable_for_mapping_order() -> None:
    first = canonical_json_bytes({"b": [2, 1], "a": {"c": True}}, max_bytes=1024)
    second = canonical_json_bytes({"a": {"c": True}, "b": [2, 1]}, max_bytes=1024)

    assert first == b'{"a":{"c":true},"b":[2,1]}'
    assert first == second


def test_compute_custody_metadata_is_deterministic_for_same_envelope() -> None:
    config = CustodyConfig(enabled=True, algorithm="sha256", key_id="policy-v1")

    first = compute_custody_metadata(envelope(), config=config)
    second = compute_custody_metadata(envelope(), config=config)

    assert first == second
    assert first["schema"] == CUSTODY_SCHEMA
    assert first["algorithm"] == "sha256"
    assert first["key_id"] == "policy-v1"
    assert len(first["payload_hash"]) == 64
    assert len(first["metadata_hash"]) == 64
    assert len(first["record_hash"]) == 64
    assert first["privacy"] == "hashes_are_not_encryption"


def test_sha512_custody_hashes_are_supported() -> None:
    metadata = compute_custody_metadata(
        envelope(),
        config=CustodyConfig(enabled=True, algorithm="sha512"),
    )

    assert metadata["algorithm"] == "sha512"
    assert len(metadata["record_hash"]) == 128


def test_invalid_custody_algorithm_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"custody\.algorithm"):
        CustodyConfig(enabled=True, algorithm="md5")  # type: ignore[arg-type]


def test_previous_record_hash_is_optional_and_validated() -> None:
    previous = "a" * 64
    metadata = compute_custody_metadata(
        envelope(headers={"Nats-Sinks-Previous-Custody-Hash": previous}),
        config=CustodyConfig(enabled=True, include_previous_hash=True),
    )

    assert metadata["previous_record_hash"] == previous


def test_malformed_previous_record_hash_fails_closed() -> None:
    with pytest.raises(ValidationError, match="previous_record_hash"):
        compute_custody_metadata(
            envelope(headers={"Nats-Sinks-Previous-Custody-Hash": "not-a-digest"}),
            config=CustodyConfig(enabled=True, include_previous_hash=True),
        )


def test_oversized_custody_hash_input_fails_closed() -> None:
    with pytest.raises(ValidationError, match="max_hash_input_bytes"):
        compute_custody_metadata(
            envelope(data=b"x" * 2048),
            config=CustodyConfig(enabled=True, max_hash_input_bytes=1024),
        )


def test_attach_custody_metadata_is_noop_when_disabled() -> None:
    original = envelope()

    [result] = attach_custody_metadata([original], config=CustodyConfig())

    assert result is original
    assert result.custody is None


def test_attach_custody_metadata_freezes_result_for_sink_use() -> None:
    [result] = attach_custody_metadata([envelope()], config=CustodyConfig(enabled=True))

    assert result.custody is not None
    assert result.custody_for_json_storage() == result.custody_for_json_storage()
    with pytest.raises(TypeError):
        result.custody["record_hash"] = "changed"  # type: ignore[index]
