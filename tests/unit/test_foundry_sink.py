# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.config import DeliveryConfig
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
)
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.foundry import (
    FoundrySink,
    FoundrySinkConfig,
    FoundryStreamPushResult,
    foundry_record_key,
    foundry_value_for_envelope,
    prepare_foundry_batch,
)
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_lifecycle,
    certify_sink_write_success,
)


class FakeFoundryClient:
    def __init__(
        self,
        *,
        result: FoundryStreamPushResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[tuple[Mapping[str, Any], ...], float]] = []

    async def push_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        timeout_seconds: float,
    ) -> FoundryStreamPushResult:
        self.calls.append((tuple(records), timeout_seconds))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return FoundryStreamPushResult(accepted_records=len(records), response_status=202)


def _config(**overrides: Any) -> FoundrySinkConfig:
    values: dict[str, Any] = {
        "stream_push_url": "https://foundry.example.invalid/api/push/streams/safe",
        "bearer_token_env": "FOUNDRY_TOKEN",
        "endpoint_allowed_hosts": ["foundry.example.invalid"],
    }
    values.update(overrides)
    return FoundrySinkConfig.model_validate(values)


def _envelope(
    *,
    stream_sequence: int = 1,
    message_id: str | None = "message-1",
    data: bytes = b'{"sensor_id":"S-1","status":"ok"}',
) -> NatsEnvelope:
    return certification_envelope(
        subject="mission.sensor.event",
        stream="SENSOR_EVENTS",
        stream_sequence=stream_sequence,
        message_id=message_id,
        data=data,
        priority="high",
        classification="nato-unclassified",
        labels=("sensor", "foundry"),
    )


def test_foundry_config_requires_https_unless_loopback_test() -> None:
    with pytest.raises(ValueError, match="https outside local loopback"):
        _config(stream_push_url="http://foundry.example.invalid/api/push")

    config = _config(
        stream_push_url="http://127.0.0.1:18080/api/push",
        allow_http_for_local_testing=True,
        endpoint_allowed_hosts=[],
    )

    assert config.stream_push_url.startswith("http://127.0.0.1")


def test_foundry_config_requires_auth_mode_specific_fields() -> None:
    with pytest.raises(ValueError, match="bearer_token_env is required"):
        FoundrySinkConfig.model_validate(
            {
                "stream_push_url": "https://foundry.example.invalid/api/push",
            }
        )

    oauth = FoundrySinkConfig.model_validate(
        {
            "stream_push_url": "https://foundry.example.invalid/api/push",
            "auth_mode": "oauth2_client_credentials",
            "oauth2_token_url": "https://foundry.example.invalid/oauth2/token",
            "oauth2_client_id_env": "FOUNDRY_CLIENT_ID",
            "oauth2_client_secret_env": "FOUNDRY_CLIENT_SECRET",
            "oauth2_scope": "api:use-streams-write",
            "endpoint_allowed_hosts": ["foundry.example.invalid"],
        }
    )

    assert oauth.auth_mode == "oauth2_client_credentials"


def test_foundry_config_rejects_ambiguous_endpoint_and_field_names() -> None:
    with pytest.raises(ValueError, match="host is not in endpoint_allowed_hosts"):
        _config(endpoint_allowed_hosts=["other.example.invalid"])

    with pytest.raises(ValueError, match="field names must be unique"):
        _config(payload_field="subject")

    with pytest.raises(ValueError, match="must start with a letter"):
        _config(payload_field="payload.value")


def test_foundry_from_mapping_wraps_pydantic_errors() -> None:
    with pytest.raises(ConfigurationError, match="bearer_token_env"):
        FoundrySink.from_mapping(
            {
                "type": "foundry",
                "stream_push_url": "https://foundry.example.invalid/api/push",
            }
        )


def test_foundry_mapping_preserves_metadata_and_payload_contract() -> None:
    envelope = _envelope()
    config = _config()

    value = foundry_value_for_envelope(envelope, config=config)

    assert value["nats_sinks_record_key"] == "stream-sequence:SENSOR_EVENTS:1"
    assert value["subject"] == "mission.sensor.event"
    assert value["payload"] == {"sensor_id": "S-1", "status": "ok"}
    assert value["priority"] == "high"
    assert value["classification"] == "nato-unclassified"
    assert value["labels_list"] == ["sensor", "foundry"]
    assert isinstance(value["metadata"], dict)


def test_foundry_record_key_strategies_fail_closed_when_required_metadata_is_missing() -> None:
    envelope = _envelope(message_id=None)

    assert foundry_record_key(envelope, config=_config(record_key_strategy="idempotency_key"))

    with pytest.raises(PermanentSinkError, match="requires a message ID"):
        foundry_record_key(envelope, config=_config(record_key_strategy="message_id"))

    with pytest.raises(PermanentSinkError, match="requires stream metadata"):
        foundry_record_key(
            certification_envelope(stream=None, stream_sequence=None),
            config=_config(record_key_strategy="stream_sequence"),
        )


