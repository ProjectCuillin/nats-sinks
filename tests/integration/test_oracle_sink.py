# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.oracle import OracleSink
from nats_sinks.oracle.sql import validate_identifier

DEFAULT_ORACLE_TEST_TABLE = "NATS_SINKS_ORACLE_TEST_EVENTS_V2"
REQUIRED_ORACLE_TEST_COLUMNS = {
    "STREAM_NAME",
    "STREAM_SEQUENCE",
    "SUBJECT",
    "MESSAGE_ID",
    "MESSAGE_CREATED_AT_EPOCH_NS",
    "JETSTREAM_TIMESTAMP_EPOCH_NS",
    "RECEIVED_AT_EPOCH_NS",
    "STORED_AT_EPOCH_NS",
    "PAYLOAD_JSON",
    "HEADERS_JSON",
    "METADATA_JSON",
}


def _oracle_integration_enabled() -> bool:
    return os.getenv("NATS_SINKS_ORACLE_INTEGRATION") == "1"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _oracle_integration_enabled(),
        reason="set NATS_SINKS_ORACLE_INTEGRATION=1 to run Oracle integration tests",
    ),
]


def _setting(name: str, fallback: str | None = None) -> str | None:
    return os.getenv(f"NATS_SINKS_ORACLE_{name}", fallback)


def _oracle_sink(*, table: str) -> OracleSink:
    dsn = _setting("DSN")
    user = _setting("USER")
    password_env = _setting("PASSWORD_ENV", "ORACLE_PASSWORD")
    if not dsn or not user:
        pytest.skip("NATS_SINKS_ORACLE_DSN and NATS_SINKS_ORACLE_USER are required")
    if not password_env or os.getenv(password_env) is None:
        pytest.skip(f"{password_env or 'ORACLE_PASSWORD'} must contain the Oracle password")

    return OracleSink(
        dsn=dsn,
        user=user,
        password_env=password_env,
        config_dir=_setting("CONFIG_DIR"),
        wallet_location=_setting("WALLET_LOCATION"),
        wallet_password_env=_setting("WALLET_PASSWORD_ENV"),
        ssl_server_dn_match=_bool_setting("SSL_SERVER_DN_MATCH"),
        ssl_server_cert_dn=_setting("SSL_SERVER_CERT_DN"),
        tcp_connect_timeout=_float_setting("TCP_CONNECT_TIMEOUT"),
        retry_count=_int_setting("RETRY_COUNT"),
        retry_delay=_int_setting("RETRY_DELAY"),
        https_proxy=_setting("HTTPS_PROXY"),
        https_proxy_port=_int_setting("HTTPS_PROXY_PORT"),
        table=table,
        mode="merge",
        auto_create=True,
    )


def _bool_setting(name: str) -> bool | None:
    value = _setting(name)
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def _int_setting(name: str) -> int | None:
    value = _setting(name)
    return int(value) if value else None


def _float_setting(name: str) -> float | None:
    value = _setting(name)
    return float(value) if value else None


