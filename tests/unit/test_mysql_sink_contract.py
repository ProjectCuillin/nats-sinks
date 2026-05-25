# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

import pytest

from nats_sinks import (
    ConfigurationError,
    DestinationUnavailableError,
    InMemoryMetrics,
    MetricNames,
    NatsEnvelope,
    PermanentSinkError,
)
from nats_sinks.mysql import MySqlSink


def envelope() -> NatsEnvelope:
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
        priority="urgent",
        classification="restricted",
        labels=("billing", "urgent"),
    )


def envelope_with_subject(subject: str, sequence: int) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=b'{"order_id":"O-1001"}',
        headers={"Nats-Msg-Id": f"m-{sequence}"},
        stream="ORDERS",
        consumer="mysql",
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
        self.executions: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.rowcount = 1
        self.closed = False

    def execute(self, sql: str) -> None:
        self.statements.append(sql)

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.executions.append((sql, rows))

    def fetchone(self) -> tuple[int]:
        return (1,)

    def close(self) -> None:
        self.closed = True


class DuplicateReportingCursor(FakeCursor):
    def __init__(self, rowcount: int) -> None:
        super().__init__()
        self.rowcount = rowcount


class ExecutemanyFailingCursor(FakeCursor):
    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        super().executemany(sql, rows)
        raise RuntimeError("1054 Unknown column 'PAYLOAD_JSON'")


class RecordingConnection:
    def __init__(self, cursor: FakeCursor | None = None) -> None:
        self.cursor_instance = cursor or FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class RecordingPool:
    def __init__(self, connection: RecordingConnection | None = None) -> None:
        self.connection = connection or RecordingConnection()

    def get_connection(self) -> RecordingConnection:
        return self.connection


class CommitFailingConnection(RecordingConnection):
    def commit(self) -> None:
        raise RuntimeError("2013 Lost connection to Oracle MySQL server during query")


@pytest.mark.asyncio
async def test_mysql_sink_writes_tuples_and_commits() -> None:
    connection = RecordingConnection()
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert",
    )
    sink._pool = RecordingPool(connection)

    await sink.write_batch([envelope()])

    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True
    assert connection.cursor_instance.closed is True
    sql, rows = connection.cursor_instance.executions[0]
    assert sql.startswith("insert into `NATS_SINK_EVENTS`")
    assert rows[0][0] == "ORDERS"
    assert "orders.created" in rows[0]


@pytest.mark.asyncio
async def test_mysql_insert_ignore_duplicate_is_success() -> None:
    metrics = InMemoryMetrics()
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        metrics=metrics,
    )
    sink._pool = object()

    def raise_duplicate(rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        del rows_by_table
        raise RuntimeError("1062 Duplicate entry")

    sink._write_rows_sync = raise_duplicate  # type: ignore[method-assign]

    await sink.write_batch([envelope()])

    assert metrics.counters[MetricNames.MYSQL_CONFLICTS_TOTAL] == 1
    assert metrics.counters[MetricNames.MYSQL_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.MYSQL_DUPLICATE_IGNORED_TOTAL] == 1


@pytest.mark.asyncio
async def test_mysql_insert_ignore_rowcount_reports_ignored_duplicates() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=1)
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.MYSQL_CONFLICTS_TOTAL] == 0
    assert metrics.counters[MetricNames.MYSQL_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.MYSQL_DUPLICATE_IGNORED_TOTAL] == 1


@pytest.mark.asyncio
async def test_mysql_upsert_records_unknown_outcome_metrics() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=2)
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="upsert",
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.MYSQL_UPSERT_ROWS_TOTAL] == 2
    assert metrics.counters[MetricNames.MYSQL_UPSERT_OUTCOME_UNKNOWN_TOTAL] == 2
    assert metrics.counters[MetricNames.MYSQL_DUPLICATES_TOTAL] == 0


@pytest.mark.asyncio
async def test_mysql_upsert_without_update_columns_reports_noop_duplicates() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=1)
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="upsert",
        upsert_update_columns=[],
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.MYSQL_UPSERT_ROWS_TOTAL] == 2
    assert metrics.counters[MetricNames.MYSQL_UPSERT_OUTCOME_UNKNOWN_TOTAL] == 0
    assert metrics.counters[MetricNames.MYSQL_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.MYSQL_DUPLICATE_NOOP_TOTAL] == 1


@pytest.mark.asyncio
async def test_mysql_commit_failure_rolls_back_and_raises_temporary() -> None:
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert",
    )
    connection = CommitFailingConnection()
    sink._pool = RecordingPool(connection)

    with pytest.raises(DestinationUnavailableError, match="commit failed"):
        await sink.write_batch([envelope()])

    assert connection.rolled_back is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_mysql_schema_mismatch_has_human_message() -> None:
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert",
    )
    connection = RecordingConnection(ExecutemanyFailingCursor())
    sink._pool = RecordingPool(connection)

    with pytest.raises(PermanentSinkError, match="missing columns expected by nats-sinks"):
        await sink.write_batch([envelope()])

    assert connection.rolled_back is True


def test_mysql_requires_secret_source() -> None:
    with pytest.raises(ConfigurationError, match="password"):
        MySqlSink(
            host="127.0.0.1",
            database="nats_sinks_test",
            user="app_user",
            table="NATS_SINK_EVENTS",
        )


def test_mysql_rejects_conflicting_table_route_policies() -> None:
    with pytest.raises(ConfigurationError, match="conflicting Oracle MySQL idempotency"):
        MySqlSink(
            host="127.0.0.1",
            database="nats_sinks_test",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            table_routes=[
                {
                    "subject": "orders.*",
                    "table": "NATS_SINK_EVENTS",
                    "idempotency": {"strategy": "message_id"},
                }
            ],
        )


def test_mysql_pool_options_include_tls_ca_without_logging_secret() -> None:
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="database-secret",  # noqa: S106 - local test placeholder
        ssl_ca=".local/oracle-mysql-test/ca.pem",
    )

    options = sink._pool_options()

    assert options["ssl_ca"] == ".local/oracle-mysql-test/ca.pem"
    assert options["ssl_verify_identity"] is True
    assert options["password"] == "database" + "-secret"


def test_mysql_pool_options_normalize_connection_timeout_to_integer_seconds() -> None:
    sink = MySqlSink(
        host="database.internal",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        connection_timeout=2.5,
    )

    options = sink._pool_options()

    assert options["connection_timeout"] == 3
    assert isinstance(options["connection_timeout"], int)
