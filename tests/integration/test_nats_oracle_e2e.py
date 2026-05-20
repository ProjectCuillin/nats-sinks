# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import json
import os
import ssl
import uuid
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import nats
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
from nats.js.errors import NotFoundError

from nats_sinks.core.config import DeliveryConfig, EncryptionConfig
from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY, PayloadEncryptor
from nats_sinks.core.metrics import InMemoryMetrics
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.oracle import OracleSink
from nats_sinks.oracle.routing import matches_subject
from nats_sinks.oracle.sql import validate_identifier

DEFAULT_E2E_MESSAGE_COUNT = 256
DEFAULT_E2E_BATCH_SIZE = 64
DEFAULT_TEXT_PAYLOAD_INTERVAL = 17
DEFAULT_EMPTY_PAYLOAD_INTERVAL = 31
DEFAULT_MISSING_MESSAGE_ID_INTERVAL = 23
DEFAULT_EXPECTED_STREAM_HEADER_INTERVAL = 29
DEFAULT_E2E_TEST_TABLE = "NATS_SINKS_E2E_EVENTS_V2"
MESSAGE_METADATA_PATTERN_SIZE = 4
REQUIRED_E2E_COLUMNS = {
    "STREAM_NAME",
    "STREAM_SEQUENCE",
    "SUBJECT",
    "MESSAGE_ID",
    "PRIORITY",
    "CLASSIFICATION",
    "LABELS",
    "MESSAGE_CREATED_AT_EPOCH_NS",
    "JETSTREAM_TIMESTAMP_EPOCH_NS",
    "RECEIVED_AT_EPOCH_NS",
    "STORED_AT_EPOCH_NS",
    "PAYLOAD_JSON",
    "HEADERS_JSON",
    "METADATA_JSON",
}


@dataclass(frozen=True)
class E2ECase:
    nats_url: str
    stream: str
    subject: str
    publish_subject: str
    consumer: str
    table: str
    message_count: int
    batch_size: int
    run_id: str
    text_payload_interval: int
    empty_payload_interval: int
    missing_message_id_interval: int
    expected_stream_header_interval: int
    expected_text_payloads: int
    expected_empty_payloads: int
    expected_message_ids: int
    expected_stream_headers: int
    expected_priorities: int
    expected_classifications: int
    expected_labels: int
    expected_both_message_metadata: int
    expected_batch_count: int
    expected_final_batch_size: int
    drop_table_before: bool
    drop_table_after: bool
    encryption: EncryptionConfig | None


def _e2e_enabled() -> bool:
    return os.getenv("NATS_SINKS_E2E_INTEGRATION") == "1"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _e2e_enabled(),
        reason="set NATS_SINKS_E2E_INTEGRATION=1 to run NATS-to-Oracle e2e tests",
    ),
]


def _e2e_setting(name: str, fallback: str | None = None) -> str | None:
    return os.getenv(f"NATS_SINKS_E2E_{name}", fallback)


def _oracle_setting(name: str, fallback: str | None = None) -> str | None:
    return os.getenv(f"NATS_SINKS_ORACLE_{name}", fallback)


def _required(value: str | None, name: str) -> str:
    if value:
        return value
    pytest.skip(f"{name} is required")


def _bool_setting(prefix: str, name: str) -> bool | None:
    value = os.getenv(f"{prefix}_{name}")
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def _int_setting(prefix: str, name: str) -> int | None:
    value = os.getenv(f"{prefix}_{name}")
    return int(value) if value else None


def _float_setting(prefix: str, name: str) -> float | None:
    value = os.getenv(f"{prefix}_{name}")
    return float(value) if value else None


def _e2e_int(name: str, fallback: int) -> int:
    value = _e2e_setting(name)
    if value is None:
        return fallback
    parsed = int(value)
    if parsed < 1:
        pytest.fail(f"NATS_SINKS_E2E_{name} must be greater than zero")
    return parsed