def test_foundry_batch_rejects_duplicate_keys_and_size_overflow() -> None:
    config = _config(record_key_strategy="message_id")
    first = _envelope(message_id="same", stream_sequence=1)
    duplicate = _envelope(message_id="same", stream_sequence=2)

    with pytest.raises(PermanentSinkError, match="duplicate record keys"):
        prepare_foundry_batch((first, duplicate), config=config)

    with pytest.raises(PermanentSinkError, match="max_record_bytes"):
        prepare_foundry_batch(
            (_envelope(data=b'{"large":"' + (b"x" * 2048) + b'"}'),),
            config=_config(max_record_bytes=1024),
        )


@pytest.mark.asyncio
async def test_foundry_sink_batches_records_and_accepts_success() -> None:
    client = FakeFoundryClient()
    sink = FoundrySink(
        stream_push_url=_config().stream_push_url,
        config=_config(batch_size=1),
        client=client,
    )

    await sink.start()
    await sink.write_batch((_envelope(stream_sequence=1), _envelope(stream_sequence=2)))
    await sink.stop()

    assert len(client.calls) == 2
    assert client.calls[0][1] == 10.0


@pytest.mark.asyncio
async def test_foundry_sink_fails_closed_on_rejected_or_ambiguous_result() -> None:
    rejected = FoundrySink(
        stream_push_url=_config().stream_push_url,
        config=_config(),
        client=FakeFoundryClient(
            result=FoundryStreamPushResult(accepted_records=0, rejected_records=1)
        ),
    )

    with pytest.raises(PermanentSinkError, match="rejected"):
        await rejected.write_batch((_envelope(),))

    ambiguous = FoundrySink(
        stream_push_url=_config().stream_push_url,
        config=_config(),
        client=FakeFoundryClient(result=FoundryStreamPushResult(accepted_records=0)),
    )

    with pytest.raises(DestinationUnavailableError, match="confirm every record"):
        await ambiguous.write_batch((_envelope(),))


@pytest.mark.asyncio
async def test_foundry_sink_preserves_temporary_client_failures() -> None:
    sink = FoundrySink(
        stream_push_url=_config().stream_push_url,
        config=_config(),
        client=FakeFoundryClient(error=DestinationUnavailableError("temporary")),
    )

    with pytest.raises(DestinationUnavailableError, match="temporary"):
        await sink.write_batch((_envelope(),))


def _certification_case(client: FakeFoundryClient) -> SinkCertificationCase:
    def make_sink() -> FoundrySink:
        return FoundrySink(
            stream_push_url=_config().stream_push_url,
            config=_config(),
            client=client,
        )

    return SinkCertificationCase(
        name="foundry",
        sink_factory=make_sink,
        messages=(_envelope(),),
        duplicate_messages=(_envelope(),),
    )


@pytest.mark.asyncio
async def test_foundry_sink_passes_lifecycle_and_write_certification() -> None:
    client = FakeFoundryClient()

    await certify_sink_lifecycle(_certification_case(client))
    await certify_sink_write_success(_certification_case(client))

    assert client.calls


@pytest.mark.asyncio
async def test_foundry_sink_passes_duplicate_redelivery_certification() -> None:
    client = FakeFoundryClient()

    await certify_sink_duplicate_redelivery(_certification_case(client))

    assert len(client.calls) == 2
    first_key = client.calls[0][0][0]["value"]["nats_sinks_record_key"]
    second_key = client.calls[1][0][0]["value"]["nats_sinks_record_key"]
    assert first_key == second_key


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "SENSOR_EVENTS"
    consumer: str = "foundry"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    def __init__(self, events: list[str]) -> None:
        self.subject = "mission.sensor.event"
        self.data = b'{"sensor_id":"S-1"}'
        self.headers = {"Nats-Msg-Id": "message-1"}
        self.metadata = FakeMetadata()
        self.events = events
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append("nak")
        self.nacked = True

    async def term(self) -> None:
        self.events.append("term")

    async def in_progress(self) -> None:
        self.events.append("in_progress")


@pytest.mark.asyncio
async def test_foundry_sink_failure_prevents_core_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    sink = FoundrySink(
        stream_push_url=_config().stream_push_url,
        config=_config(),
        client=FakeFoundryClient(error=DestinationUnavailableError("temporary")),
    )
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="SENSOR_EVENTS",
        consumer="foundry",
        subject="mission.sensor.*",
        sink=sink,
        delivery=DeliveryConfig(max_retries=1, retry_backoff_ms=0),
    )

    await runner.process_raw_batch([message])

    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_foundry_sink_success_allows_core_ack_after_acceptance() -> None:
    events: list[str] = []
    message = FakeMessage(events)

    class RecordingClient(FakeFoundryClient):
        async def push_records(
            self,
            records: Sequence[Mapping[str, Any]],
            *,
            timeout_seconds: float,
        ) -> FoundryStreamPushResult:
            events.append("accepted")
            return await super().push_records(records, timeout_seconds=timeout_seconds)

    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="SENSOR_EVENTS",
        consumer="foundry",
        subject="mission.sensor.*",
        sink=FoundrySink(
            stream_push_url=_config().stream_push_url,
            config=_config(),
            client=RecordingClient(),
        ),
    )

    await runner.process_raw_batch([message])

    assert events == ["accepted", "ack"]
    assert message.acked
