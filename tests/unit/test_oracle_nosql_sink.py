# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

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


def test_oracle_nosql_handle_config_supports_current_sdk_shape() -> None:
    """The adapter should support SDKs that set authorization after construction."""

    class HandleConfig:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.provider: object | None = None

        def set_authorization_provider(self, provider: object) -> None:
            self.provider = provider

    class FakeBorneo:
        def __getattr__(self, name: str) -> object:
            if name == "NoSQLHandleConfig":
                return self.no_sql_handle_config
            raise AttributeError(name)

        def no_sql_handle_config(
            self,
            endpoint: str,
            provider: object | None = None,
        ) -> HandleConfig:
            if provider is not None:
                raise TypeError("single-argument constructor shape")
            return HandleConfig(endpoint)

    provider = object()
    config = oracle_nosql_sink_module._build_handle_config(
        borneo=FakeBorneo(),
        endpoint="http://127.0.0.1:8080",
        provider=provider,
    )

    assert config.endpoint == "http://127.0.0.1:8080"
    assert config.provider is provider


def test_oracle_nosql_handle_config_fails_closed_without_provider_setup() -> None:
    """Ambiguous SDK authorization setup must not silently continue."""

    def no_sql_handle_config(endpoint: str, provider: object | None = None) -> object:
        _ = endpoint
        if provider is not None:
            raise TypeError("constructor shape does not accept provider")
        return object()

    fake_borneo = SimpleNamespace(NoSQLHandleConfig=no_sql_handle_config)

    with pytest.raises(ConfigurationError, match="authorization-provider setup"):
        oracle_nosql_sink_module._build_handle_config(
            borneo=fake_borneo,
            endpoint="http://127.0.0.1:8080",
            provider=object(),
        )


def test_oracle_nosql_auth_provider_construction_is_mode_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment modes should select fixed SDK auth providers without networking."""

    class StoreAccessTokenProvider:
        pass

    class SignatureProvider:
        calls: ClassVar[list[dict[str, Any]]] = []
        instance_principal_calls: ClassVar[int] = 0

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.calls.append(kwargs)

        @classmethod
        def create_with_instance_principal(cls) -> object:
            cls.instance_principal_calls += 1
            return SimpleNamespace(kind="instance-principal")

    class AuthorizationProvider:
        pass

    def fake_import(name: str) -> object:
        if name == "borneo.kv":
            return SimpleNamespace(StoreAccessTokenProvider=StoreAccessTokenProvider)
        if name == "borneo.iam":
            return SimpleNamespace(SignatureProvider=SignatureProvider)
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(oracle_nosql_sink_module.importlib, "import_module", fake_import)
    env_var_name = "NOSQL_TEST_VALUE_ENV"
    monkeypatch.setenv(env_var_name, "redacted-local-value")
    fake_borneo = SimpleNamespace(AuthorizationProvider=AuthorizationProvider)

    kv_provider = oracle_nosql_sink_module._build_authorization_provider(
        borneo=fake_borneo,
        config=OracleNoSqlSinkConfig(deployment_mode="kvstore"),
    )
    assert isinstance(kv_provider, StoreAccessTokenProvider)

    cloudsim_provider = oracle_nosql_sink_module._build_authorization_provider(
        borneo=fake_borneo,
        config=OracleNoSqlSinkConfig(
            deployment_mode="cloudsim",
            cloudsim_tenant_id="tenant-demo",
        ),
    )
    assert cloudsim_provider.get_authorization_string() == "Bearer tenant-demo"

    oci_provider = oracle_nosql_sink_module._build_authorization_provider(
        borneo=fake_borneo,
        config=OracleNoSqlSinkConfig(
            deployment_mode="cloud",
            oci_config_file="/safe/local/oci-config",
            oci_profile="CERTIFICATION",
            oci_private_key_passphrase_env=env_var_name,
        ),
    )
    assert isinstance(oci_provider, SignatureProvider)
    assert SignatureProvider.calls[-1] == {
        "profile_name": "CERTIFICATION",
        "config_file": "/safe/local/oci-config",
        "pass_phrase": "redacted-local-value",
    }

    instance_provider = oracle_nosql_sink_module._build_authorization_provider(
        borneo=fake_borneo,
        config=OracleNoSqlSinkConfig(deployment_mode="cloud", auth_mode="instance_principal"),
    )
    assert instance_provider == SimpleNamespace(kind="instance-principal")
    assert SignatureProvider.instance_principal_calls == 1


def test_oracle_nosql_client_from_config_sets_namespace_without_sdk_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client construction should use validated config and avoid dynamic paths."""

    class StoreAccessTokenProvider:
        pass

    class HandleConfig:
        def __init__(self, endpoint: str, provider: object) -> None:
            self.endpoint = endpoint
            self.provider = provider
            self.namespace: str | None = None
            self.compartment: str | None = None

        def set_default_namespace(self, namespace: str) -> None:
            self.namespace = namespace

        def set_default_compartment(self, compartment: str) -> None:
            self.compartment = compartment

    class Handle:
        def __init__(self, config: HandleConfig) -> None:
            self.config = config

    def fake_import(name: str) -> object:
        if name == "borneo":
            return SimpleNamespace(NoSQLHandleConfig=HandleConfig, NoSQLHandle=Handle)
        if name == "borneo.kv":
            return SimpleNamespace(StoreAccessTokenProvider=StoreAccessTokenProvider)
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(oracle_nosql_sink_module.importlib, "import_module", fake_import)

    client = oracle_nosql_sink_module._BorneoOracleNoSqlClient.from_config(
        OracleNoSqlSinkConfig(
            endpoint="http://127.0.0.1:8080",
            namespace="mission_namespace",
            compartment_id="ocid1.compartment.oc1..exampleuniqueid",
        )
    )

    assert client._handle.config.endpoint == "http://127.0.0.1:8080"
    assert isinstance(client._handle.config.provider, StoreAccessTokenProvider)
    assert client._handle.config.namespace == "mission_namespace"
    assert client._handle.config.compartment == "ocid1.compartment.oc1..exampleuniqueid"


