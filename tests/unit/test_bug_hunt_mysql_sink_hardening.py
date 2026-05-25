# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

import nats_sinks.mysql.sink as mysql_sink_module
from nats_sinks import (
    ConfigurationError,
    DestinationUnavailableError,
    NatsEnvelope,
    PermanentSinkError,
)
from nats_sinks.mysql import MySqlSink
from nats_sinks.mysql.config import MySqlColumnMapping, MySqlSinkConfig
from nats_sinks.mysql.ddl import create_events_table_ddl
from nats_sinks.mysql.sql import build_write_sql, validate_identifier

CONFIG_ERRORS = (ConfigurationError, PydanticValidationError)


def _base_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "type": "mysql",
        "host": "db.example.invalid",
        "database": "nats_sinks",
        "user": "nats_sinks_app",
        "password_env": "NATS_SINKS_MYSQL_PASSWORD",
    }
    config.update(overrides)
    return config


def _envelope() -> NatsEnvelope:
    return NatsEnvelope(
        subject="orders.created",
        data=b'{"order_id":"O-1001"}',
        headers={"Nats-Msg-Id": "m-1"},
        stream="ORDERS",
        consumer="mysql",
        stream_sequence=42,
        consumer_sequence=7,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


class _FakePool:
    def __init__(self, **_kwargs: object) -> None:
        self.connection = _RecordingConnection()

    def get_connection(self) -> _RecordingConnection:
        return self.connection


class _FakePoolingModule:
    @staticmethod
    def MySQLConnectionPool(**kwargs: object) -> _FakePool:  # noqa: N802 - driver API shape
        return _FakePool(**kwargs)


def _patch_mysql_driver_import(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import(name: str) -> object:
        if name == "mysql.connector":
            return object()
        if name == "mysql.connector.pooling":
            return _FakePoolingModule()
        raise ImportError(name)

    monkeypatch.setattr(mysql_sink_module.importlib, "import_module", fake_import)


class _RecordingCursor:
    rowcount = 1

    def __init__(self, *, fail_write: bool = False, fail_close: bool = False) -> None:
        self.fail_write = fail_write
        self.fail_close = fail_close

    def execute(self, _sql: str) -> None:
        return None

    def executemany(self, _sql: str, _rows: list[tuple[Any, ...]]) -> None:
        if self.fail_write:
            raise RuntimeError("1054 Unknown column 'PAYLOAD_JSON'")

    def fetchone(self) -> tuple[int]:
        return (1,)

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("cursor close failed")


class _RecordingConnection:
    def __init__(
        self,
        *,
        cursor: _RecordingCursor | None = None,
        fail_close: bool = False,
    ) -> None:
        self.cursor_instance = cursor or _RecordingCursor()
        self.fail_close = fail_close
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("connection close failed")


class _RecordingPool:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    def get_connection(self) -> _RecordingConnection:
        return self.connection


def test_mysql_config_rejects_conflicting_password_sources() -> None:
    with pytest.raises(CONFIG_ERRORS, match=r"password.*password_env"):
        MySqlSinkConfig.model_validate(
            _base_config(
                password="inline-test-secret",  # noqa: S106
                password_env="NATS_SINKS_MYSQL_PASSWORD",  # noqa: S106
            )
        )


def test_mysql_config_rejects_blank_inline_password() -> None:
    with pytest.raises(CONFIG_ERRORS, match="password"):
        MySqlSinkConfig.model_validate(_base_config(password="   ", password_env=None))  # noqa: S106


def test_mysql_config_rejects_invalid_password_env_name() -> None:
    with pytest.raises(CONFIG_ERRORS, match="password_env"):
        MySqlSinkConfig.model_validate(_base_config(password_env="bad env name"))  # noqa: S106


def test_mysql_resolve_password_rejects_empty_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NATS_SINKS_MYSQL_PASSWORD", "")
    config = MySqlSinkConfig.model_validate(_base_config())

    with pytest.raises(ConfigurationError, match="must not be empty"):
        config.resolve_password()


def test_mysql_config_rejects_control_characters_in_connection_fields() -> None:
    with pytest.raises(CONFIG_ERRORS, match="control characters"):
        MySqlSinkConfig.model_validate(_base_config(host="db.example.invalid\nspoof"))


def test_mysql_config_rejects_invalid_tls_paths() -> None:
    with pytest.raises(CONFIG_ERRORS, match="ssl_ca"):
        MySqlSinkConfig.model_validate(_base_config(ssl_ca=""))


def test_mysql_config_rejects_invalid_pool_name() -> None:
    with pytest.raises(CONFIG_ERRORS, match="pool_name"):
        MySqlSinkConfig.model_validate(_base_config(pool_name="pool name with spaces"))


def test_mysql_sql_rejects_duplicate_column_mapping() -> None:
    with pytest.raises(ConfigurationError, match="column mapping"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(headers="PAYLOAD_JSON"),
            mode="upsert",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        )


def test_mysql_sql_rejects_dotted_column_mapping() -> None:
    with pytest.raises(ConfigurationError, match="column identifier"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(payload="events.PAYLOAD_JSON"),
            mode="upsert",
            key_columns=["STREAM_NAME", "STREAM_SEQUENCE"],
        )


def test_mysql_sql_rejects_unknown_idempotency_key_columns() -> None:
    with pytest.raises(ConfigurationError, match="idempotency key"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(),
            mode="upsert",
            key_columns=["NOT_A_MAPPED_COLUMN"],
        )


def test_mysql_sql_rejects_duplicate_idempotency_key_columns() -> None:
    with pytest.raises(ConfigurationError, match="duplicate idempotency"):
        build_write_sql(
            table="NATS_SINK_EVENTS",
            columns=MySqlColumnMapping(),
            mode="upsert",
            key_columns=["STREAM_NAME", "STREAM_NAME"],
        )


def test_mysql_table_identifier_rejects_more_than_schema_and_table() -> None:
    with pytest.raises(ConfigurationError, match=r"schema.table"):
        validate_identifier("too.many.parts")


def test_mysql_ddl_supports_max_length_table_identifier() -> None:
    table = "N" * 64

    ddl = create_events_table_ddl(table)

    assert f"`{table}`" in ddl
    assert "primary key" in ddl


@pytest.mark.asyncio
async def test_mysql_start_preserves_missing_password_env_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mysql_driver_import(monkeypatch)
    monkeypatch.delenv("NATS_SINKS_MYSQL_PASSWORD", raising=False)
    sink = MySqlSink(
        host="db.example.invalid",
        database="nats_sinks",
        user="nats_sinks_app",
        password_env="NATS_SINKS_MYSQL_PASSWORD",  # noqa: S106
    )

    with pytest.raises(ConfigurationError, match="environment variable"):
        await sink.start()


@pytest.mark.asyncio
async def test_mysql_start_clears_pool_when_auto_create_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mysql_driver_import(monkeypatch)
    sink = MySqlSink(
        host="db.example.invalid",
        database="nats_sinks",
        user="nats_sinks_app",
        password="example",  # noqa: S106
        auto_create=True,
    )

    async def fail_schema() -> None:
        raise DestinationUnavailableError("schema unavailable")

    monkeypatch.setattr(sink, "ensure_schema", fail_schema)

    with pytest.raises(DestinationUnavailableError, match="schema unavailable"):
        await sink.start()

    assert sink._pool is None


@pytest.mark.asyncio
async def test_mysql_connection_close_after_commit_does_not_fail_committed_write() -> None:
    connection = _RecordingConnection(fail_close=True)
    sink = MySqlSink(
        host="db.example.invalid",
        database="nats_sinks",
        user="nats_sinks_app",
        password="example",  # noqa: S106
        mode="insert",
    )
    sink._pool = _RecordingPool(connection)

    await sink.write_batch([_envelope()])

    assert connection.committed is True
    assert connection.rolled_back is False


@pytest.mark.asyncio
async def test_mysql_cursor_close_failure_does_not_mask_schema_error() -> None:
    cursor = _RecordingCursor(fail_write=True, fail_close=True)
    connection = _RecordingConnection(cursor=cursor)
    sink = MySqlSink(
        host="db.example.invalid",
        database="nats_sinks",
        user="nats_sinks_app",
        password="example",  # noqa: S106
        mode="insert",
    )
    sink._pool = _RecordingPool(connection)

    with pytest.raises(PermanentSinkError, match="unknown column"):
        await sink.write_batch([_envelope()])

    assert connection.rolled_back is True


def test_mysql_documentation_route_example_does_not_duplicate_payload_field() -> None:
    docs = Path("docs/mysql-sink.md").read_text(encoding="utf-8")
    route_example = docs.split("Route-specific idempotency can also be configured:", 1)[1].split(
        "Routes that point to the same table", 1
    )[0]

    assert route_example.count('"payload_field": "event_id"') == 1
