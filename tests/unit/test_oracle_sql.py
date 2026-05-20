# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle.config import OracleColumnMapping
from nats_sinks.oracle.ddl import create_events_table_ddl
from nats_sinks.oracle.sql import build_write_sql, validate_identifier


def test_validate_identifier_allows_dotted_schema_table() -> None:
    assert validate_identifier("app.nats_sink_events") == "APP.NATS_SINK_EVENTS"


def test_validate_identifier_rejects_injection() -> None:
    with pytest.raises(ConfigurationError):
        validate_identifier("events; drop table users")


def test_build_merge_sql_uses_bind_variables() -> None:
    statement = build_write_sql(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        mode="merge",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert "merge into NATS_SINK_EVENTS target" in statement.sql
    assert ":stream_name" in statement.sql
    assert "when matched then update" in statement.sql
    assert statement.table_name == "NATS_SINK_EVENTS"
    assert statement.key_columns == ("STREAM_NAME", "STREAM_SEQUENCE")
    assert ":priority" in statement.sql
    assert ":classification" in statement.sql
    assert ":labels" in statement.sql
    assert ":metadata_json" in statement.sql
    assert ":stored_at_epoch_ns" in statement.sql


def test_build_insert_ignore_sql_has_no_update_clause() -> None:
    statement = build_write_sql(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        mode="insert_ignore",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert "when not matched then insert" in statement.sql
    assert "when matched then update" not in statement.sql


def test_append_mode_is_plain_insert_with_append_hint() -> None:
    statement = build_write_sql(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        mode="append",
        key_columns=[],
    )

    assert statement.sql.startswith("insert /*+ append */ into")


def test_recommended_ddl_contains_metadata_and_epoch_columns() -> None:
    ddl = create_events_table_ddl("nats_sink_events")

    assert "metadata_json     json" in ddl
    assert "priority          clob" in ddl
    assert "classification    clob" in ddl
    assert "labels            clob" in ddl
    assert "message_created_at_epoch_ns number(19)" in ddl
    assert "received_at_epoch_ns number(19) not null" in ddl
    assert "stored_at_epoch_ns number(19) not null" in ddl
