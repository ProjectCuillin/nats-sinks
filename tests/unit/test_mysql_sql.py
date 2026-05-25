# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.mysql.config import MySqlColumnMapping
from nats_sinks.mysql.ddl import create_events_table_ddl
from nats_sinks.mysql.sql import build_write_sql, quote_identifier, validate_identifier


def test_validate_identifier_allows_dotted_database_table() -> None:
    assert validate_identifier("app.NATS_SINK_EVENTS") == "app.NATS_SINK_EVENTS"
    assert quote_identifier("app.NATS_SINK_EVENTS") == "`app`.`NATS_SINK_EVENTS`"


def test_validate_identifier_rejects_injection() -> None:
    with pytest.raises(ConfigurationError):
        validate_identifier("events; drop table users")


def test_build_upsert_sql_uses_positional_placeholders() -> None:
    statement = build_write_sql(
        table="NATS_SINK_EVENTS",
        columns=MySqlColumnMapping(),
        mode="upsert",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert "insert into `NATS_SINK_EVENTS`" in statement.sql
    assert "%s" in statement.sql
    assert "on duplicate key update" in statement.sql
    assert "values(`PAYLOAD_JSON`)" in statement.sql
    assert statement.table_name == "NATS_SINK_EVENTS"
    assert statement.key_columns == ("STREAM_NAME", "STREAM_SEQUENCE")
    assert "payload_json" in statement.bind_names
    assert statement.update_columns


def test_build_upsert_sql_can_limit_update_columns() -> None:
    statement = build_write_sql(
        table="NATS_SINK_EVENTS",
        columns=MySqlColumnMapping(),
        mode="upsert",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        upsert_update_columns=["PAYLOAD_JSON", "METADATA_JSON"],
    )

    assert "`PAYLOAD_JSON` = values(`PAYLOAD_JSON`)" in statement.sql
    assert "`METADATA_JSON` = values(`METADATA_JSON`)" in statement.sql
    assert "`SUBJECT` = values(`SUBJECT`)" not in statement.sql
    assert statement.update_columns == ("PAYLOAD_JSON", "METADATA_JSON")


def test_build_upsert_sql_can_leave_matched_rows_unchanged() -> None:
    statement = build_write_sql(
        table="NATS_SINK_EVENTS",
        columns=MySqlColumnMapping(),
        mode="upsert",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        upsert_update_columns=[],
    )

    assert "on duplicate key update `STREAM_NAME` = `STREAM_NAME`" in statement.sql
    assert statement.update_columns == ()


def test_build_upsert_sql_rejects_unknown_update_columns() -> None:
    with pytest.raises(ConfigurationError, match="not present"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(),
            mode="upsert",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
            upsert_update_columns=["NOT_A_MAPPED_COLUMN"],
        )


def test_build_upsert_sql_rejects_key_update_columns() -> None:
    with pytest.raises(ConfigurationError, match="idempotency key columns"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(),
            mode="upsert",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
            upsert_update_columns=["STREAM_SEQUENCE"],
        )


def test_build_insert_ignore_sql_has_no_update_clause() -> None:
    statement = build_write_sql(
        table="NATS_SINK_EVENTS",
        columns=MySqlColumnMapping(),
        mode="insert_ignore",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert statement.sql.startswith("insert ignore into")
    assert "on duplicate key update" not in statement.sql


def test_append_mode_is_plain_insert() -> None:
    statement = build_write_sql(
        table="NATS_SINK_EVENTS",
        columns=MySqlColumnMapping(),
        mode="append",
        key_columns=[],
    )

    assert statement.sql.startswith("insert into `NATS_SINK_EVENTS`")
    assert "on duplicate key update" not in statement.sql


def test_recommended_ddl_contains_metadata_epoch_and_label_columns() -> None:
    ddl = create_events_table_ddl("NATS_SINK_EVENTS")

    assert "`PAYLOAD_JSON` json" in ddl
    assert "`METADATA_JSON` json" in ddl
    assert "`MISSION_METADATA_JSON` json" in ddl
    assert "`SECURITY_LABELS_JSON` json" in ddl
    assert "`PRIORITY` text" in ddl
    assert "`CLASSIFICATION` text" in ddl
    assert "`LABELS` text" in ddl
    assert "primary key (`STREAM_NAME`, `STREAM_SEQUENCE`)" in ddl
