# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.config import AppConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
)
from nats_sinks.oracle_nosql import (
    OracleNoSqlSink,
    OracleNoSqlSinkConfig,
    oracle_nosql_create_table_statement,
    oracle_nosql_key_for_envelope,
    oracle_nosql_row_for_envelope,
    oracle_nosql_value_for_envelope,
)
from nats_sinks.oracle_nosql import sink as oracle_nosql_sink_module
from nats_sinks.sinks.base import Sink
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_write_success,
)


class FakeOracleNoSqlClient:
    def __init__(self, *, fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.closed = False
        self.ensure_table_calls = 0
        self.rows: dict[str, dict[str, Any]] = {}
        self.put_calls: list[tuple[dict[str, Any], bool]] = []

    async def ensure_table(self) -> None:
        await self._maybe_wait_or_fail()
        self.ensure_table_calls += 1

    async def put_row(self, row: dict[str, Any], *, if_absent: bool) -> bool:
        await self._maybe_wait_or_fail()
        self.put_calls.append((row, if_absent))
        key = str(row["sink_key"])
        if if_absent and key in self.rows:
            return False
        self.rows[key] = row
        return True

    async def close(self) -> None:
        self.closed = True

    async def _maybe_wait_or_fail(self) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("synthetic Oracle NoSQL failure")


def _envelope(
    *,
    data: bytes = b'{"event_id":"NOSQL-1","status":"ok"}',
    message_id: str | None = "nosql-message-1",
    stream: str | None = "NOSQL",
    stream_sequence: int | None = 42,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="mission.sensor.alpha",
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="nosql-consumer",
        stream_sequence=stream_sequence,
        consumer_sequence=7,
        timestamp=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        message_id=message_id,
        redelivered=False,
        pending=0,
        priority="high",
        classification="NATO SECRET",
        labels=("sensor", "audit"),
        mission_metadata={"profile": "example", "phase": "find"},
        security_labels={"profile": "demo", "classification": "NATO SECRET"},
    )


def _sink_with_fake_client(
    client: FakeOracleNoSqlClient,
    *,
    config: OracleNoSqlSinkConfig | None = None,
) -> OracleNoSqlSink:
    effective_config = config or OracleNoSqlSinkConfig(table_name="events")
    return OracleNoSqlSink(config=effective_config, client_factory=lambda _config: client)


def test_oracle_nosql_config_rejects_unsafe_values() -> None:
    with pytest.raises(PydanticValidationError, match="endpoint"):
        OracleNoSqlSinkConfig(endpoint="ftp://nosql.example.invalid")

    with pytest.raises(PydanticValidationError, match="credentials"):
        OracleNoSqlSinkConfig(endpoint="https://user:pass@example.invalid")

    with pytest.raises(PydanticValidationError, match="table_name"):
        OracleNoSqlSinkConfig(table_name="../events")

    with pytest.raises(PydanticValidationError, match="value_field"):
        OracleNoSqlSinkConfig(value_field="event-json")

    with pytest.raises(PydanticValidationError, match="must be distinct"):
        OracleNoSqlSinkConfig(value_field="sink_key")

    with pytest.raises(PydanticValidationError, match="not valid"):
        OracleNoSqlSinkConfig(deployment_mode="cloud", auth_mode="store_access_token")


def test_oracle_nosql_optional_fanout_targets_receive_safe_defaults() -> None:
    config = AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "EVENTS",
                "consumer": "nosql-sink",
                "subject": "events.>",
            },
            "sink": {"type": "fanout"},
            "sinks": {"nosql_read_model": {"type": "oracle_nosql"}},
            "routing": {
                "enabled": True,
                "routes": [
                    {
                        "name": "audit",
                        "match": {"subject": "events.>"},
                        "targets": [{"sink": "nosql_read_model", "required": False}],
                    }
                ],
            },
        }
    )

    target = config.routing.routes[0].targets[0]
    assert config.routing.target_sink_types == {"nosql_read_model": "oracle_nosql"}
    assert target.minimum_wait_ms == 1_000
    assert target.timeout_ms == 5_000


