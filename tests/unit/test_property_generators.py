# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError, SerializationError, ValidationError
from nats_sinks.core.message_metadata import (
    labels_to_storage_string,
    normalise_labels_value,
    normalise_metadata_value,
    resolve_metadata_field,
    resolve_metadata_labels,
)
from nats_sinks.core.mission_metadata import (
    freeze_mission_metadata,
    normalize_mission_metadata_object,
    parse_mission_metadata_header,
    thaw_mission_metadata,
)
from nats_sinks.core.payload import normalize_payload_for_json_storage
from nats_sinks.core.subjects import matches_subject, validate_subject_pattern
from nats_sinks.file.config import FileSinkConfig
from nats_sinks.file.mapping import (
    MAX_COMPONENT_LENGTH,
    relative_path_for_envelope,
    safe_path_component,
)


class RaisesOnText:
    """Object used to prove normalizers fail closed for hostile string conversion."""

    def __str__(self) -> str:
        raise RuntimeError("synthetic string conversion failure")


def _bounded_subject_tokens() -> list[str]:
    """Return deterministic tokens that cover common subject-token shapes."""

    return ["orders", "sensor", "alpha-1", "route_2", "NATOSECRET"]


def _bounded_subjects() -> Iterable[str]:
    """Generate a small, deterministic subject corpus for matcher properties."""

    tokens = _bounded_subject_tokens()
    for first in tokens[:3]:
        yield first
    for first in tokens[:3]:
        for second in tokens[1:4]:
            yield f"{first}.{second}"
    for first in tokens[:2]:
        for second in tokens[1:3]:
            for third in tokens[2:4]:
                yield f"{first}.{second}.{third}"


def _hostile_path_values() -> list[object]:
    """Return path-like values that must never become raw filesystem paths."""

    return [
        "",
        ".",
        "..",
        "../../private/orders",
        "/absolute/path",
        "nested/../../escape",
        "mission synthetic sensor",
        "line\nbreak",
        "tab\tvalue",
        "ansi\x1b[31mred",
        "semi;colon",
        "unicode-Δ",
        "a" * 512,
        RaisesOnText(),
    ]


def _envelope_for_subject(subject: str, *, data: bytes = b"{}") -> NatsEnvelope:
    """Build the smallest envelope needed by file path mapping tests."""

    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={},
        stream="MISSION_SYNTHETIC",
        consumer="file-sink",
        stream_sequence=7,
        consumer_sequence=1,
        timestamp=None,
        message_id="message-7",
        redelivered=False,
        pending=0,
    )


def test_property_subject_patterns_match_nats_wildcard_invariants() -> None:
    """Bounded subject generation protects wildcard matching from broad matches."""

    subjects = list(_bounded_subjects())
    for subject in subjects:
        exact = validate_subject_pattern(subject)
        assert matches_subject(exact, subject)

        tokens = subject.split(".")
        for index in range(len(tokens)):
            pattern_tokens = list(tokens)
            pattern_tokens[index] = "*"
            pattern = validate_subject_pattern(".".join(pattern_tokens))
            assert matches_subject(pattern, subject)

            shorter_subject = ".".join(tokens[:-1])
            if shorter_subject:
                assert not matches_subject(pattern, shorter_subject)

        if len(tokens) > 1:
            tail_pattern = validate_subject_pattern(f"{tokens[0]}.>")
            assert matches_subject(tail_pattern, subject)
            assert not matches_subject(tail_pattern, tokens[0])


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        " orders.created",
        "orders.created ",
        "orders..created",
        ".orders",
        "orders.",
        "orders.>.created",
        "ord*ers.created",
        "orders.cre>ated",
        "orders.created\nnext",
        object(),
    ],
)
def test_property_subject_pattern_validator_rejects_ambiguous_input(candidate: object) -> None:
    """Invalid subject patterns fail fast as configuration errors."""

    with pytest.raises(ConfigurationError):
        validate_subject_pattern(candidate)


