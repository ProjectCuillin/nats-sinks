# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks import NatsEnvelope
from nats_sinks.core.config import (
    MessageMetadataFieldConfig,
    MessageMetadataLabelsConfig,
    MessageMetadataRuleConfig,
    MissionMetadataConfig,
    SecurityLabelProfileConfig,
)
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.metadata import datetime_to_epoch_ns
from nats_sinks.core.security_labels import (
    SECURITY_LABEL_PROFILE_NAME,
    normalize_security_label_profile,
)


def _base_envelope(**overrides: object) -> NatsEnvelope:
    """Return a minimal envelope for trust-boundary header regression tests."""

    values = {
        "subject": "mission.track.update",
        "data": b"{}",
        "headers": {},
        "stream": "EVENTS",
        "consumer": "sink",
        "stream_sequence": 1,
        "consumer_sequence": 1,
        "timestamp": None,
        "message_id": None,
        "redelivered": False,
        "pending": 0,
    }
    values.update(overrides)
    return NatsEnvelope(**values)  # type: ignore[arg-type]


def test_priority_metadata_header_rejects_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataFieldConfig(header="Priority\x00Header")


def test_labels_metadata_header_rejects_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataLabelsConfig(header="Labels\x00Header")


def test_priority_metadata_default_rejects_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataFieldConfig(header="Priority", default="routine\x00value")


def test_rule_classification_default_rejects_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataRuleConfig(
            subject="mission.>",
            classification="NATO SECRET\x00",
        )


def test_label_defaults_reject_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataLabelsConfig(header="Labels", default=["ops\x00label"])

    with pytest.raises(PydanticValidationError, match="control characters"):
        MessageMetadataRuleConfig(subject="mission.>", labels=["ops\tlabel"])


def test_configured_label_array_items_reject_semicolon_separator() -> None:
    with pytest.raises(PydanticValidationError, match="semicolon"):
        MessageMetadataLabelsConfig(header="Labels", default=["ops;mission"])

    assert MessageMetadataLabelsConfig(header="Labels", default="ops;mission").default == (
        "ops",
        "mission",
    )


def test_envelope_drops_empty_header_names() -> None:
    envelope = _base_envelope(headers={"   ": "discarded", "X-Kept": "value"})

    assert "" not in envelope.headers
    assert "   " not in envelope.headers
    assert "X-Kept" in envelope.headers


def test_envelope_drops_control_character_header_names() -> None:
    envelope = _base_envelope(headers={"Bad\x00Header": "discarded", "X-Kept": "value"})

    assert "Bad\x00Header" not in envelope.headers
    assert envelope.headers["X-Kept"] == "value"


def test_security_label_scalar_fields_reject_non_string_values() -> None:
    with pytest.raises(ValidationError, match="classification"):
        normalize_security_label_profile(
            {
                "profile": SECURITY_LABEL_PROFILE_NAME,
                "classification": 123,
            }
        )


def test_security_label_list_fields_reject_non_string_items() -> None:
    with pytest.raises(ValidationError, match="releasability"):
        normalize_security_label_profile(
            {
                "profile": SECURITY_LABEL_PROFILE_NAME,
                "releasability": [42],
            }
        )


def test_security_label_list_items_reject_semicolon_separator() -> None:
    with pytest.raises(ValidationError, match="semicolon"):
        normalize_security_label_profile(
            {
                "profile": SECURITY_LABEL_PROFILE_NAME,
                "releasability": ["NATO;FVEY"],
            }
        )

    profile = normalize_security_label_profile(
        {
            "profile": SECURITY_LABEL_PROFILE_NAME,
            "releasability": "NATO;FVEY",
        }
    )

    assert profile["releasability"] == ["NATO", "FVEY"]


def test_security_label_allow_lists_reject_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        SecurityLabelProfileConfig(allowed_classifications=["NATO\tSECRET"])


def test_mission_metadata_allowed_profiles_reject_control_characters() -> None:
    with pytest.raises(PydanticValidationError, match="control characters"):
        MissionMetadataConfig(allowed_profiles=["mission\tprofile"])


def test_datetime_to_epoch_ns_uses_exact_integer_arithmetic() -> None:
    value = datetime(2500, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    expected = (delta.days * 86_400 + delta.seconds) * 1_000_000_000
    expected += delta.microseconds * 1_000

    assert datetime_to_epoch_ns(value) == expected
