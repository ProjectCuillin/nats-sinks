# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime
from typing import Any

import pytest

from nats_sinks import InMemoryMetrics, MetricNames, NatsEnvelope
from nats_sinks.mysql import MySqlSink
from nats_sinks.mysql.sql import quote_identifier, validate_identifier

pytestmark = pytest.mark.integration

DEFAULT_MYSQL_TEST_TABLE = "NATS_SINKS_MYSQL_TEST_EVENTS"


def _enabled() -> bool:
    return os.getenv("NATS_SINKS_MYSQL_INTEGRATION") == "1"


def _setting(name: str, fallback: str | None = None) -> str | None:
    return os.getenv(f"NATS_SINKS_MYSQL_{name}", fallback)


def _required(value: str | None, name: str) -> str:
    if not value:
        pytest.skip(f"{name} is required for Oracle MySQL integration tests")
    return value


def _connector_module() -> Any:
    try:
        return importlib.import_module("mysql.connector")
    except ImportError:
        pytest.skip("install nats-sinks[mysql] to run Oracle MySQL integration tests")


def _password() -> str:
    password_env = _setting("PASSWORD_ENV", "NATS_SINKS_MYSQL_PASSWORD")
    return _required(os.getenv(password_env or ""), password_env or "NATS_SINKS_MYSQL_PASSWORD")


def _mysql_connection() -> Any:
    connector = _connector_module()
    return connector.connect(
        host=_required(_setting("HOST"), "NATS_SINKS_MYSQL_HOST"),
        port=int(_required(_setting("PORT"), "NATS_SINKS_MYSQL_PORT")),
        database=_required(_setting("DATABASE"), "NATS_SINKS_MYSQL_DATABASE"),
        user=_required(_setting("USER"), "NATS_SINKS_MYSQL_USER"),
        password=_password(),
        connection_timeout=10,
    )


def _drop_table_if_requested(table: str) -> None:
    if _setting("DROP_TABLE_BEFORE", "false").lower() != "true":
        return
    connection = _mysql_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(f"drop table if exists {quote_identifier(validate_identifier(table))}")  # nosec B608
        finally:
            cursor.close()
        connection.commit()
    finally:
        connection.close()


def _count_rows(table: str) -> int:
    connection = _mysql_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(f"select count(*) from {quote_identifier(validate_identifier(table))}")  # noqa: S608  # nosec B608
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()
    return int(row[0])


def _select_one(table: str) -> tuple[str | None, str | None, str | None, str]:
    connection = _mysql_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "select PRIORITY, CLASSIFICATION, LABELS, PAYLOAD_JSON "  # noqa: S608
                f"from {quote_identifier(validate_identifier(table))} "
                "where STREAM_SEQUENCE = %s",
                (1,),
            )  # nosec B608
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()
    assert row is not None
    return row


def _envelope(
    *,
    subject: str,
    sequence: int,
    data: bytes,
    priority: str | None = None,
    classification: str | None = None,
    labels: tuple[str, ...] = (),
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={"Nats-Msg-Id": f"mysql-e2e-{sequence}"},
        stream="MYSQL_E2E",
        consumer="mysql-e2e",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        message_id=None,
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=labels,
    )


@pytest.mark.skipif(
    not _enabled(),
    reason="set NATS_SINKS_MYSQL_INTEGRATION=1 to run Oracle MySQL integration tests",
)
@pytest.mark.asyncio
async def test_mysql_sink_container_e2e_routes_and_idempotent_writes() -> None:
    """Write routed events to the short-lived Oracle MySQL test container."""

    default_table = validate_identifier(
        _setting("TABLE", DEFAULT_MYSQL_TEST_TABLE) or DEFAULT_MYSQL_TEST_TABLE
    )
    route_table = validate_identifier(f"{default_table}_ROUTE")
    _drop_table_if_requested(default_table)
    _drop_table_if_requested(route_table)
    metrics = InMemoryMetrics()
    sink = MySqlSink(
        host=_required(_setting("HOST"), "NATS_SINKS_MYSQL_HOST"),
        port=int(_required(_setting("PORT"), "NATS_SINKS_MYSQL_PORT")),
        database=_required(_setting("DATABASE"), "NATS_SINKS_MYSQL_DATABASE"),
        user=_required(_setting("USER"), "NATS_SINKS_MYSQL_USER"),
        password_env=_setting("PASSWORD_ENV", "NATS_SINKS_MYSQL_PASSWORD"),
        table=default_table,
        table_routes=[{"subject": "ops.restricted.*", "table": route_table}],
        mode="upsert",
        upsert_update_columns=[],
        auto_create=True,
        metrics=metrics,
    )

    await sink.start()
    try:
        await sink.healthcheck()
        await sink.write_batch(
            [
                _envelope(
                    subject="ops.routine.created",
                    sequence=1,
                    data=b'{"event_id":"ROUTINE-1"}',
                    priority="routine",
                    classification="NATO UNCLASSIFIED",
                    labels=("test", "routine"),
                ),
                _envelope(
                    subject="ops.restricted.created",
                    sequence=2,
                    data=b"encrypted-text:v1:ciphertext",
                    priority="urgent",
                    classification="NATO SECRET",
                    labels=("sensor", "restricted"),
                ),
                _envelope(
                    subject="ops.routine.empty",
                    sequence=3,
                    data=b"",
                ),
            ]
        )
        await sink.write_batch(
            [
                _envelope(
                    subject="ops.routine.created",
                    sequence=1,
                    data=b'{"event_id":"ROUTINE-1-duplicate"}',
                    priority="urgent",
                    classification="NATO SECRET",
                    labels=("duplicate",),
                )
            ]
        )
    finally:
        await sink.stop()

    assert _count_rows(default_table) == 2
    assert _count_rows(route_table) == 1
    priority, classification, labels, payload_json = _select_one(default_table)
    assert priority == "routine"
    assert classification == "NATO UNCLASSIFIED"
    assert labels == "test;routine"
    assert "ROUTINE-1" in payload_json
    assert metrics.counters[MetricNames.MYSQL_UPSERT_ROWS_TOTAL] == 4
