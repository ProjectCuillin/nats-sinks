# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.oracle import OracleSink
from nats_sinks.oracle.sql import validate_identifier
from nats_sinks.testing.disconnected_spool_replay import (
    DisconnectedSpoolReplayOptions,
    run_disconnected_spool_replay_certification,
)

DEFAULT_ORACLE_TEST_TABLE = "NATS_SINKS_ORACLE_TEST_EVENTS_V2"
REQUIRED_ORACLE_TEST_COLUMNS = {
    "STREAM_NAME",
    "STREAM_SEQUENCE",
    "SUBJECT",
    "MESSAGE_ID",
    "PRIORITY",
    "CLASSIFICATION",
    "MESSAGE_CREATED_AT_EPOCH_NS",
    "JETSTREAM_TIMESTAMP_EPOCH_NS",
    "RECEIVED_AT_EPOCH_NS",
    "STORED_AT_EPOCH_NS",
    "PAYLOAD_JSON",
    "HEADERS_JSON",
    "METADATA_JSON",
    "MISSION_METADATA_JSON",
}


def _oracle_integration_enabled() -> bool:
    return os.getenv("NATS_SINKS_ORACLE_INTEGRATION") == "1"


def _disconnected_replay_enabled() -> bool:
    return os.getenv("NATS_SINKS_ORACLE_DISCONNECTED_REPLAY") == "1"


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


def _unreachable_oracle_sink(*, table: str) -> OracleSink:
    user = _setting("USER")
    password_env = _setting("PASSWORD_ENV", "ORACLE_PASSWORD")
    if not user:
        pytest.skip("NATS_SINKS_ORACLE_USER is required")
    if not password_env or os.getenv(password_env) is None:
        pytest.skip(f"{password_env or 'ORACLE_PASSWORD'} must contain the Oracle password")
    return OracleSink(
        dsn=_setting("UNREACHABLE_DSN", "127.0.0.1:1/UNREACHABLE") or "127.0.0.1:1/UNREACHABLE",
        user=user,
        password_env=password_env,
        tcp_connect_timeout=1.0,
        retry_count=0,
        retry_delay=0,
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
    priority: str | None = None,
    classification: str | None = None,
    labels: tuple[str, ...] = (),
    mission_metadata: dict[str, object] | None = None,
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
        priority=priority,
        classification=classification,
        labels=labels,
        mission_metadata=mission_metadata,
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


def _count_distinct_rows(pool: Any, *, table: str, stream: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            sql = (
                f"select count(distinct stream_sequence) from {table_name} "  # noqa: S608
                "where stream_name = :stream_name"
            )
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


def _message_metadata_values(
    pool: Any, *, table: str, stream: str
) -> tuple[str | None, str | None, str | None]:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select priority, classification, labels from {table_name} where stream_name = :stream_name"  # noqa: E501, S608
            cursor.execute(sql, {"stream_name": stream})
            row: Mapping[int, Any] | tuple[Any, ...] | None = cursor.fetchone()
    if row is None:
        return (None, None, None)
    return (
        None if row[0] is None else str(row[0]),
        None if row[1] is None else str(row[1]),
        None if row[2] is None else str(row[2]),
    )


def _mission_metadata_profile(pool: Any, *, table: str, stream: str) -> str | None:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            sql = f"select json_value(mission_metadata_json, '$.profile') from {table_name} where stream_name = :stream_name"  # noqa: E501, S608
            cursor.execute(sql, {"stream_name": stream})
            row: Mapping[int, Any] | tuple[Any, ...] | None = cursor.fetchone()
    if row is None:
        return None
    return None if row[0] is None else str(row[0])


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


async def _start_sink_for_verification(sink: OracleSink, *, table: str) -> None:
    """Open an Oracle sink for final assertions without destructive setup."""

    await sink.start()
    if sink._pool is not None:
        await _assert_current_test_schema(sink._pool, table=table)


class OracleDisconnectedReplayBackend:
    """Adapter used by the disconnected spool-and-replay certification."""

    name = "Oracle Database"

    def __init__(self, *, table: str) -> None:
        self.table = table
        self.last_sink: OracleSink | None = None

    def reachable_sink(self) -> OracleSink:
        sink = _oracle_sink(table=self.table)
        self.last_sink = sink
        return sink

    def unreachable_sink(self) -> OracleSink:
        return _unreachable_oracle_sink(table=self.table)

    async def assert_expected_records(self, messages: Sequence[NatsEnvelope]) -> None:
        assert messages
        stream = messages[0].stream
        assert stream is not None
        sink = self.last_sink
        if sink is None or sink._pool is None:
            sink = _oracle_sink(table=self.table)
            await _start_sink_for_verification(sink, table=self.table)
            close_after = True
        else:
            close_after = False
        try:
            assert await asyncio.to_thread(
                _count_rows, sink._pool, table=self.table, stream=stream
            ) == len(messages)
            assert await asyncio.to_thread(
                _count_distinct_rows,
                sink._pool,
                table=self.table,
                stream=stream,
            ) == len(messages)
        finally:
            if close_after:
                await sink.stop()


@pytest.mark.asyncio
async def test_oracle_integration_auto_creates_table_and_writes_batch() -> None:
    table = _test_table()
    stream = f"IT_{uuid.uuid4().hex[:16].upper()}"
    sink = _oracle_sink(table=table)

    await _start_sink_for_test(sink, table=table)
    try:
        await sink.healthcheck()
        await sink.write_batch(
            [
                _envelope(
                    stream=stream,
                    priority="urgent",
                    classification="restricted",
                    labels=("billing", "urgent"),
                    mission_metadata={
                        "profile": "mission-event-v1",
                        "mission_id": "integration-test",
                    },
                )
            ]
        )
        assert await asyncio.to_thread(_count_rows, sink._pool, table=table, stream=stream) == 1
        assert await asyncio.to_thread(
            _message_metadata_values,
            sink._pool,
            table=table,
            stream=stream,
        ) == ("urgent", "restricted", "billing;urgent")
        assert (
            await asyncio.to_thread(
                _mission_metadata_profile,
                sink._pool,
                table=table,
                stream=stream,
            )
            == "mission-event-v1"
        )
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


@pytest.mark.skipif(
    not (_oracle_integration_enabled() and _disconnected_replay_enabled()),
    reason=(
        "set NATS_SINKS_ORACLE_INTEGRATION=1 and "
        "NATS_SINKS_ORACLE_DISCONNECTED_REPLAY=1 to run Oracle Database "
        "disconnected replay certification"
    ),
)
@pytest.mark.asyncio
async def test_oracle_integration_disconnected_spool_replay_certification(
    tmp_path: Path,
) -> None:
    """Certify Oracle Database replay after local spool custody."""

    table = validate_identifier(f"{_test_table()}_DISC")
    stream = f"ORACLE_DISC_{uuid.uuid4().hex[:12].upper()}"
    sink = _oracle_sink(table=table)
    await _start_sink_for_test(sink, table=table)
    await _stop_sink_for_test(sink, table=table)

    report = await run_disconnected_spool_replay_certification(
        OracleDisconnectedReplayBackend(table=table),
        spool_directory=tmp_path / "spool",
        options=DisconnectedSpoolReplayOptions(stream=stream),
    )

    assert report.backend == "Oracle Database"
    assert report.expected_backend_records == 3003
    assert report.spool_remaining_records == 0
    assert report.outage_proved is True


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