def _bool_env_setting(name: str, *, default: bool = False) -> bool:
    value = _setting(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _test_table() -> str:
    return validate_identifier(
        _setting("TABLE", DEFAULT_ORACLE_TEST_TABLE) or DEFAULT_ORACLE_TEST_TABLE
    )


def _envelope(
    *,
    stream: str,
    sequence: int = 1,
    data: bytes = b'{"order_id":"O-IT-1001","amount":42.5}',
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="orders.created",
        data=data,
        headers={"Nats-Msg-Id": f"{stream}-{sequence}"},
        stream=stream,
        consumer="oracle-integration",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def _count_rows(pool: Any, *, table: str, stream: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select count(*) from {table_name} where stream_name = :stream_name"  # noqa: S608
            cursor.execute(sql, {"stream_name": stream})
            row: Mapping[int, Any] | tuple[Any, ...] | None = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _count_text_payload_envelopes(pool: Any, *, table: str, stream: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select count(*) from {table_name} where stream_name = :stream_name and json_value(payload_json, '$._nats_sinks.payload_format') = 'text'"  # noqa: E501, S608
            cursor.execute(sql, {"stream_name": stream})
            row: Mapping[int, Any] | tuple[Any, ...] | None = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _drop_table(pool: Any, *, table: str) -> None:
    table_name = validate_identifier(table)
    with suppress(Exception):
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                # The table name is allow-list validated and points to an
                # explicitly configured integration-test table.
                cursor.execute(f"drop table {table_name} purge")
            connection.commit()


def _table_columns(pool: Any, *, table: str) -> set[str]:
    """Return the current columns for the retained integration-test table."""

    table_name = validate_identifier(table)
    parts = table_name.split(".")
    if len(parts) == 2:
        sql = (
            "select column_name from all_tab_columns "
            "where owner = :owner_name and table_name = :table_name"
        )
        binds = {"owner_name": parts[0], "table_name": parts[1]}
    else:
        sql = "select column_name from user_tab_columns where table_name = :table_name"
        binds = {"table_name": parts[0]}

    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, binds)
            return {str(row[0]).upper() for row in cursor.fetchall()}


async def _assert_current_test_schema(pool: Any, *, table: str) -> None:
    """Fail clearly when a retained test table has an older layout."""

    columns = await asyncio.to_thread(_table_columns, pool, table=table)
    missing = sorted(REQUIRED_ORACLE_TEST_COLUMNS - columns)
    if missing:
        pytest.fail(
            f"Oracle integration table {table!r} is missing required columns {missing}. "
            "Set NATS_SINKS_ORACLE_DROP_TABLE_BEFORE=true for this test table, "
            "or choose a fresh NATS_SINKS_ORACLE_TABLE."
        )


async def _start_sink_for_test(sink: OracleSink, *, table: str) -> None:
    await sink.start()
    if sink._pool is not None and _bool_env_setting("DROP_TABLE_BEFORE"):
        await asyncio.to_thread(_drop_table, sink._pool, table=table)
        await sink.ensure_schema()
    if sink._pool is not None:
        await _assert_current_test_schema(sink._pool, table=table)


async def _stop_sink_for_test(sink: OracleSink, *, table: str) -> None:
    try:
        if sink._pool is not None and _bool_env_setting("DROP_TABLE_AFTER"):
            await asyncio.to_thread(_drop_table, sink._pool, table=table)
    finally:
        await sink.stop()


@pytest.mark.asyncio
async def test_oracle_integration_auto_creates_table_and_writes_batch() -> None:
    table = _test_table()
    stream = f"IT_{uuid.uuid4().hex[:16].upper()}"
    sink = _oracle_sink(table=table)

    await _start_sink_for_test(sink, table=table)
    try:
        await sink.healthcheck()
        await sink.write_batch([_envelope(stream=stream)])
        assert await asyncio.to_thread(_count_rows, sink._pool, table=table, stream=stream) == 1
    finally:
        await _stop_sink_for_test(sink, table=table)


@pytest.mark.asyncio
async def test_oracle_integration_duplicate_redelivery_is_idempotent() -> None:
    table = _test_table()
    stream = f"IT_{uuid.uuid4().hex[:16].upper()}"
    message = _envelope(stream=stream)
    sink = _oracle_sink(table=table)

    await _start_sink_for_test(sink, table=table)
    try:
        await sink.write_batch([message])
        await sink.write_batch([message])
        assert await asyncio.to_thread(_count_rows, sink._pool, table=table, stream=stream) == 1
    finally:
        await _stop_sink_for_test(sink, table=table)


@pytest.mark.asyncio
async def test_oracle_integration_persists_non_json_text_payload() -> None:
    table = _test_table()
    stream = f"IT_{uuid.uuid4().hex[:16].upper()}"
    message = _envelope(stream=stream, data=b"encrypted-text:v1:integration-ciphertext")
    sink = _oracle_sink(table=table)

    await _start_sink_for_test(sink, table=table)
    try:
        await sink.write_batch([message])
        assert await asyncio.to_thread(_count_rows, sink._pool, table=table, stream=stream) == 1
        assert (
            await asyncio.to_thread(
                _count_text_payload_envelopes,
                sink._pool,
                table=table,
                stream=stream,
            )
            == 1
        )
    finally:
        await _stop_sink_for_test(sink, table=table)


@pytest.mark.asyncio
async def test_oracle_integration_persists_empty_payload() -> None:
    table = _test_table()
    stream = f"IT_{uuid.uuid4().hex[:16].upper()}"
    message = _envelope(stream=stream, data=b"")
    sink = _oracle_sink(table=table)

    await _start_sink_for_test(sink, table=table)
    try:
        await sink.write_batch([message])
        assert await asyncio.to_thread(_count_rows, sink._pool, table=table, stream=stream) == 1
        assert (
            await asyncio.to_thread(
                _count_text_payload_envelopes,
                sink._pool,
                table=table,
                stream=stream,
            )
            == 1
        )
    finally:
        await _stop_sink_for_test(sink, table=table)
