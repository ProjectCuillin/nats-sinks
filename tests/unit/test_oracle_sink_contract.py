# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import json
import os
import secrets
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
from nats_sinks.core.config import EncryptionConfig
from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY, PayloadEncryptor
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
        consumer="oracle",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def encryption_config() -> EncryptionConfig:
    configured = os.getenv("NATS_SINKS_TEST_ENCRYPTION_KEY_B64")
    key_b64 = configured or base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return EncryptionConfig(
        enabled=True,
        algorithm="aes-256-gcm",
        key_id="oracle-sink-test-key",
        key_b64=key_b64,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.statement_binds: list[tuple[str, dict[str, Any] | None]] = []
        self.executions: list[tuple[str, list[dict[str, Any]]]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, binds: dict[str, Any] | None = None) -> None:
        self.statements.append(sql)
        self.statement_binds.append((sql, binds))

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        self.executions.append((sql, rows))


class DuplicateReportingCursor(FakeCursor):
    """Cursor test double that reports fewer affected rows than attempted."""

    def __init__(self, rowcount: int) -> None:
        super().__init__()
        self.rowcount = rowcount


class ExecutemanyFailingCursor(FakeCursor):
    """Cursor test double that fails after connection preparation has succeeded."""

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        super().executemany(sql, rows)
        raise RuntimeError("ORA-00904: invalid identifier")


class RecordingConnection:
    def __init__(self, cursor: FakeCursor | None = None) -> None:
        self.cursor_instance = cursor or FakeCursor()
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class RecordingPool:
    def __init__(self, connection: RecordingConnection | None = None) -> None:
        self.connection = connection or RecordingConnection()

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
    metrics = InMemoryMetrics()
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        metrics=metrics,
    )
    sink._pool = object()

    def raise_duplicate(rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        del rows_by_table
        raise RuntimeError("ORA-00001: unique constraint violated")

    sink._write_rows_sync = raise_duplicate  # type: ignore[method-assign]

    await sink.write_batch([envelope()])

    assert metrics.counters[MetricNames.ORACLE_CONFLICTS_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL] == 1


@pytest.mark.asyncio
async def test_oracle_insert_ignore_rowcount_reports_ignored_duplicates() -> None:
    metrics = InMemoryMetrics()
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        metrics=metrics,
    )
    cursor = DuplicateReportingCursor(rowcount=1)
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.ORACLE_CONFLICTS_TOTAL] == 0
    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL] == 1
    assert sink._duplicate_ignored_count(cursor, [{}]) == 0


@pytest.mark.asyncio
async def test_oracle_merge_records_unknown_outcome_metrics() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=2)
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.ORACLE_MERGE_ROWS_TOTAL] == 2
    assert metrics.counters[MetricNames.ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL] == 2
    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 0


@pytest.mark.asyncio
async def test_oracle_merge_without_update_columns_reports_noop_duplicates() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=1)
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        merge_update_columns=[],
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    statement = cursor.executions[0][0]
    assert "when matched then update" not in statement
    assert metrics.counters[MetricNames.ORACLE_MERGE_ROWS_TOTAL] == 2
    assert metrics.counters[MetricNames.ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL] == 0
    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL] == 1


def test_oracle_merge_update_columns_apply_only_to_merge_mode() -> None:
    with pytest.raises(ConfigurationError, match="merge_update_columns"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="insert_ignore",
            merge_update_columns=[],
        )