def test_property_payload_normalization_is_json_serializable_and_non_leaking() -> None:
    """Mixed payload bytes normalize into JSON values without echoing invalid payloads."""

    payloads = [
        b"",
        b"{}",
        b'{"order_id":"O-1001"}',
        b"[1,2,3]",
        b"42",
        b'"text"',
        b'{"broken":',
        b"plain text",
        b"unicode text",
        b"\xff\xfe\x00binary",
    ]

    for payload in payloads:
        normalized = normalize_payload_for_json_storage(payload, subject="mission.synthetic")
        rendered = json.dumps(normalized.value, ensure_ascii=False, allow_nan=False)

        assert rendered
        assert normalized.size_bytes == len(payload)
        assert normalized.sha256 == hashlib.sha256(payload).hexdigest()

    with pytest.raises(SerializationError) as exc_info:
        normalize_payload_for_json_storage(
            b'{"payload-fragment-that-must-not-leak":',
            subject="mission.synthetic",
            mode="json_only",
        )
    error_text = str(exc_info.value)
    assert "payload-fragment-that-must-not-leak" not in error_text
    assert "mission.synthetic" in error_text


def test_property_message_metadata_normalization_is_stable_and_bounded() -> None:
    """Metadata values and labels normalize deterministically for mixed input."""

    values: list[object | None] = [
        None,
        "",
        "   ",
        "urgent",
        " urgent ",
        42,
        True,
        RaisesOnText(),
    ]
    for value in values:
        normalized = normalise_metadata_value(value)
        assert normalized is None or (
            isinstance(normalized, str) and normalized == normalized.strip()
        )

    label_values: list[object | None] = [
        None,
        "",
        "alpha;beta;alpha;; ",
        ["alpha", " beta ", "", "alpha", 7],
        ("one", "two", "one"),
        {RaisesOnText(), "safe"},
    ]
    for value in label_values:
        labels = normalise_labels_value(value)
        assert len(labels) == len(set(labels))
        assert all(isinstance(label, str) and label == label.strip() and label for label in labels)
        rendered = labels_to_storage_string(labels)
        if labels:
            assert rendered == ";".join(labels)
        else:
            assert rendered is None

    assert (
        resolve_metadata_field(
            {"nats-sinks-priority": " immediate "},
            header_name="Nats-Sinks-Priority",
            default="routine",
        )
        == "immediate"
    )
    assert (
        resolve_metadata_field(
            {"Nats-Sinks-Priority": " "},
            header_name="Nats-Sinks-Priority",
            default="routine",
        )
        is None
    )
    assert resolve_metadata_labels(
        {},
        header_name="Nats-Sinks-Labels",
        default=["sensor", "sensor", "audit"],
    ) == ("sensor", "audit")


def test_property_mission_metadata_validation_freezes_safe_json_objects() -> None:
    """Validated mission metadata remains JSON-safe and immutable for sinks."""

    candidates: list[dict[str, Any]] = [
        {},
        {"profile": "sensor-event-custody", "profile_version": 1},
        {
            "profile": "sensor-event-custody",
            "profile_version": 1,
            "source": {
                "source_system": "synthetic-gateway",
                "confidence": 0.87,
                "labels": ["sensor", "audit"],
            },
        },
    ]

    for candidate in candidates:
        normalized = normalize_mission_metadata_object(
            candidate,
            allowed_profiles=() if not candidate else ("sensor-event-custody",),
        )
        frozen = freeze_mission_metadata(normalized)
        thawed = thaw_mission_metadata(frozen)

        assert thawed == normalized
        json.dumps(thawed, allow_nan=False)

    invalid_headers = [
        '{"profile":"sensor-event-custody","profile":"duplicate"}',
        '{"password":"synthetic"}',
        '{"profile":"sensor-event-custody","note":"line\\nbreak"}',
        "[]",
    ]
    for header in invalid_headers:
        with pytest.raises(ValidationError):
            parse_mission_metadata_header(
                header,
                max_bytes=512,
                allowed_profiles=("sensor-event-custody",),
            )

    with pytest.raises(ValidationError):
        normalize_mission_metadata_object(
            {"profile": "unexpected"},
            allowed_profiles=("sensor-event-custody",),
        )


def test_property_file_path_sanitizer_never_emits_traversal_components() -> None:
    """Hostile subjects and identifiers must stay within relative file paths."""

    config = FileSinkConfig(
        type="file",
        directory=Path(".local/property-tests"),
        filename_strategy="payload_sha256",
    )

    for raw_value in _hostile_path_values():
        component = safe_path_component(raw_value, fallback="subject")
        assert component
        assert component not in {".", ".."}
        assert "/" not in component
        assert "\\" not in component
        assert len(component) <= MAX_COMPONENT_LENGTH

        subject = component if isinstance(raw_value, RaisesOnText) else str(raw_value)
        relative_path = relative_path_for_envelope(_envelope_for_subject(subject), config=config)
        assert not relative_path.is_absolute()
        assert all(part not in {".", ".."} for part in relative_path.parts)
