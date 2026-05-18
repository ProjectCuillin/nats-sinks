# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

import pytest

from nats_sinks import ConfigurationError, DestinationUnavailableError, NatsEnvelope
from nats_sinks.oracle import OracleSink


def envelope() -> NatsEnvelope:
    return NatsEnvelope(
        subject="orders.created",
        data=b'{"order_id":"O-1001"}',
        headers={"Nats-Msg-Id": "m-1"},
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=42,
        consumer_sequence=7,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def envelope_with_subject(subject: str, sequence: int) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=b'{"order_id":"O-1001"}',
        headers={"Nats-Msg-Id": f"m-{sequence}"},
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.executions: list[tuple[str, list[dict[str, Any]]]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.statements.append(sql)

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        self.executions.append((sql, rows))


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True


class RecordingPool:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    def acquire(self) -> RecordingConnection:
        return self.connection


class CommitFailingConnection:
    def __enter__(self) -> CommitFailingConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor()

    def commit(self) -> None:
        raise RuntimeError("ORA-03113: end-of-file on communication channel")


class CommitFailingPool:
    def acquire(self) -> CommitFailingConnection:
        return CommitFailingConnection()


@pytest.mark.asyncio
async def test_oracle_insert_ignore_duplicate_is_success() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
    )
    sink._pool = object()

    def raise_duplicate(rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        del rows_by_table
        raise RuntimeError("ORA-00001: unique constraint violated")

    sink._write_rows_sync = raise_duplicate  # type: ignore[method-assign]

    await sink.write_batch([envelope()])


@pytest.mark.asyncio
async def test_oracle_commit_failure_raises_temporary_error() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
    )
    sink._pool = CommitFailingPool()

    with pytest.raises(DestinationUnavailableError):
        await sink.write_batch([envelope()])


def test_oracle_pool_options_include_autonomous_wallet_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORACLE_PASSWORD", "database-secret")
    monkeypatch.setenv("ORACLE_WALLET_PASSWORD", "wallet-secret")
    sink = OracleSink(
        dsn="mydb_low",
        user="app_user",
        password_env="ORACLE_PASSWORD",  # noqa: S106 - environment variable name, not a secret
        config_dir=".local/oracle-adb/wallet",
        wallet_location=".local/oracle-adb/wallet",
        wallet_password_env="ORACLE_WALLET_PASSWORD",  # noqa: S106 - env var name only
        ssl_server_dn_match=True,
        retry_count=20,
        retry_delay=3,
        table="NATS_SINK_EVENTS",
        mode="merge",
    )

    options = sink._pool_options()

    assert options["dsn"] == "mydb_low"
    assert options["password"] == "database-secret"  # noqa: S105
    assert options["config_dir"] == ".local/oracle-adb/wallet"
    assert options["wallet_location"] == ".local/oracle-adb/wallet"
    assert options["wallet_password"] == "wallet-secret"  # noqa: S105
    assert options["ssl_server_dn_match"] is True
    assert options["retry_count"] == 20
    assert options["retry_delay"] == 3


def test_oracle_wallet_password_requires_wallet_location() -> None:
    with pytest.raises(ConfigurationError, match="wallet_password_env requires"):
        OracleSink(
            dsn="mydb_low",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            wallet_password_env="ORACLE_WALLET_PASSWORD",  # noqa: S106 - env var name only
            table="NATS_SINK_EVENTS",
            mode="merge",
        )


@pytest.mark.asyncio
async def test_oracle_routes_different_subjects_to_different_tables() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        table_routes=[
            {"subject": "orders.created", "table": "ORDER_CREATED_EVENTS"},
            {"subject": "orders.cancelled", "table": "ORDER_CANCELLED_EVENTS"},
        ],
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch(
        [
            envelope_with_subject("orders.created", 1),
            envelope_with_subject("orders.cancelled", 2),
            envelope_with_subject("orders.updated", 3),
        ]
    )

    statements = [sql for sql, _rows in pool.connection.cursor_instance.executions]
    assert any("ORDER_CREATED_EVENTS" in sql for sql in statements)
    assert any("ORDER_CANCELLED_EVENTS" in sql for sql in statements)
    assert any("NATS_SINK_EVENTS" in sql for sql in statements)
    assert "alter session disable parallel dml" in pool.connection.cursor_instance.statements
    assert pool.connection.committed


@pytest.mark.asyncio
async def test_oracle_sink_python_api_accepts_payload_mode() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        payload_mode="text_envelope",
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch([envelope()])

    rows = pool.connection.cursor_instance.executions[0][1]
    assert rows[0]["payload_json"].startswith('{"_nats_sinks":')


def test_oracle_rejects_invalid_table_route_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="invalid NATS subject route pattern"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="merge",
            table_routes=[{"subject": "orders.cre*ated", "table": "ORDER_CREATED_EVENTS"}],
        )