@pytest.mark.asyncio
async def test_oracle_nosql_cloud_table_creation_uses_limits_and_waits() -> None:
    """Cloud table creation should attach bounded limits and wait for completion."""

    class TableLimits:
        def __init__(self, read_units: int, write_units: int, storage_gb: int) -> None:
            self.read_units = read_units
            self.write_units = write_units
            self.storage_gb = storage_gb

    class TableRequest:
        def __init__(self) -> None:
            self.statement: str | None = None
            self.limits: TableLimits | None = None

        def set_statement(self, statement: str) -> TableRequest:
            self.statement = statement
            return self

        def set_table_limits(self, limits: TableLimits) -> TableRequest:
            self.limits = limits
            return self

    class TableResult:
        def __init__(self) -> None:
            self.wait_args: tuple[object, int, int] | None = None

        def wait_for_completion(
            self,
            handle: object,
            timeout_ms: int,
            poll_interval_ms: int,
        ) -> None:
            self.wait_args = (handle, timeout_ms, poll_interval_ms)

    class Handle:
        def __init__(self) -> None:
            self.request: TableRequest | None = None
            self.result = TableResult()

        def table_request(self, request: TableRequest) -> TableResult:
            self.request = request
            return self.result

    handle = Handle()
    client = oracle_nosql_sink_module._BorneoOracleNoSqlClient(
        config=OracleNoSqlSinkConfig(
            endpoint="https://nosql.example.invalid",
            deployment_mode="cloud",
            table_name="events",
            read_units=7,
            write_units=11,
            storage_gb=3,
            table_timeout_ms=12_000,
            table_poll_interval_ms=500,
        ),
        borneo=SimpleNamespace(TableRequest=TableRequest, TableLimits=TableLimits),
        handle=handle,
    )

    await client.ensure_table()

    assert handle.request is not None
    assert handle.request.statement == (
        "CREATE TABLE IF NOT EXISTS events "
        "(sink_key STRING, event_json JSON, stored_at_epoch_ns LONG, PRIMARY KEY(sink_key))"
    )
    assert handle.request.limits is not None
    assert handle.request.limits.read_units == 7
    assert handle.request.limits.write_units == 11
    assert handle.request.limits.storage_gb == 3
    assert handle.result.wait_args == (handle, 12_000, 500)


@pytest.mark.asyncio
async def test_oracle_nosql_put_request_uses_timeout_and_conditional_option() -> None:
    """SDK put requests should keep code and data separate through fixed setters."""

    class PutOption:
        IF_ABSENT = "IF_ABSENT"

    class PutRequest:
        def __init__(self) -> None:
            self.table_name: str | None = None
            self.value: dict[str, Any] | None = None
            self.timeout_ms: int | None = None
            self.option: object | None = None
            self.return_row: bool | None = None

        def set_table_name(self, table_name: str) -> PutRequest:
            self.table_name = table_name
            return self

        def set_value(self, value: dict[str, Any]) -> PutRequest:
            self.value = value
            return self

        def set_timeout(self, timeout_ms: int) -> PutRequest:
            self.timeout_ms = timeout_ms
            return self

        def set_option(self, option: object) -> PutRequest:
            self.option = option
            return self

        def set_return_row(self, return_row: bool) -> PutRequest:
            self.return_row = return_row
            return self

    class Result:
        @staticmethod
        def get_success() -> bool:
            return True

    class Handle:
        def __init__(self) -> None:
            self.request: PutRequest | None = None

        def put(self, request: PutRequest) -> Result:
            self.request = request
            return Result()

    handle = Handle()
    client = oracle_nosql_sink_module._BorneoOracleNoSqlClient(
        config=OracleNoSqlSinkConfig(table_name="events", request_timeout_seconds=2.5),
        borneo=SimpleNamespace(PutRequest=PutRequest, PutOption=PutOption),
        handle=handle,
    )
    row = {"sink_key": "key-1", "event_json": {"ok": True}, "stored_at_epoch_ns": 1}

    assert await client.put_row(row, if_absent=True) is True
    assert handle.request is not None
    assert handle.request.table_name == "events"
    assert handle.request.value == row
    assert handle.request.timeout_ms == 2500
    assert handle.request.option == PutOption.IF_ABSENT
    assert handle.request.return_row is False


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
