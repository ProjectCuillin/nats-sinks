# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for read-only Oracle lineage query helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.main import app
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle import (
    OracleSinkConfig,
    build_oracle_lineage_query,
    render_lineage_result_text,
    resolve_lineage_table,
)
from nats_sinks.oracle.config import OracleColumnMapping
from nats_sinks.oracle.lineage import (
    MAX_LINEAGE_LIMIT,
    OracleLineageResult,
    oracle_lineage_records_from_rows,
)


def test_oracle_lineage_query_uses_allowlisted_json_field_and_bind_value() -> None:
    """Mission metadata lookups should bind values and keep payloads out by default."""

    query = build_oracle_lineage_query(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        field="mission-id",
        value="MISSION-ALPHA",
        limit=25,
    )

    assert query.field == "mission_id"
    assert query.binds == {"lineage_value": "MISSION-ALPHA"}
    assert "MISSION-ALPHA" not in query.sql
    assert "json_value(MISSION_METADATA_JSON, '$.mission_id')" in query.sql
    assert "fetch first 25 rows only" in query.sql
    assert "PAYLOAD_JSON" not in query.sql


def test_oracle_lineage_query_can_include_payload_only_when_explicit() -> None:
    query = build_oracle_lineage_query(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        field="message_id",
        value="msg-001",
        include_payload=True,
    )

    assert "MESSAGE_ID = :lineage_value" in query.sql
    assert "PAYLOAD_JSON as payload_json" in query.sql
    assert query.include_payload is True


@pytest.mark.parametrize("field", ["", "payload", "metadata_json", "mission_id;drop"])
def test_oracle_lineage_query_rejects_unknown_fields(field: str) -> None:
    with pytest.raises(ConfigurationError, match="lineage field must be one of"):
        build_oracle_lineage_query(
            table="nats_sink_events",
            columns=OracleColumnMapping(),
            field=field,
            value="safe",
        )


@pytest.mark.parametrize("value", ["", "  ", "mission\nalpha", "x" * 513])
def test_oracle_lineage_query_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ConfigurationError):
        build_oracle_lineage_query(
            table="nats_sink_events",
            columns=OracleColumnMapping(),
            field="mission_id",
            value=value,
        )


@pytest.mark.parametrize("limit", [0, -1, MAX_LINEAGE_LIMIT + 1])
def test_oracle_lineage_query_rejects_unbounded_limits(limit: int) -> None:
    with pytest.raises(ConfigurationError, match="lineage limit must be between"):
        build_oracle_lineage_query(
            table="nats_sink_events",
            columns=OracleColumnMapping(),
            field="mission_id",
            value="M-1",
            limit=limit,
        )


def test_oracle_lineage_table_must_be_configured_table_or_route() -> None:
    config = OracleSinkConfig.model_validate(
        {
            "type": "oracle",
            "dsn": "localhost/FREEPDB1",
            "user": "app",
            "password_env": "NATS_SINKS_TEST_ORACLE_PASSWORD",
            "table": "NATS_EVENTS_DEFAULT",
            "table_routes": [
                {"subject": "mission.secret.>", "table": "NATS_EVENTS_SECRET"},
            ],
        }
    )

    assert resolve_lineage_table(config, None) == "NATS_EVENTS_DEFAULT"
    assert resolve_lineage_table(config, "nats_events_secret") == "NATS_EVENTS_SECRET"
    with pytest.raises(ConfigurationError, match="lineage table must be"):
        resolve_lineage_table(config, "OTHER_TABLE")


def test_oracle_lineage_records_and_text_output_omit_payload_by_default() -> None:
    rows = [
        (
            "MISSION",
            42,
            "mission.events",
            "msg-42",
            "urgent",
            "NATO SECRET",
            "track;watch-floor",
            1_000,
            2_000,
            3_000,
            '{"mission_id":"M-1","correlation_id":"C-1"}',
            '{"sensitive":"payload"}',
        )
    ]
    aliases = (
        "stream_name",
        "stream_sequence",
        "subject",
        "message_id",
        "priority",
        "classification",
        "labels",
        "message_created_at_epoch_ns",
        "received_at_epoch_ns",
        "stored_at_epoch_ns",
        "mission_metadata_json",
        "payload_json",
    )

    records = oracle_lineage_records_from_rows(rows, aliases=aliases, include_payload=False)
    result = OracleLineageResult(
        field="mission_id",
        table_name="NATS_EVENTS",
        limit=50,
        records=records,
    )

    rendered = result.to_dict()
    text = render_lineage_result_text(result)
    assert rendered["records"][0]["mission_metadata_keys"] == ["correlation_id", "mission_id"]
    assert "payload_json" not in rendered["records"][0]
    assert "payload=omitted" in text
    assert "sensitive" not in json.dumps(rendered)
    assert "sensitive" not in text


def test_oracle_lineage_records_can_include_payload_when_explicit() -> None:
    records = oracle_lineage_records_from_rows(
        [("MISSION", 1, "s", "m", None, None, None, None, 1, 2, None, '{"ok":true}')],
        aliases=(
            "stream_name",
            "stream_sequence",
            "subject",
            "message_id",
            "priority",
            "classification",
            "labels",
            "message_created_at_epoch_ns",
            "received_at_epoch_ns",
            "stored_at_epoch_ns",
            "mission_metadata_json",
            "payload_json",
        ),
        include_payload=True,
    )

    assert records[0].to_dict(include_payload=True)["payload_json"] == {"ok": True}


def test_cli_lineage_dry_run_is_script_friendly_and_redacted(tmp_path: Path) -> None:
    config = tmp_path / "oracle-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "MISSION",
                    "consumer": "oracle-mission-sink",
                    "subject": "mission.>",
                },
                "sink": {
                    "type": "oracle",
                    "dsn": "localhost/FREEPDB1",
                    "user": "app",
                    "password_env": "NATS_SINKS_TEST_ORACLE_PASSWORD",
                    "table": "NATS_EVENTS",
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "query-lineage",
            str(config),
            "--field",
            "mission_id",
            "--value",
            "MISSION-ALPHA",
            "--limit",
            "10",
            "--format",
            "json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["field"] == "mission_id"
    assert payload["table"] == "NATS_EVENTS"
    assert payload["limit"] == 10
    assert payload["payload_included"] is False
    assert payload["binds"] == ["lineage_value"]
    assert "MISSION-ALPHA" not in result.output
    assert "PAYLOAD_JSON" not in payload["sql"]


def test_cli_lineage_rejects_non_oracle_config(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "MISSION",
                    "consumer": "file-mission-sink",
                    "subject": "mission.>",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "query-lineage",
            str(config),
            "--field",
            "mission_id",
            "--value",
            "MISSION-ALPHA",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "lineage queries currently require sink.type 'oracle'" in result.output