def _e2e_bool(name: str, *, default: bool = False) -> bool:
    value = _e2e_setting(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _e2e_encryption_config() -> EncryptionConfig | None:
    if not _e2e_bool("ENCRYPTION_ENABLED"):
        return None
    key_env = _e2e_setting("ENCRYPTION_KEY_B64_ENV", "NATS_SINKS_E2E_ENCRYPTION_KEY_B64")
    _required(os.getenv(key_env or ""), key_env or "NATS_SINKS_E2E_ENCRYPTION_KEY_B64")
    return EncryptionConfig(
        enabled=True,
        algorithm=_e2e_setting("ENCRYPTION_ALGORITHM", "aes-256-gcm") or "aes-256-gcm",
        key_id=_e2e_setting("ENCRYPTION_KEY_ID", "nats-sinks-e2e-key") or "nats-sinks-e2e-key",
        key_b64_env=key_env,
    )


def _nats_options() -> dict[str, Any]:
    password_env = _e2e_setting("NATS_PASSWORD_ENV", "NATS_PASSWORD")
    token_env = _e2e_setting("NATS_TOKEN_ENV")
    tls_ca_file = _e2e_setting("NATS_TLS_CA_FILE")
    options: dict[str, Any] = {
        "name": "nats-sinks-e2e-test",
        "connect_timeout": 5,
        "allow_reconnect": False,
    }

    user = _e2e_setting("NATS_USER")
    if user:
        options["user"] = user
        options["password"] = _required(
            os.getenv(password_env or ""), password_env or "NATS_PASSWORD"
        )
    elif token_env:
        options["token"] = _required(os.getenv(token_env), token_env)

    url = _required(_e2e_setting("NATS_URL"), "NATS_SINKS_E2E_NATS_URL")
    if url.startswith("tls://") or tls_ca_file:
        options["tls"] = ssl.create_default_context(cafile=tls_ca_file)
    return options


def _oracle_sink(table: str) -> OracleSink:
    dsn = _required(_oracle_setting("DSN"), "NATS_SINKS_ORACLE_DSN")
    user = _required(_oracle_setting("USER"), "NATS_SINKS_ORACLE_USER")
    password_env = _oracle_setting("PASSWORD_ENV", "ORACLE_PASSWORD")
    _required(os.getenv(password_env or ""), password_env or "ORACLE_PASSWORD")

    return OracleSink(
        dsn=dsn,
        user=user,
        password_env=password_env,
        config_dir=_oracle_setting("CONFIG_DIR"),
        wallet_location=_oracle_setting("WALLET_LOCATION"),
        wallet_password_env=_oracle_setting("WALLET_PASSWORD_ENV"),
        ssl_server_dn_match=_bool_setting("NATS_SINKS_ORACLE", "SSL_SERVER_DN_MATCH"),
        ssl_server_cert_dn=_oracle_setting("SSL_SERVER_CERT_DN"),
        tcp_connect_timeout=_float_setting("NATS_SINKS_ORACLE", "TCP_CONNECT_TIMEOUT"),
        retry_count=_int_setting("NATS_SINKS_ORACLE", "RETRY_COUNT"),
        retry_delay=_int_setting("NATS_SINKS_ORACLE", "RETRY_DELAY"),
        https_proxy=_oracle_setting("HTTPS_PROXY"),
        https_proxy_port=_int_setting("NATS_SINKS_ORACLE", "HTTPS_PROXY_PORT"),
        table=table,
        mode="merge",
        auto_create=True,
    )


async def _ensure_stream(js: Any, *, stream: str, subject: str) -> None:
    try:
        info = await js.stream_info(stream)
    except NotFoundError:
        await js.add_stream(
            config=StreamConfig(
                name=stream,
                subjects=[subject],
                max_msgs=1000,
                max_age=24 * 60 * 60,
            )
        )
        return

    subjects = info.config.subjects or []
    if not any(matches_subject(pattern, subject) for pattern in subjects):
        pytest.fail(f"stream {stream!r} does not include test subject {subject!r}")


async def _prepare_consumer(
    js: Any,
    *,
    stream: str,
    subject: str,
    consumer: str,
    max_ack_pending: int,
) -> None:
    with suppress(Exception):
        await js.delete_consumer(stream, consumer)
    await js.add_consumer(
        stream,
        config=ConsumerConfig(
            durable_name=consumer,
            deliver_policy=DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=30,
            max_ack_pending=max_ack_pending,
            filter_subject=subject,
        ),
    )


def _run_filter_sql(table_name: str) -> str:
    return (
        f"from {table_name} "
        """where json_value(
            metadata_json,
            '$."headers"."Nats-Sinks-E2E-Run-Id"'
        ) = :run_id"""
    )


def _row_summary_by_run_id(pool: Any, *, table: str, run_id: str) -> tuple[int, int]:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select count(*), count(distinct message_id) {_run_filter_sql(table_name)}"
            cursor.execute(sql, {"run_id": run_id})
            row = cursor.fetchone()
    if row is None:
        return (0, 0)
    return (int(row[0]), int(row[1]))


def _text_payload_count_by_run_id(pool: Any, *, table: str, run_id: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select count(*) {_run_filter_sql(table_name)} and json_value(payload_json, '$._nats_sinks.payload_format') = 'text'"  # noqa: E501
            cursor.execute(sql, {"run_id": run_id})
            row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _empty_payload_count_by_run_id(pool: Any, *, table: str, run_id: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = f"select count(*) {_run_filter_sql(table_name)} and json_value(payload_json, '$._nats_sinks.size_bytes' returning number) = 0"  # noqa: E501
            cursor.execute(sql, {"run_id": run_id})
            row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _expected_stream_header_count_by_run_id(
    pool: Any,
    *,
    table: str,
    run_id: str,
    stream: str,
) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            # The table name is allow-list validated; data remains bind values.
            sql = (
                f"select count(*) {_run_filter_sql(table_name)} "
                """and json_value(
                    metadata_json,
                    '$."nats"."reserved_headers"."Nats-Expected-Stream"'
                ) = :stream"""
            )
            cursor.execute(
                sql,
                {
                    "run_id": run_id,
                    "stream": stream,
                },
            )
            row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _encrypted_payload_count_by_run_id(pool: Any, *, table: str, run_id: str) -> int:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            sql = f"select count(*) {_run_filter_sql(table_name)} and json_value(payload_json, '$._nats_sinks_encryption.schema') = :schema"  # noqa: E501
            cursor.execute(
                sql,
                {
                    "run_id": run_id,
                    "schema": "nats_sinks.encrypted_payload.v1",
                },
            )
            row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _message_metadata_counts_by_run_id(
    pool: Any, *, table: str, run_id: str
) -> tuple[int, int, int, int]:
    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            sql = (
                "select "
                "sum(case when priority is not null then 1 else 0 end), "
                "sum(case when classification is not null then 1 else 0 end), "
                "sum(case when labels is not null then 1 else 0 end), "
                "sum(case when priority is not null and classification is not null then 1 else 0 end) "  # noqa: E501
                f"{_run_filter_sql(table_name)}"
            )
            cursor.execute(sql, {"run_id": run_id})
            row = cursor.fetchone()
    if row is None:
        return (0, 0, 0, 0)
    return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))


def _payload_rows_by_run_id(pool: Any, *, table: str, run_id: str) -> list[tuple[int, str]]:
    def json_safe(value: object) -> object:
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [json_safe(item) for item in value]
        return value

    def as_text(value: object) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"))
        reader = getattr(value, "read", None)
        if callable(reader):
            return str(reader())
        return str(value)

    table_name = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            sql = (
                "select json_value(metadata_json, "
                """'$."headers"."Nats-Sinks-E2E-Index"' returning number), payload_json """
                f"{_run_filter_sql(table_name)} order by 1"
            )
            cursor.execute(sql, {"run_id": run_id})
            return [(int(row[0]), as_text(row[1])) for row in cursor.fetchall()]


def _drop_table(pool: Any, *, table: str) -> None:
    table_name = validate_identifier(table)
    with suppress(Exception):
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                # The table name is allow-list validated and generated for this
                # e2e run, so cleanup is scoped to the test table only.
                cursor.execute(f"drop table {table_name} purge")
            connection.commit()


def _table_columns(pool: Any, *, table: str) -> set[str]:
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


def _print_timings_if_requested(*, metrics: InMemoryMetrics, message_count: int) -> None:
    if _e2e_setting("PRINT_TIMINGS", "false").lower() not in {"1", "true", "yes", "on"}:
        return
    observations = metrics.observations.get("batch_write_seconds", [])
    total = sum(observations)
    batches = len(observations)
    rate = message_count / total if total > 0 else 0.0
    print(  # noqa: T201 - explicit operator-requested timing output for live integration tests.
        "backend_write_timing "
        f"messages={message_count} batches={batches} "
        f"total_seconds={total:.6f} messages_per_second={rate:.2f}"
    )


def _e2e_payload(
    *,
    run_id: str,
    message_id: str,
    index: int,
    text_interval: int,
    empty_interval: int,
) -> bytes:
    if index % empty_interval == 0:
        return b""
    if index % text_interval == 0:
        return f"encrypted-text:v1:{run_id}:{index:06d}".encode()
    return json.dumps(
        {
            "e2e_id": message_id,
            "run_id": run_id,
            "index": index,
            "source": "nats-sinks-e2e-test",
        },
        separators=(",", ":"),
    ).encode("utf-8")


async def _publish_e2e_messages(
    js: Any,
    *,
    stream: str,
    subject: str,
    run_id: str,
    message_count: int,
    text_interval: int,
    empty_interval: int,
    missing_message_id_interval: int,
    expected_stream_header_interval: int,
) -> None:
    for index in range(message_count):
        message_id = f"{run_id}-{index:06d}"
        payload = _e2e_payload(
            run_id=run_id,
            message_id=message_id,
            index=index,
            text_interval=text_interval,
            empty_interval=empty_interval,
        )
        headers: dict[str, str] = {"Nats-Sinks-E2E-Run-Id": run_id}
        headers["Nats-Sinks-E2E-Index"] = str(index)
        remainder = index % MESSAGE_METADATA_PATTERN_SIZE
        if remainder == 0:
            headers["Nats-Sinks-Priority"] = "urgent"
            headers["Nats-Sinks-Classification"] = "restricted"
            headers["Nats-Sinks-Labels"] = "billing;urgent"
        elif remainder == 1:
            headers["Nats-Sinks-Priority"] = "normal"
            headers["Nats-Sinks-Labels"] = "standard"
        elif remainder == 2:
            headers["Nats-Sinks-Classification"] = "internal"
        if index % missing_message_id_interval != 0:
            headers["Nats-Msg-Id"] = message_id
        if index % expected_stream_header_interval == 0:
            headers["Nats-Expected-Stream"] = stream
        await js.publish(
            subject,
            payload,
            headers=headers or None,
        )


def _build_e2e_case() -> E2ECase:
    nats_url = _required(_e2e_setting("NATS_URL"), "NATS_SINKS_E2E_NATS_URL")
    stream = _e2e_setting("STREAM", "NATS_SINKS_E2E") or "NATS_SINKS_E2E"
    subject = _required(_e2e_setting("SUBJECT"), "NATS_SINKS_E2E_SUBJECT")
    publish_subject = _e2e_setting("PUBLISH_SUBJECT", subject) or subject
    consumer = f"nats_sinks_e2e_{uuid.uuid4().hex[:12]}"
    table = validate_identifier(
        _e2e_setting("ORACLE_TABLE") or _oracle_setting("TABLE") or DEFAULT_E2E_TEST_TABLE
    )
    message_count = _e2e_int("MESSAGE_COUNT", DEFAULT_E2E_MESSAGE_COUNT)
    batch_size = min(_e2e_int("BATCH_SIZE", DEFAULT_E2E_BATCH_SIZE), message_count)
    run_id = f"nats-sinks-e2e-{uuid.uuid4().hex}"
    text_payload_interval = _e2e_int("TEXT_PAYLOAD_INTERVAL", DEFAULT_TEXT_PAYLOAD_INTERVAL)
    empty_payload_interval = _e2e_int("EMPTY_PAYLOAD_INTERVAL", DEFAULT_EMPTY_PAYLOAD_INTERVAL)
    missing_message_id_interval = _e2e_int(
        "MISSING_MESSAGE_ID_INTERVAL",
        DEFAULT_MISSING_MESSAGE_ID_INTERVAL,
    )
    expected_stream_header_interval = _e2e_int(
        "EXPECTED_STREAM_HEADER_INTERVAL",
        DEFAULT_EXPECTED_STREAM_HEADER_INTERVAL,
    )
    text_payload_indexes = set(range(0, message_count, text_payload_interval))
    empty_payload_indexes = set(range(0, message_count, empty_payload_interval))
    expected_text_payloads = len(text_payload_indexes | empty_payload_indexes)
    expected_empty_payloads = len(empty_payload_indexes)
    expected_message_ids = message_count - len(range(0, message_count, missing_message_id_interval))
    expected_stream_headers = len(range(0, message_count, expected_stream_header_interval))
    expected_priorities = len(
        [index for index in range(message_count) if index % MESSAGE_METADATA_PATTERN_SIZE in {0, 1}]
    )
    expected_classifications = len(
        [index for index in range(message_count) if index % MESSAGE_METADATA_PATTERN_SIZE in {0, 2}]
    )
    expected_labels = len(
        [index for index in range(message_count) if index % MESSAGE_METADATA_PATTERN_SIZE in {0, 1}]
    )
    expected_both_message_metadata = len(
        [index for index in range(message_count) if index % MESSAGE_METADATA_PATTERN_SIZE == 0]
    )
    expected_batch_count = (message_count + batch_size - 1) // batch_size
    expected_final_batch_size = message_count % batch_size or batch_size
    return E2ECase(
        nats_url=nats_url,
        stream=stream,
        subject=subject,
        publish_subject=publish_subject,
        consumer=consumer,
        table=table,
        message_count=message_count,
        batch_size=batch_size,
        run_id=run_id,
        text_payload_interval=text_payload_interval,
        empty_payload_interval=empty_payload_interval,
        missing_message_id_interval=missing_message_id_interval,
        expected_stream_header_interval=expected_stream_header_interval,
        expected_text_payloads=expected_text_payloads,
        expected_empty_payloads=expected_empty_payloads,
        expected_message_ids=expected_message_ids,
        expected_stream_headers=expected_stream_headers,
        expected_priorities=expected_priorities,
        expected_classifications=expected_classifications,
        expected_labels=expected_labels,
        expected_both_message_metadata=expected_both_message_metadata,
        expected_batch_count=expected_batch_count,
        expected_final_batch_size=expected_final_batch_size,
        drop_table_before=_e2e_bool("DROP_TABLE_BEFORE"),
        drop_table_after=_e2e_bool("DROP_TABLE_AFTER"),
        encryption=_e2e_encryption_config(),
    )


async def _drain_messages(runner: JetStreamSinkRunner, case: E2ECase) -> None:
    if runner._js is None:
        pytest.fail("runner did not create a JetStream context")
    subscription = await runner._js.pull_subscribe(
        case.subject,
        durable=case.consumer,
        stream=case.stream,
    )
    processed = 0
    while processed < case.message_count:
        fetch_size = min(case.batch_size, case.message_count - processed)
        raw_messages = await subscription.fetch(fetch_size, timeout=10)
        await runner.process_raw_batch(raw_messages)
        processed += len(raw_messages)


async def _assert_e2e_rows(
    *,
    sink: OracleSink,
    setup_js: Any,
    metrics: InMemoryMetrics,
    case: E2ECase,
) -> None:
    row_count, distinct_message_ids = await asyncio.to_thread(
        _row_summary_by_run_id,
        sink._pool,
        table=case.table,
        run_id=case.run_id,
    )
    assert row_count == case.message_count
    assert distinct_message_ids == case.expected_message_ids
    if case.encryption is None:
        assert (
            await asyncio.to_thread(
                _text_payload_count_by_run_id,
                sink._pool,
                table=case.table,
                run_id=case.run_id,
            )
            == case.expected_text_payloads
        )
        assert (
            await asyncio.to_thread(
                _empty_payload_count_by_run_id,
                sink._pool,
                table=case.table,
                run_id=case.run_id,
            )
            == case.expected_empty_payloads
        )
    else:
        assert (
            await asyncio.to_thread(
                _encrypted_payload_count_by_run_id,
                sink._pool,
                table=case.table,
                run_id=case.run_id,
            )
            == case.message_count
        )
        rows = await asyncio.to_thread(
            _payload_rows_by_run_id,
            sink._pool,
            table=case.table,
            run_id=case.run_id,
        )
        encryptor = PayloadEncryptor(case.encryption)
        assert len(rows) == case.message_count
        for index, payload_json in rows:
            payload = json.loads(payload_json)
            assert ENCRYPTED_PAYLOAD_KEY in payload
            message_id = f"{case.run_id}-{index:06d}"
            assert encryptor.decrypt_payload(payload) == _e2e_payload(
                run_id=case.run_id,
                message_id=message_id,
                index=index,
                text_interval=case.text_payload_interval,
                empty_interval=case.empty_payload_interval,
            )
    assert (
        await asyncio.to_thread(
            _expected_stream_header_count_by_run_id,
            sink._pool,
            table=case.table,
            run_id=case.run_id,
            stream=case.stream,
        )
        == case.expected_stream_headers
    )
    priority_count, classification_count, labels_count, both_count = await asyncio.to_thread(
        _message_metadata_counts_by_run_id,
        sink._pool,
        table=case.table,
        run_id=case.run_id,
    )
    assert priority_count == case.expected_priorities
    assert classification_count == case.expected_classifications
    assert labels_count == case.expected_labels
    assert both_count == case.expected_both_message_metadata
    observations = metrics.observations.get("batch_write_seconds", [])
    assert observations
    assert len(observations) == case.expected_batch_count
    assert sum(observations) > 0
    assert metrics.gauges["current_batch_size"] == case.expected_final_batch_size
    _print_timings_if_requested(metrics=metrics, message_count=case.message_count)
    info = await setup_js.consumer_info(case.stream, case.consumer)
    assert info.num_ack_pending == 0


async def _prepare_sink_table_for_e2e(sink: OracleSink, case: E2ECase) -> None:
    await sink.start()
    if sink._pool is not None and case.drop_table_before:
        await asyncio.to_thread(_drop_table, sink._pool, table=case.table)
        await sink.ensure_schema()
    if sink._pool is not None:
        columns = await asyncio.to_thread(_table_columns, sink._pool, table=case.table)
        missing = sorted(REQUIRED_E2E_COLUMNS - columns)
        if missing:
            pytest.fail(
                f"Oracle e2e table {case.table!r} is missing required columns {missing}. "
                "Use NATS_SINKS_E2E_DROP_TABLE_BEFORE=true or choose a fresh "
                "NATS_SINKS_E2E_ORACLE_TABLE."
            )


@pytest.mark.asyncio
async def test_nats_publish_runner_receive_and_oracle_store() -> None:
    case = _build_e2e_case()

    sink = _oracle_sink(case.table)
    try:
        await _prepare_sink_table_for_e2e(sink, case)
        setup_nc = await nats.connect(case.nats_url, **_nats_options())
        setup_js = setup_nc.jetstream()
        try:
            await _ensure_stream(setup_js, stream=case.stream, subject=case.publish_subject)
            await _prepare_consumer(
                setup_js,
                stream=case.stream,
                subject=case.subject,
                consumer=case.consumer,
                max_ack_pending=max(case.message_count, case.batch_size),
            )
            await _publish_e2e_messages(
                setup_js,
                stream=case.stream,
                subject=case.publish_subject,
                run_id=case.run_id,
                message_count=case.message_count,
                text_interval=case.text_payload_interval,
                empty_interval=case.empty_payload_interval,
                missing_message_id_interval=case.missing_message_id_interval,
                expected_stream_header_interval=case.expected_stream_header_interval,
            )
            metrics = InMemoryMetrics()
            runner = JetStreamSinkRunner(
                nats_url=case.nats_url,
                stream=case.stream,
                consumer=case.consumer,
                subject=case.subject,
                sink=sink,
                delivery=DeliveryConfig(batch_size=case.batch_size, batch_timeout_ms=5000),
                encryption=case.encryption,
                metrics=metrics,
                nats_options=_nats_options(),
            )
            await runner.start()
            try:
                await _drain_messages(runner, case)
                await _assert_e2e_rows(sink=sink, setup_js=setup_js, metrics=metrics, case=case)
            finally:
                if sink._pool is not None and case.drop_table_after:
                    await asyncio.to_thread(_drop_table, sink._pool, table=case.table)
                await runner.stop()
        finally:
            with suppress(Exception):
                await setup_js.delete_consumer(case.stream, case.consumer)
            await setup_nc.close()
    finally:
        await sink.stop()
