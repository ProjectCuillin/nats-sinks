# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle.config import OracleColumnMapping
from nats_sinks.oracle.ddl import create_events_table_ddl, create_staging_events_table_ddl
from nats_sinks.oracle.sql import build_staging_merge_sql, build_write_sql, validate_identifier


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
    assert ":mission_metadata_json" in statement.sql
    assert ":stored_at_epoch_ns" in statement.sql
    assert "target.PAYLOAD_JSON = source.PAYLOAD_JSON" in statement.sql
    assert statement.update_columns


def test_build_merge_sql_can_limit_update_columns() -> None:
    statement = build_write_sql(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        mode="merge",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        merge_update_columns=["payload_json", "metadata_json"],
    )

    assert "target.PAYLOAD_JSON = source.PAYLOAD_JSON" in statement.sql
    assert "target.METADATA_JSON = source.METADATA_JSON" in statement.sql
    assert "target.SUBJECT = source.SUBJECT" not in statement.sql
    assert statement.update_columns == ("PAYLOAD_JSON", "METADATA_JSON")


def test_build_merge_sql_can_leave_matched_rows_unchanged() -> None:
    statement = build_write_sql(
        table="nats_sink_events",
        columns=OracleColumnMapping(),
        mode="merge",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        merge_update_columns=[],
    )

    assert "when matched then update" not in statement.sql
    assert "when not matched then insert" in statement.sql
    assert statement.update_columns == ()


def test_build_merge_sql_rejects_unknown_update_columns() -> None:
    with pytest.raises(ConfigurationError, match="not present"):
        build_write_sql(
            table="nats_sink_events",
            columns=OracleColumnMapping(),
            mode="merge",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
            merge_update_columns=["NOT_A_MAPPED_COLUMN"],
        )


def test_build_merge_sql_rejects_key_update_columns() -> None:
    with pytest.raises(ConfigurationError, match="idempotency key columns"):
        build_write_sql(
            table="nats_sink_events",
            columns=OracleColumnMapping(),
            mode="merge",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
            merge_update_columns=["STREAM_SEQUENCE"],
        )


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
    assert "mission_metadata_json json" in ddl
    assert "priority          clob" in ddl
    assert "classification    clob" in ddl
    assert "labels            clob" in ddl
    assert "message_created_at_epoch_ns number(19)" in ddl
    assert "received_at_epoch_ns number(19) not null" in ddl
    assert "stored_at_epoch_ns number(19) not null" in ddl


def test_build_staging_merge_sql_loads_stage_and_merges_target() -> None:
    statement = build_staging_merge_sql(
        target_table="nats_sink_events",
        staging_table="nats_sink_events_stage",
        batch_id_column="nats_sinks_batch_id",
        columns=OracleColumnMapping(),
        mode="merge",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert statement.target_table_name == "NATS_SINK_EVENTS"
    assert statement.staging_table_name == "NATS_SINK_EVENTS_STAGE"
    assert statement.batch_id_column == "NATS_SINKS_BATCH_ID"
    assert statement.batch_bind_name == "nats_sinks_batch_id"
    assert statement.insert_sql.startswith(
        "insert into NATS_SINK_EVENTS_STAGE (NATS_SINKS_BATCH_ID"
    )
    assert ":nats_sinks_batch_id" in statement.insert_sql
    assert "merge into NATS_SINK_EVENTS target" in statement.merge_sql
    assert "from NATS_SINK_EVENTS_STAGE where NATS_SINKS_BATCH_ID = :nats_sinks_batch_id" in (
        statement.merge_sql
    )
    assert "when matched then update" in statement.merge_sql
    assert statement.update_columns
    assert statement.cleanup_sql == (
        "delete from NATS_SINK_EVENTS_STAGE where NATS_SINKS_BATCH_ID = :nats_sinks_batch_id"
    )


def test_build_staging_merge_sql_can_leave_matched_rows_unchanged() -> None:
    statement = build_staging_merge_sql(
        target_table="nats_sink_events",
        staging_table="nats_sink_events_stage",
        batch_id_column="nats_sinks_batch_id",
        columns=OracleColumnMapping(),
        mode="merge",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        merge_update_columns=[],
    )

    assert "when matched then update" not in statement.merge_sql
    assert "when not matched then insert" in statement.merge_sql
    assert statement.update_columns == ()


def test_build_staging_insert_ignore_sql_has_no_update_clause() -> None:
    statement = build_staging_merge_sql(
        target_table="nats_sink_events",
        staging_table="nats_sink_events_stage",
        batch_id_column="nats_sinks_batch_id",
        columns=OracleColumnMapping(),
        mode="insert_ignore",
        key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
    )

    assert "when not matched then insert" in statement.merge_sql
    assert "when matched then update" not in statement.merge_sql


def test_build_staging_merge_sql_rejects_unsafe_stage_identifier() -> None:
    with pytest.raises(ConfigurationError):
        build_staging_merge_sql(
            target_table="nats_sink_events",
            staging_table="events_stage; drop table events",
            batch_id_column="nats_sinks_batch_id",
            columns=OracleColumnMapping(),
            mode="merge",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        )


def test_build_staging_merge_sql_rejects_non_merge_modes() -> None:
    with pytest.raises(ConfigurationError, match="staging merge SQL requires"):
        build_staging_merge_sql(
            target_table="nats_sink_events",
            staging_table="nats_sink_events_stage",
            batch_id_column="nats_sinks_batch_id",
            columns=OracleColumnMapping(),
            mode="append",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        )


def test_recommended_staging_ddl_contains_batch_column_and_no_primary_key() -> None:
    ddl = create_staging_events_table_ddl("nats_sink_events_stage")

    assert "NATS_SINK_EVENTS_STAGE" in ddl
    assert "NATS_SINKS_BATCH_ID varchar2(64) not null" in ddl
    assert "primary key" not in ddl.lower()
    assert "mission_metadata_json json" in ddl
