# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle.routing import matches_subject, validate_subject_pattern
from nats_sinks.oracle.sql import validate_identifier


class RaisesOnText:
    def __str__(self) -> str:
        raise RuntimeError("text rendering failed")


class RaisesOnBytes:
    def __bytes__(self) -> bytes:
        raise RuntimeError("bytes rendering failed")


class StrangeRawMessage:
    def __init__(self) -> None:
        self.subject = RaisesOnText()
        self.data = RaisesOnBytes()
        self.headers = {RaisesOnText(): "kept?", "bad-value": RaisesOnText()}

    @property
    def metadata(self) -> object:
        raise RuntimeError("metadata unavailable")


def test_envelope_normalization_tolerates_strange_message_objects() -> None:
    envelope = envelope_from_nats_message(StrangeRawMessage())

    assert envelope.subject == ""
    assert envelope.data == b""
    assert envelope.headers == {"bad-value": ""}
    assert envelope.stream_sequence is None
    assert envelope.consumer_sequence is None


def test_envelope_header_normalization_drops_unrenderable_keys_and_values() -> None:
    envelope = NatsEnvelope(
        subject="orders.created",
        data=b"{}",
        headers={
            RaisesOnText(): "drop-key",
            "drop-value": RaisesOnText(),
            "partial-list": [RaisesOnText(), "safe"],
        },
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )

    assert dict(envelope.headers) == {"partial-list": "safe"}


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        " ",
        "1EVENTS",
        "EVENTS;DROP",
        "EVENTS DROP",
        "EVENTS\nDROP",
        "EVENTS-DROP",
        "EVENTS/DROP",
        'EVENTS"DROP',
        "EVENTS'DROP",
        "APP..EVENTS",
        "A" * 129,
        object(),
    ],
)
def test_oracle_identifier_validation_rejects_fuzzed_bad_values(identifier: object) -> None:
    with pytest.raises(ConfigurationError):
        validate_identifier(identifier)  # type: ignore[arg-type]


def test_subject_pattern_validation_handles_fuzzed_values_without_unexpected_errors() -> None:
    alphabet = "abc.*> -_/;\n"
    candidates: list[object] = [
        "",
        " ",
        ".orders",
        "orders.",
        "orders..created",
        "orders.>.created",
        "orders.cre*ated",
        "orders.created",
        "orders.*",
        "orders.>",
        object(),
    ]
    candidates.extend(
        "".join(alphabet[(seed * 7 + index * 11) % len(alphabet)] for index in range(seed % 24))
        for seed in range(64)
    )

    for candidate in candidates:
        try:
            pattern = validate_subject_pattern(candidate)  # type: ignore[arg-type]
        except ConfigurationError:
            continue
        assert isinstance(pattern, str)
        assert isinstance(matches_subject(pattern, "orders.created"), bool)


def test_subject_matching_rejects_non_string_inputs_without_crashing() -> None:
    assert not matches_subject(object(), "orders.created")  # type: ignore[arg-type]
    assert not matches_subject("orders.*", object())  # type: ignore[arg-type]
