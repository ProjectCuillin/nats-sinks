# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.config import MissionMetadataConfig, load_config
from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.mission_metadata import (
    DEFAULT_MISSION_METADATA_HEADER,
    parse_mission_metadata_header,
)
from nats_sinks.file.config import FileSinkConfig
from nats_sinks.file.mapping import file_record_for_envelope
from nats_sinks.oracle.config import OracleIdempotencyConfig
from nats_sinks.oracle.mapping import envelope_to_row


class HeaderRawMessage:
    """Minimal message-like object used to test core normalization."""

    data = b'{"event":"created"}'
    metadata = None

    def __init__(self, headers: dict[str, str], *, subject: str = "mission.track.update") -> None:
        self.subject = subject
        self.headers = headers


def _metadata_header(value: object) -> dict[str, str]:
    return {DEFAULT_MISSION_METADATA_HEADER: json.dumps(value)}


def test_mission_metadata_is_disabled_by_default() -> None:
    envelope = envelope_from_nats_message(
        HeaderRawMessage(_metadata_header({"profile": "mission-event-v1"}))
    )

    assert envelope.mission_metadata is None
    assert envelope.mission_metadata_for_json_storage() is None


def test_mission_metadata_header_is_parsed_and_frozen() -> None:
    config = MissionMetadataConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(
            _metadata_header(
                {
                    "profile": "mission-event-v1",
                    "mission_id": "M-1001",
                    "f2t2ea_phase": "track",
                    "source_confidence": 0.91,
                    "labels": ["coalition", "watch-floor"],
                }
            )
        ),
        mission_metadata=config,
    )

    assert envelope.mission_metadata_for_json_storage() == {
        "profile": "mission-event-v1",
        "mission_id": "M-1001",
        "f2t2ea_phase": "track",
        "source_confidence": 0.91,
        "labels": ["coalition", "watch-floor"],
    }
    with pytest.raises(TypeError):
        envelope.mission_metadata["mission_id"] = "changed"  # type: ignore[index]


def test_empty_mission_metadata_header_explicitly_clears_default() -> None:
    config = MissionMetadataConfig.model_validate(
        {
            "enabled": True,
            "default": {
                "profile": "mission-event-v1",
                "mission_id": "M-DEFAULT",
            },
        }
    )

    envelope = envelope_from_nats_message(
        HeaderRawMessage({DEFAULT_MISSION_METADATA_HEADER: "  "}),
        mission_metadata=config,
    )

    assert envelope.mission_metadata is None


def test_mission_metadata_subject_rules_override_global_defaults() -> None:
    config = MissionMetadataConfig.model_validate(
        {
            "enabled": True,
            "default": {
                "profile": "mission-event-v1",
                "mission_id": "M-DEFAULT",
            },
            "rules": [
                {
                    "subject": "mission.urgent.>",
                    "metadata": {
                        "profile": "mission-event-v1",
                        "mission_id": "M-URGENT",
                        "f2t2ea_phase": "track",
                    },
                }
            ],
        }
    )

    urgent = envelope_from_nats_message(
        HeaderRawMessage({}, subject="mission.urgent.track"),
        mission_metadata=config,
    )
    fallback = envelope_from_nats_message(
        HeaderRawMessage({}, subject="mission.normal.track"),
        mission_metadata=config,
    )

    assert urgent.mission_metadata_for_json_storage()["mission_id"] == "M-URGENT"
    assert fallback.mission_metadata_for_json_storage()["mission_id"] == "M-DEFAULT"


def test_mission_metadata_allowed_profiles_fail_closed() -> None:
    config = MissionMetadataConfig.model_validate(
        {
            "enabled": True,
            "allowed_profiles": ["mission-event-v1"],
        }
    )

    with pytest.raises(ValidationError, match="profile must be one"):
        envelope_from_nats_message(
            HeaderRawMessage(_metadata_header({"profile": "unknown"})),
            mission_metadata=config,
        )


@pytest.mark.parametrize(
    "raw_header",
    [
        "{not-json",
        '{"profile":"mission-event-v1","profile":"duplicate"}',
        '["not", "an", "object"]',
        '{"password":"not allowed"}',
        '{"bad key":"not allowed"}',
        '{"profile":"mission-event-v1","notes":"line\\nbreak"}',
    ],
)
def test_invalid_mission_metadata_headers_are_permanent_validation_errors(
    raw_header: str,
) -> None:
    config = MissionMetadataConfig(enabled=True)

    with pytest.raises(ValidationError):
        envelope_from_nats_message(
            HeaderRawMessage({DEFAULT_MISSION_METADATA_HEADER: raw_header}),
            mission_metadata=config,
        )


def test_mission_metadata_size_limit_is_enforced() -> None:
    with pytest.raises(ValidationError, match="byte limit"):
        parse_mission_metadata_header(
            json.dumps({"profile": "mission-event-v1", "notes": "x" * 128}),
            max_bytes=64,
            allowed_profiles=(),
        )


def test_mission_metadata_config_loads_from_json(tmp_path: Path) -> None:
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
                "mission_metadata": {
                    "enabled": True,
                    "allowed_profiles": ["mission-event-v1"],
                    "default": {
                        "profile": "mission-event-v1",
                        "mission_id": "M-DEFAULT",
                    },
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

    assert config.mission_metadata.enabled is True
    assert config.mission_metadata.allowed_profiles == ("mission-event-v1",)
    assert config.mission_metadata.default["mission_id"] == "M-DEFAULT"


def test_mission_metadata_config_rejects_unknown_profile_default() -> None:
    with pytest.raises(PydanticValidationError, match="profile must be one"):
        MissionMetadataConfig.model_validate(
            {
                "enabled": True,
                "allowed_profiles": ["mission-event-v1"],
                "default": {
                    "profile": "mission-event-v2",
                },
            }
        )


def test_file_sink_records_mission_metadata_in_json_output() -> None:
    config = MissionMetadataConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(
            _metadata_header(
                {
                    "profile": "mission-event-v1",
                    "mission_id": "M-1001",
                    "f2t2ea_phase": "fix",
                }
            )
        ),
        mission_metadata=config,
    )

    record = file_record_for_envelope(envelope, config=FileSinkConfig(directory=Path("out")))

    assert record["mission_metadata"] == {
        "profile": "mission-event-v1",
        "mission_id": "M-1001",
        "f2t2ea_phase": "fix",
    }
    assert record["metadata"]["mission_metadata"]["f2t2ea_phase"] == "fix"


def test_oracle_row_maps_mission_metadata_to_json_column() -> None:
    config = MissionMetadataConfig(enabled=True)
    envelope = envelope_from_nats_message(
        HeaderRawMessage(
            _metadata_header(
                {
                    "profile": "mission-event-v1",
                    "mission_id": "M-1001",
                    "f2t2ea_phase": "finish",
                }
            )
        ),
        mission_metadata=config,
    )
    envelope = replace(envelope, stream="ORDERS", stream_sequence=42)

    row = envelope_to_row(envelope, idempotency=OracleIdempotencyConfig())

    assert json.loads(row["mission_metadata_json"]) == {
        "profile": "mission-event-v1",
        "mission_id": "M-1001",
        "f2t2ea_phase": "finish",
    }
    assert json.loads(row["metadata_json"])["mission_metadata"]["mission_id"] == "M-1001"
