# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from nats_sinks import NatsEnvelope, Sink
from nats_sinks.file import FileSink
from nats_sinks.mysql import MySqlSink
from nats_sinks.oracle import OracleSink
from nats_sinks.testing import (
    SinkCertificationCase,
    assert_envelope_has_no_ack_primitives,
    assert_log_records_exclude_sensitive_values,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_lifecycle,
    certify_sink_write_success,
)


def _json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _read_json(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _file_case(root: Path) -> SinkCertificationCase:
    first = certification_envelope(stream_sequence=1, message_id="cert-file-1")
    second = certification_envelope(stream_sequence=2, message_id="cert-file-2")
    duplicate = certification_envelope(stream_sequence=3, message_id="cert-file-3")

    def make_sink() -> Sink:
        return FileSink(directory=root, fsync=False)

    def assert_written(_sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        files = _json_files(root)
        assert len(files) == len(messages)
        record = _read_json(files[0])
        assert record["subject"] == first.subject
        assert record["priority"] == "normal"
        assert record["classification"] == "unclassified"
        assert record["labels"] == "certification"

    def assert_duplicate(_sink: Sink, _messages: Sequence[NatsEnvelope]) -> None:
        files = _json_files(root)
        assert len(files) == 1
        record = _read_json(files[0])
        assert record["stream_sequence"] == 3

    return SinkCertificationCase(
        name="file",
        sink_factory=make_sink,
        messages=(first, second),
        duplicate_messages=(duplicate,),
        after_write=assert_written,
        after_duplicate_write=assert_duplicate,
    )


class _OracleCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.executemany_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.rowcount = 1

    def __enter__(self) -> _OracleCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, binds: dict[str, Any] | None = None) -> None:
        self.executed.append((sql, binds))

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        self.executemany_calls.append((sql, rows))


class _OracleConnection:
    def __init__(self) -> None:
        self.cursor_instance = _OracleCursor()
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> _OracleConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _OracleCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _OraclePool:
    def __init__(self) -> None:
        self.connection = _OracleConnection()

    def acquire(self) -> _OracleConnection:
        return self.connection


class _MySqlCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.rowcount = 1
        self.closed = False

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.executions.append((sql, rows))

    def close(self) -> None:
        self.closed = True


class _MySqlConnection:
    def __init__(self) -> None:
        self.cursor_instance = _MySqlCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _MySqlCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _MySqlPool:
    def __init__(self) -> None:
        self.connection = _MySqlConnection()

    def get_connection(self) -> _MySqlConnection:
        return self.connection


class _CertifiedOracleSink(OracleSink):
    """Oracle sink test double that uses a fake pool instead of a live database."""

    def __init__(self, pool: _OraclePool) -> None:
        self.certification_pool = pool
        super().__init__(
            dsn="localhost:1521/FREEPDB1",
            user="app_user",
            password="example",  # noqa: S106 - local non-secret test placeholder.
            table="NATS_SINK_EVENTS",
            mode="insert",
        )

    async def start(self) -> None:
        self._pool = self.certification_pool

    async def stop(self) -> None:
        self._pool = None


class _CertifiedMySqlSink(MySqlSink):
    """Oracle MySQL sink test double that uses a fake pool."""

    def __init__(self, pool: _MySqlPool) -> None:
        self.certification_pool = pool
        super().__init__(
            host="127.0.0.1",
            database="nats_sinks_test",
            user="app_user",
            password="example",  # noqa: S106 - local non-secret test placeholder.
            table="NATS_SINK_EVENTS",
            mode="insert",
        )

    async def start(self) -> None:
        self._pool = self.certification_pool

    async def stop(self) -> None:
        self._pool = None


@pytest.mark.asyncio
async def test_file_sink_passes_lifecycle_certification(tmp_path: Path) -> None:
    await certify_sink_lifecycle(_file_case(tmp_path))


@pytest.mark.asyncio
async def test_file_sink_passes_write_certification(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await certify_sink_write_success(_file_case(tmp_path))

    assert_log_records_exclude_sensitive_values(
        caplog.records,
        sensitive_values=("CERT-1", "cert-file-1"),
    )


@pytest.mark.asyncio
async def test_file_sink_passes_duplicate_redelivery_certification(tmp_path: Path) -> None:
    await certify_sink_duplicate_redelivery(_file_case(tmp_path))


@pytest.mark.asyncio
async def test_oracle_sink_passes_durable_success_certification() -> None:
    pool = _OraclePool()

    def make_sink() -> Sink:
        return _CertifiedOracleSink(pool)

    def assert_committed(sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        del sink
        assert pool.connection.committed is True
        assert pool.connection.rolled_back is False
        assert len(pool.connection.cursor_instance.executemany_calls) == 1
        _sql, rows = pool.connection.cursor_instance.executemany_calls[0]
        assert len(rows) == len(messages)
        assert rows[0]["subject"] == "certification.events.created"

    case = SinkCertificationCase(
        name="oracle",
        sink_factory=make_sink,
        messages=(certification_envelope(),),
        after_write=assert_committed,
    )

    await certify_sink_write_success(case)


@pytest.mark.asyncio
async def test_mysql_sink_passes_durable_success_certification() -> None:
    pool = _MySqlPool()

    def make_sink() -> Sink:
        return _CertifiedMySqlSink(pool)

    def assert_committed(sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        del sink
        assert pool.connection.committed is True
        assert pool.connection.rolled_back is False
        assert pool.connection.closed is True
        assert len(pool.connection.cursor_instance.executions) == 1
        _sql, rows = pool.connection.cursor_instance.executions[0]
        assert len(rows) == len(messages)
        assert "certification.events.created" in rows[0]

    case = SinkCertificationCase(
        name="mysql",
        sink_factory=make_sink,
        messages=(certification_envelope(),),
        after_write=assert_committed,
    )

    await certify_sink_write_success(case)


def test_certification_envelope_never_exposes_ack_primitives() -> None:
    envelope = certification_envelope()

    assert_envelope_has_no_ack_primitives(envelope)
    assert not any(hasattr(envelope, name) for name in ("ack", "nak", "term"))


def test_certification_case_requires_messages() -> None:
    with pytest.raises(ValueError, match="at least one message"):
        SinkCertificationCase(
            name="invalid",
            sink_factory=lambda: cast(Sink, object()),
            messages=(),
        )