@pytest.mark.asyncio
async def test_oracle_plain_insert_duplicate_records_conflict_and_raises() -> None:
    metrics = InMemoryMetrics()
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert",
        metrics=metrics,
    )
    sink._pool = object()

    def raise_duplicate(rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        del rows_by_table
        raise RuntimeError("ORA-00001: unique constraint violated")

    sink._write_rows_sync = raise_duplicate  # type: ignore[method-assign]

    with pytest.raises(PermanentSinkError, match="duplicate key"):
        await sink.write_batch([envelope()])

    assert metrics.counters[MetricNames.ORACLE_CONFLICTS_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 0
    assert metrics.counters[MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL] == 0


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


def test_oracle_staging_requires_explicit_table() -> None:
    with pytest.raises(ConfigurationError, match=r"sink\.staging\.table"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="merge",
            staging={"enabled": True},
        )


def test_oracle_staging_rejects_append_mode() -> None:
    with pytest.raises(ConfigurationError, match=r"sink\.staging\.enabled requires"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="append",
            staging={"enabled": True, "table": "NATS_SINK_EVENTS_STAGE"},
        )


@pytest.mark.asyncio
async def test_oracle_staging_merge_loads_stage_merges_cleans_and_commits() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        staging={"enabled": True, "table": "NATS_SINK_EVENTS_STAGE"},
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    cursor = pool.connection.cursor_instance
    assert cursor.executions[0][0].startswith("insert into NATS_SINK_EVENTS_STAGE")
    staging_rows = cursor.executions[0][1]
    assert len(staging_rows) == 2
    batch_ids = {row["nats_sinks_batch_id"] for row in staging_rows}
    assert len(batch_ids) == 1
    assert any(sql.startswith("merge into NATS_SINK_EVENTS target") for sql in cursor.statements)
    assert any(sql.startswith("delete from NATS_SINK_EVENTS_STAGE") for sql in cursor.statements)
    merge_binds = [
        binds
        for sql, binds in cursor.statement_binds
        if sql.startswith("merge into NATS_SINK_EVENTS target")
    ]
    assert merge_binds == [{"nats_sinks_batch_id": next(iter(batch_ids))}]
    assert pool.connection.committed
    assert not pool.connection.rolled_back


@pytest.mark.asyncio
async def test_oracle_staging_insert_ignore_reports_duplicate_metrics() -> None:
    metrics = InMemoryMetrics()
    cursor = DuplicateReportingCursor(rowcount=1)
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        staging={"enabled": True, "table": "NATS_SINK_EVENTS_STAGE"},
        metrics=metrics,
    )
    sink._pool = RecordingPool(RecordingConnection(cursor))

    await sink.write_batch(
        [envelope_with_subject("orders.created", 1), envelope_with_subject("orders.created", 2)]
    )

    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 1
    assert metrics.counters[MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL] == 1


@pytest.mark.asyncio
async def test_oracle_staging_keep_cleanup_leaves_stage_rows_for_operator_review() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        staging={"enabled": True, "table": "NATS_SINK_EVENTS_STAGE", "cleanup": "keep"},
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch([envelope_with_subject("orders.created", 1)])

    assert not any(
        sql.startswith("delete from NATS_SINK_EVENTS_STAGE")
        for sql in pool.connection.cursor_instance.statements
    )
    assert pool.connection.committed


@pytest.mark.asyncio
async def test_oracle_staging_write_failure_rolls_back_without_commit() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        staging={"enabled": True, "table": "NATS_SINK_EVENTS_STAGE"},
    )
    connection = RecordingConnection(ExecutemanyFailingCursor())
    sink._pool = RecordingPool(connection)

    with pytest.raises(PermanentSinkError, match="invalid Oracle identifier"):
        await sink.write_batch([envelope_with_subject("orders.created", 1)])

    assert connection.rolled_back
    assert not connection.committed


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


@pytest.mark.parametrize(
    ("raw_error", "expected"),
    [
        (
            "ORA-00904: invalid identifier",
            "configured Oracle table may be missing columns expected by nats-sinks",
        ),
        (
            "ORA-00942: table or view does not exist",
            "table or view is not available to the runtime user",
        ),
        (
            "ORA-01017: invalid username/password; logon denied",
            "authentication failed",
        ),
    ],
)
def test_oracle_translation_explains_common_operator_configuration_errors(
    raw_error: str,
    expected: str,
) -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
    )

    translated = sink._translate_exception(RuntimeError(raw_error), "Oracle batch write failed")

    assert isinstance(translated, PermanentSinkError)
    assert expected in str(translated)
    assert "example" not in str(translated)


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
async def test_oracle_route_can_override_idempotency_to_message_id() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        table_routes=[
            {
                "subject": "orders.by-id",
                "table": "ORDER_MESSAGE_ID_EVENTS",
                "idempotency": {"strategy": "message_id", "columns": ["MESSAGE_ID"]},
            }
        ],
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch(
        [
            envelope_with_subject("orders.updated", 1),
            envelope_with_subject("orders.by-id", 2),
        ]
    )

    executions = pool.connection.cursor_instance.executions
    default_sql = next(sql for sql, _rows in executions if "NATS_SINK_EVENTS" in sql)
    routed_sql, routed_rows = next(
        (sql, rows) for sql, rows in executions if "ORDER_MESSAGE_ID_EVENTS" in sql
    )
    assert "target.STREAM_NAME = source.STREAM_NAME" in default_sql
    assert "target.STREAM_SEQUENCE = source.STREAM_SEQUENCE" in default_sql
    assert "target.MESSAGE_ID = source.MESSAGE_ID" in routed_sql
    assert routed_rows[0]["message_id"] == "m-2"


