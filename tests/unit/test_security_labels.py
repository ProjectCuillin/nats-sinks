# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.config import SecurityLabelProfileConfig, load_config
from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.security_labels import (
    DEFAULT_SECURITY_LABELS_HEADER,
    SECURITY_LABEL_PROFILE_NAME,
    parse_security_label_header,
)
from nats_sinks.file.config import FileSinkConfig
from nats_sinks.file.mapping import file_record_for_envelope
from nats_sinks.oracle.config import OracleIdempotencyConfig
from nats_sinks.oracle.mapping import envelope_to_row


class HeaderRawMessage:
    """Minimal message-like object used to test core security label normalization."""

    data = b'{"event":"created"}'
    metadata = None

    def __init__(self, headers: dict[str, str], *, subject: str = "mission.track.update") -> None:
        self.subject = subject
        self.headers = headers


def _security_header(value: object) -> dict[str, str]:
    return {DEFAULT_SECURITY_LABELS_HEADER: json.dumps(value)}


def _sample_profile() -> dict[str, object]:
    return {
        "profile": SECURITY_LABEL_PROFILE_NAME,
        "classification": "NATO SECRET",
        "releasability": ["NATO", "FVEY"],
        "handling_caveats": ["MISSION"],
        "owner": "example-owner",
        "originator": "example-originator",
        "policy_id": "example-policy",
        "retention_category": "mission-log-30d",
    }


def test_security_labels_are_disabled_by_default() -> None:
    envelope = envelope_from_nats_message(HeaderRawMessage(_security_header(_sample_profile())))

    assert envelope.security_labels is None
    assert envelope.security_labels_for_json_storage() is None


def test_security_label_header_is_parsed_frozen_and_filled_from_message_metadata() -> None:
    config = SecurityLabelProfileConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(
            {
                **_security_header(_sample_profile()),
                "Nats-Sinks-Priority": "immediate",
                "Nats-Sinks-Classification": "NATO SECRET",
                "Nats-Sinks-Labels": "track;watch-floor",
            }
        ),
        security_labels=config,
    )

    profile = envelope.security_labels_for_json_storage()

    assert profile is not None
    assert profile["profile"] == SECURITY_LABEL_PROFILE_NAME
    assert profile["priority"] == "immediate"
    assert profile["classification"] == "NATO SECRET"
    assert profile["labels"] == ["track", "watch-floor"]
    assert profile["releasability"] == ["NATO", "FVEY"]
    with pytest.raises(TypeError):
        envelope.security_labels["classification"] = "changed"  # type: ignore[index]


def test_empty_security_label_header_explicitly_clears_default() -> None:
    config = SecurityLabelProfileConfig.model_validate(
        {"enabled": True, "default": _sample_profile()}
    )

    envelope = envelope_from_nats_message(
        HeaderRawMessage({DEFAULT_SECURITY_LABELS_HEADER: "  "}),
        security_labels=config,
    )

    assert envelope.security_labels is None


def test_security_label_subject_rules_override_global_defaults() -> None:
    config = SecurityLabelProfileConfig.model_validate(
        {
            "enabled": True,
            "default": {
                **_sample_profile(),
                "classification": "NATO RESTRICTED",
            },
            "rules": [
                {
                    "subject": "mission.urgent.>",
                    "profile": {
                        **_sample_profile(),
                        "classification": "NATO SECRET",
                    },
                }
            ],
        }
    )

    urgent = envelope_from_nats_message(
        HeaderRawMessage({}, subject="mission.urgent.track"),
        security_labels=config,
    )
    fallback = envelope_from_nats_message(
        HeaderRawMessage({}, subject="mission.normal.track"),
        security_labels=config,
    )

    assert urgent.security_labels_for_json_storage()["classification"] == "NATO SECRET"
    assert fallback.security_labels_for_json_storage()["classification"] == "NATO RESTRICTED"


def test_security_label_allow_lists_fail_closed() -> None:
    config = SecurityLabelProfileConfig.model_validate(
        {
            "enabled": True,
            "allowed_classifications": ["NATO UNCLASSIFIED"],
        }
    )

    with pytest.raises(ValidationError, match="classification"):
        envelope_from_nats_message(
            HeaderRawMessage(_security_header(_sample_profile())),
            security_labels=config,
        )


@pytest.mark.parametrize(
    "raw_header",
    [
        "{not-json",
        '{"profile":"nats-sinks.security-label.v1","profile":"duplicate"}',
        '["not", "an", "object"]',
        '{"profile":"nats-sinks.security-label.v1","password":"not allowed"}',
        '{"profile":"nats-sinks.security-label.v1","unknown":"not allowed"}',
        '{"profile":"nats-sinks.security-label.v1","classification":"line\\nbreak"}',
    ],
)
def test_invalid_security_label_headers_are_permanent_validation_errors(raw_header: str) -> None:
    config = SecurityLabelProfileConfig(enabled=True)

    with pytest.raises(ValidationError):
        envelope_from_nats_message(
            HeaderRawMessage({DEFAULT_SECURITY_LABELS_HEADER: raw_header}),
            security_labels=config,
        )


def test_security_label_size_limit_is_enforced() -> None:
    with pytest.raises(ValidationError, match="byte limit"):
        parse_security_label_header(
            json.dumps(
                {
                    "profile": SECURITY_LABEL_PROFILE_NAME,
                    "classification": "NATO UNCLASSIFIED",
                    "owner": "x" * 128,
                }
            ),
            max_bytes=64,
            allowed_priorities=(),
            allowed_classifications=(),
            allowed_releasability=(),
            allowed_handling_caveats=(),
            allowed_retention_categories=(),
            fallback_priority=None,
            fallback_classification=None,
            fallback_labels=(),
        )


def test_security_label_config_loads_from_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-sink",
                    "subject": "mission.>",
                },
                "security_labels": {
                    "enabled": True,
                    "allowed_classifications": ["NATO SECRET"],
                    "default": _sample_profile(),
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "out"),
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.security_labels.enabled is True
    assert config.security_labels.allowed_classifications == ("NATO SECRET",)
    assert config.security_labels.default["classification"] == "NATO SECRET"


def test_security_label_config_rejects_unknown_default_classification() -> None:
    with pytest.raises(PydanticValidationError, match="classification"):
        SecurityLabelProfileConfig.model_validate(
            {
                "enabled": True,
                "allowed_classifications": ["NATO UNCLASSIFIED"],
                "default": _sample_profile(),
            }
        )


def test_file_sink_records_security_labels_in_json_output() -> None:
    config = SecurityLabelProfileConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(_security_header(_sample_profile())),
        security_labels=config,
    )

    record = file_record_for_envelope(envelope, config=FileSinkConfig(directory=Path("out")))

    assert record["security_labels"]["classification"] == "NATO SECRET"
    assert record["metadata"]["security_labels"]["releasability"] == ["NATO", "FVEY"]


def test_oracle_row_maps_security_labels_to_json_column() -> None:
    config = SecurityLabelProfileConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(_security_header(_sample_profile())),
        security_labels=config,
    )
    envelope = replace(envelope, stream="ORDERS", stream_sequence=42)

    row = envelope_to_row(envelope, idempotency=OracleIdempotencyConfig())

    assert json.loads(row["security_labels_json"])["classification"] == "NATO SECRET"
    assert json.loads(row["metadata_json"])["security_labels"]["policy_id"] == "example-policy"
