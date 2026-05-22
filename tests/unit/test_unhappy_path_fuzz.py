# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from nats_sinks.core.config import MessageMetadataConfig
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


class HeaderRawMessage:
    data = b"{}"
    metadata = None

    def __init__(self, headers: dict[str, str], *, subject: str = "orders.created") -> None:
        self.subject = subject
        self.headers = headers


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


def test_consumer_resolves_priority_and_classification_from_headers_and_defaults() -> None:
    config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "classification": {
                "header": "X-Classification",
                "default": "internal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default;orders",
            },
        }
    )

    from_headers = envelope_from_nats_message(
        HeaderRawMessage({"X-Priority": "critical"}),
        message_metadata=config,
    )
    from_defaults = envelope_from_nats_message(HeaderRawMessage({}), message_metadata=config)
    explicit_null = envelope_from_nats_message(
        HeaderRawMessage({"X-Classification": "  ", "X-Labels": " ; "}),
        message_metadata=config,
    )

    assert from_headers.priority == "critical"
    assert from_headers.classification == "internal"
    assert from_headers.labels == ("default", "orders")
    assert from_defaults.priority == "normal"
    assert from_defaults.classification == "internal"
    assert from_defaults.labels == ("default", "orders")
    assert explicit_null.priority == "normal"
    assert explicit_null.classification is None
    assert explicit_null.labels == ()


def test_consumer_resolves_subject_specific_message_metadata_defaults() -> None:
    config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "classification": {
                "header": "X-Classification",
                "default": "internal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default",
            },
            "rules": [
                {
                    "subject": "orders.urgent.>",
                    "priority": "urgent",
                    "classification": "restricted",
                    "labels": "urgent;customer-facing",
                },
                {
                    "subject": "public.>",
                    "priority": "low",
                    "classification": None,
                    "labels": None,
                },
            ],
        }
    )

    urgent = envelope_from_nats_message(
        HeaderRawMessage({}, subject="orders.urgent.created"),
        message_metadata=config,
    )
    public = envelope_from_nats_message(
        HeaderRawMessage({}, subject="public.status"),
        message_metadata=config,
    )
    fallback = envelope_from_nats_message(
        HeaderRawMessage({}, subject="orders.created"),
        message_metadata=config,
    )

    assert urgent.priority == "urgent"
    assert urgent.classification == "restricted"
    assert urgent.labels == ("urgent", "customer-facing")
    assert public.priority == "low"
    assert public.classification is None
    assert public.labels == ()
    assert fallback.priority == "normal"
    assert fallback.classification == "internal"
    assert fallback.labels == ("default",)


def test_consumer_headers_override_subject_specific_metadata_defaults() -> None:
    config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "classification": {
                "header": "X-Classification",
                "default": "internal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default",
            },
            "rules": [
                {
                    "subject": "orders.urgent.>",
                    "priority": "urgent",
                    "classification": "restricted",
                    "labels": "urgent;customer-facing",
                },
            ],
        }
    )

    from_headers = envelope_from_nats_message(
        HeaderRawMessage(
            {
                "X-Priority": "critical",
                "X-Classification": "top-secret",
                "X-Labels": "published;override",
            },
            subject="orders.urgent.created",
        ),
        message_metadata=config,
    )
    explicit_null = envelope_from_nats_message(
        HeaderRawMessage(
            {
                "X-Priority": " ",
                "X-Labels": " ",
            },
            subject="orders.urgent.created",
        ),
        message_metadata=config,
    )

    assert from_headers.priority == "critical"
    assert from_headers.classification == "top-secret"
    assert from_headers.labels == ("published", "override")
    assert explicit_null.priority is None
    assert explicit_null.classification == "restricted"
    assert explicit_null.labels == ()


def test_consumer_uses_first_matching_metadata_rule() -> None:
    config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default",
            },
            "rules": [
                {
                    "subject": "orders.>",
                    "priority": "broad",
                    "labels": "broad",
                },
                {
                    "subject": "orders.urgent.>",
                    "priority": "urgent",
                    "labels": "urgent",
                },
            ],
        }
    )

    item = envelope_from_nats_message(
        HeaderRawMessage({}, subject="orders.urgent.created"),
        message_metadata=config,
    )

    assert item.priority == "broad"
    assert item.labels == ("broad",)


def test_consumer_uses_standard_priority_and_classification_headers_by_default() -> None:
    item = envelope_from_nats_message(
        HeaderRawMessage(
            {
                "Nats-Sinks-Priority": "urgent",
                "Nats-Sinks-Classification": "restricted",
                "Nats-Sinks-Labels": "billing;customer-facing",
            }
        )
    )

    assert item.priority == "urgent"
    assert item.classification == "restricted"
    assert item.labels == ("billing", "customer-facing")


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