@pytest.mark.asyncio
async def test_oracle_route_can_override_idempotency_to_payload_field() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
        table_routes=[
            {
                "subject": "orders.payload-key",
                "table": "ORDER_PAYLOAD_KEY_EVENTS",
                "idempotency": {
                    "strategy": "payload_field",
                    "payload_field": "order_id",
                    "columns": ["MESSAGE_ID"],
                },
                "merge_update_columns": [],
            }
        ],
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch([envelope_with_subject("orders.payload-key", 3)])

    routed_sql, routed_rows = pool.connection.cursor_instance.executions[0]
    assert "ORDER_PAYLOAD_KEY_EVENTS" in routed_sql
    assert "target.MESSAGE_ID = source.MESSAGE_ID" in routed_sql
    assert "when matched then update" not in routed_sql
    assert routed_rows[0]["message_id"] == "O-1001"


@pytest.mark.asyncio
async def test_oracle_insert_ignore_route_uses_route_specific_key() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
        table_routes=[
            {
                "subject": "orders.by-id",
                "table": "ORDER_MESSAGE_ID_EVENTS",
                "idempotency": {"strategy": "message_id", "columns": ["MESSAGE_ID"]},
            }
        ],
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch([envelope_with_subject("orders.by-id", 4)])

    routed_sql, routed_rows = pool.connection.cursor_instance.executions[0]
    assert "ORDER_MESSAGE_ID_EVENTS" in routed_sql
    assert "target.MESSAGE_ID = source.MESSAGE_ID" in routed_sql
    assert "when matched then update" not in routed_sql
    assert routed_rows[0]["message_id"] == "m-4"


def test_oracle_rejects_conflicting_policies_for_same_table() -> None:
    with pytest.raises(ConfigurationError, match="conflicting Oracle idempotency policy"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="merge",
            table_routes=[
                {
                    "subject": "orders.by-id",
                    "table": "SHARED_EVENTS",
                    "idempotency": {"strategy": "message_id", "columns": ["MESSAGE_ID"]},
                },
                {"subject": "orders.default-key", "table": "SHARED_EVENTS"},
            ],
        )


def test_oracle_route_merge_update_columns_apply_only_to_merge_mode() -> None:
    with pytest.raises(ConfigurationError, match=r"table_routes\[\]\.merge_update_columns"):
        OracleSink(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local test placeholder
            table="NATS_SINK_EVENTS",
            mode="insert_ignore",
            table_routes=[
                {
                    "subject": "orders.by-id",
                    "table": "ORDER_MESSAGE_ID_EVENTS",
                    "merge_update_columns": [],
                }
            ],
        )


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
    assert rows[0]["priority"] == "urgent"
    assert rows[0]["classification"] == "restricted"
    assert rows[0]["labels"] == "billing;urgent"


@pytest.mark.asyncio
async def test_oracle_sink_stores_core_encrypted_payload_as_decryptable_json() -> None:
    config = encryption_config()
    encryptor = PayloadEncryptor(config)
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="merge",
    )
    pool = RecordingPool()
    sink._pool = pool

    await sink.write_batch([encryptor.encrypt_envelope(envelope())])

    rows = pool.connection.cursor_instance.executions[0][1]
    payload = json.loads(rows[0]["payload_json"])
    assert ENCRYPTED_PAYLOAD_KEY in payload
    assert encryptor.decrypt_payload(payload) == b'{"order_id":"O-1001"}'
    assert json.loads(rows[0]["headers_json"])["Nats-Msg-Id"] == "m-1"


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