def test_oracle_nosql_row_preserves_full_event_metadata() -> None:
    stored_at = datetime(2026, 5, 28, 12, 5, tzinfo=UTC)
    config = OracleNoSqlSinkConfig()
    envelope = _envelope()
    value = oracle_nosql_value_for_envelope(envelope, config=config, stored_at=stored_at)
    row = oracle_nosql_row_for_envelope(envelope, config=config, stored_at=stored_at)

    assert value["schema"] == "nats_sinks.oracle_nosql.event.v1"
    assert value["schema_version"] == 1
    assert value["subject"] == "mission.sensor.alpha"
    assert value["stream"] == "NOSQL"
    assert value["stream_sequence"] == 42
    assert value["message_id"] == "nosql-message-1"
    assert value["priority"] == "high"
    assert value["classification"] == "NATO SECRET"
    assert value["labels"] == "sensor;audit"
    assert value["payload"] == {"event_id": "NOSQL-1", "status": "ok"}
    assert value["mission_metadata"] == {"phase": "find", "profile": "example"}
    assert value["security_labels"] == {
        "classification": "NATO SECRET",
        "profile": "demo",
    }
    assert value["stored_at_epoch_ns"] == 1_779_969_900_000_000_000
    assert row == {
        "sink_key": "stream-sequence:NOSQL:42",
        "event_json": value,
        "stored_at_epoch_ns": 1_779_969_900_000_000_000,
    }


def test_oracle_nosql_key_strategies_are_deterministic_and_bounded() -> None:
    envelope = _envelope()

    assert oracle_nosql_key_for_envelope(envelope, config=OracleNoSqlSinkConfig()) == (
        "stream-sequence:NOSQL:42"
    )
    assert (
        oracle_nosql_key_for_envelope(
            envelope,
            config=OracleNoSqlSinkConfig(key_strategy="message_id", key_prefix="ns"),
        )
        == "ns:message-id:nosql-message-1"
    )
    assert oracle_nosql_key_for_envelope(
        envelope,
        config=OracleNoSqlSinkConfig(key_strategy="payload_sha256"),
    ).startswith("payload-sha256:mission.sensor.alpha:")

    with pytest.raises(SerializationError, match="requires stream metadata"):
        oracle_nosql_key_for_envelope(
            _envelope(stream=None, stream_sequence=None),
            config=OracleNoSqlSinkConfig(key_strategy="stream_sequence"),
        )

    with pytest.raises(SerializationError, match="requires a message ID"):
        oracle_nosql_key_for_envelope(
            _envelope(message_id=None),
            config=OracleNoSqlSinkConfig(key_strategy="message_id"),
        )

    with pytest.raises(SerializationError, match="max_key_bytes"):
        oracle_nosql_key_for_envelope(
            envelope,
            config=OracleNoSqlSinkConfig(key_prefix="p" * 128, max_key_bytes=64),
        )


def test_oracle_nosql_generated_table_statement_uses_validated_identifiers() -> None:
    statement = oracle_nosql_create_table_statement(
        config=OracleNoSqlSinkConfig(
            table_name="ns.events",
            key_field="id",
            value_field="event_value",
            stored_at_field="stored_at",
        )
    )

    assert statement == (
        "CREATE TABLE IF NOT EXISTS ns.events "
        "(id STRING, event_value JSON, stored_at LONG, PRIMARY KEY(id))"
    )


@pytest.mark.asyncio
async def test_oracle_nosql_sink_puts_complete_value_after_optional_table_create() -> None:
    client = FakeOracleNoSqlClient()
    sink = _sink_with_fake_client(client, config=OracleNoSqlSinkConfig(auto_create=True))

    await sink.start()
    await sink.write_batch([_envelope()])
    await sink.stop()

    assert client.ensure_table_calls == 1
    assert client.closed is True
    assert len(client.put_calls) == 1
    row, if_absent = client.put_calls[0]
    assert if_absent is True
    assert row["sink_key"] == "stream-sequence:NOSQL:42"
    assert row["event_json"]["payload"] == {"event_id": "NOSQL-1", "status": "ok"}


@pytest.mark.asyncio
async def test_oracle_nosql_sink_skip_existing_preserves_prior_value() -> None:
    client = FakeOracleNoSqlClient()
    client.rows["stream-sequence:NOSQL:42"] = {"sink_key": "stream-sequence:NOSQL:42"}
    sink = _sink_with_fake_client(client)

    await sink.start()
    await sink.write_batch([_envelope()])

    assert client.rows["stream-sequence:NOSQL:42"] == {"sink_key": "stream-sequence:NOSQL:42"}
    assert len(client.put_calls) == 1


@pytest.mark.asyncio
async def test_oracle_nosql_sink_fail_existing_rejects_duplicate() -> None:
    client = FakeOracleNoSqlClient()
    client.rows["stream-sequence:NOSQL:42"] = {"sink_key": "stream-sequence:NOSQL:42"}
    sink = _sink_with_fake_client(
        client,
        config=OracleNoSqlSinkConfig(duplicate_policy="fail_existing"),
    )

    await sink.start()

    with pytest.raises(PermanentSinkError, match="already exists"):
        await sink.write_batch([_envelope()])


@pytest.mark.asyncio
async def test_oracle_nosql_sink_replace_overwrites_existing_value() -> None:
    client = FakeOracleNoSqlClient()
    client.rows["stream-sequence:NOSQL:42"] = {"old": True}
    sink = _sink_with_fake_client(
        client,
        config=OracleNoSqlSinkConfig(duplicate_policy="replace"),
    )

    await sink.start()
    await sink.write_batch([_envelope()])

    assert len(client.put_calls) == 1
    assert client.put_calls[0][1] is False
    assert client.rows["stream-sequence:NOSQL:42"]["event_json"]["payload"] == {
        "event_id": "NOSQL-1",
        "status": "ok",
    }


@pytest.mark.asyncio
async def test_oracle_nosql_sink_failures_do_not_return_success() -> None:
    failing = _sink_with_fake_client(FakeOracleNoSqlClient(fail=True))

    await failing.start()

    with pytest.raises(DestinationUnavailableError, match="batch write failed"):
        await failing.write_batch([_envelope()])

    slow = _sink_with_fake_client(
        FakeOracleNoSqlClient(delay_seconds=0.1),
        config=OracleNoSqlSinkConfig(request_timeout_seconds=0.01),
    )
    await slow.start()

    with pytest.raises(DestinationUnavailableError, match="write timed out"):
        await slow.write_batch([_envelope()])


@pytest.mark.asyncio
async def test_oracle_nosql_sink_rejects_oversized_values_before_write() -> None:
    client = FakeOracleNoSqlClient()
    sink = _sink_with_fake_client(client, config=OracleNoSqlSinkConfig(max_value_bytes=10))

    await sink.start()

    with pytest.raises(SerializationError, match="max_value_bytes"):
        await sink.write_batch([_envelope()])

    assert client.put_calls == []


@pytest.mark.asyncio
async def test_oracle_nosql_sink_missing_optional_dependency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(name: str) -> object:
        if name == "borneo":
            raise ImportError("missing test dependency")
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(oracle_nosql_sink_module.importlib, "import_module", fail_import)

    sink = OracleNoSqlSink.from_mapping({"type": "oracle_nosql"})

    with pytest.raises(ConfigurationError, match=r"nats-sinks\[oracle-nosql\]"):
        await sink.start()


def test_oracle_nosql_put_result_parsing_fails_closed_on_ambiguity() -> None:
    class AmbiguousResult:
        pass

    with pytest.raises(DestinationUnavailableError, match="success indicator"):
        oracle_nosql_sink_module._put_result_succeeded(AmbiguousResult())


@pytest.mark.asyncio
async def test_oracle_nosql_sink_passes_certification_helpers() -> None:
    client = FakeOracleNoSqlClient()

    def make_sink() -> Sink:
        return _sink_with_fake_client(client)

    def assert_written(_sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        assert len(client.rows) == len(messages)
        first = client.rows["stream-sequence:CERTIFICATION:1"]
        assert first["event_json"]["subject"] == "certification.events.created"
        assert first["event_json"]["payload"] == {"event_id": "CERT-1", "status": "ok"}

    case = SinkCertificationCase(
        name="oracle_nosql",
        sink_factory=make_sink,
        messages=(certification_envelope(),),
        duplicate_messages=(certification_envelope(),),
        after_write=assert_written,
        after_duplicate_write=assert_written,
    )

    await certify_sink_write_success(case)
    await certify_sink_duplicate_redelivery(case)
