# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence
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
from nats_sinks.gotham import (
    GothamObjectWrite,
    GothamObjectWriteResult,
    GothamSink,
    GothamSinkConfig,
    gotham_external_id,
    gotham_object_request_for_envelope,
    prepare_gotham_batch,
)
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_lifecycle,
    certify_sink_write_success,
)


class FakeGothamClient:
    def __init__(
        self,
        *,
        result: GothamObjectWriteResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[tuple[GothamObjectWrite, ...], float]] = []

    async def create_objects(
        self,
        objects: Sequence[GothamObjectWrite],
        *,
        timeout_seconds: float,
    ) -> GothamObjectWriteResult:
        self.calls.append((tuple(objects), timeout_seconds))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return GothamObjectWriteResult(accepted_objects=len(objects), response_status=201)


def _config(**overrides: Any) -> GothamSinkConfig:
    values: dict[str, Any] = {
        "gotham_base_url": "https://gotham.example.invalid",
        "object_type": "com.example.object.event",
        "external_id_property_type": "com.example.property.externalId",
        "subject_property_type": "com.example.property.subject",
        "payload_property_type": "com.example.property.payload",
        "payload_info_property_type": "com.example.property.payloadInfo",
        "metadata_property_type": "com.example.property.metadata",
        "classification_property_type": "com.example.property.classification",
        "labels_list_property_type": "com.example.property.labels",
        "security_portion_markings": ["SENSITIVE"],
        "bearer_token_env": "GOTHAM_TOKEN",
        "endpoint_allowed_hosts": ["gotham.example.invalid"],
    }
    values.update(overrides)
    return GothamSinkConfig.model_validate(values)


def _sink(
    *,
    config: GothamSinkConfig | None = None,
    client: FakeGothamClient | None = None,
) -> GothamSink:
    config = config or _config()
    return GothamSink(
        gotham_base_url=config.gotham_base_url,
        object_type=config.object_type,
        external_id_property_type=config.external_id_property_type,
        subject_property_type=config.subject_property_type,
        payload_property_type=config.payload_property_type,
        config=config,
        client=client,
    )


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
        classification="restricted",
        labels=("sensor", "gotham"),
    )


def test_gotham_config_requires_https_unless_loopback_test() -> None:
    with pytest.raises(ValueError, match="https outside local loopback"):
        _config(gotham_base_url="http://gotham.example.invalid")

    local = _config(
        gotham_base_url="http://localhost:8080",
        endpoint_allowed_hosts=["localhost"],
        allow_http_for_local_testing=True,
    )

    assert local.gotham_base_url == "http://localhost:8080"


def test_gotham_config_requires_auth_mode_specific_fields() -> None:
    with pytest.raises(ValueError, match="bearer_token_env is required"):
        GothamSinkConfig.model_validate(
            {
                "gotham_base_url": "https://gotham.example.invalid",
                "object_type": "com.example.object.event",
                "external_id_property_type": "com.example.property.externalId",
                "subject_property_type": "com.example.property.subject",
                "payload_property_type": "com.example.property.payload",
            }
        )

    oauth = _config(
        auth_mode="oauth2_client_credentials",
        bearer_token_env=None,
        oauth2_token_url="https://gotham.example.invalid/multipass/api/oauth2/token",  # noqa: S106
        oauth2_client_id_env="GOTHAM_CLIENT_ID",
        oauth2_client_secret_env="GOTHAM_CLIENT_SECRET",  # noqa: S106
    )

    assert oauth.auth_mode == "oauth2_client_credentials"


def test_gotham_config_rejects_ambiguous_endpoint_and_property_mapping() -> None:
    with pytest.raises(ValueError, match="base URL"):
        _config(gotham_base_url="https://gotham.example.invalid/api/gotham/v1")

    with pytest.raises(ValueError, match="host is not in endpoint_allowed_hosts"):
        _config(endpoint_allowed_hosts=["other.example.invalid"])

    with pytest.raises(ValueError, match="Gotham property types must be unique"):
        _config(subject_property_type="com.example.property.externalId")

    with pytest.raises(ValueError, match="dotted Gotham API type name"):
        _config(object_type="unsafe object")


def test_gotham_from_mapping_wraps_pydantic_errors() -> None:
    with pytest.raises(ConfigurationError):
        GothamSink.from_mapping(
            {
                "type": "gotham",
                "gotham_base_url": "http://gotham.example.invalid",
                "object_type": "com.example.object.event",
            }
        )


def test_gotham_mapping_preserves_metadata_and_payload_contract() -> None:
    prepared = gotham_object_request_for_envelope(_envelope(), config=_config())
    properties = {
        item["propertyType"]: item["value"]
        for item in prepared.request["properties"]
        if isinstance(item, dict)
    }

    assert prepared.external_id.startswith("stream-sequence:")
    assert prepared.request["validationMode"] == "STRICT"
    assert prepared.request["security"] == {"portionMarkings": ["SENSITIVE"]}
    assert properties["com.example.property.subject"] == "mission.sensor.event"
    assert properties["com.example.property.payload"] == {"sensor_id": "S-1", "status": "ok"}
    assert properties["com.example.property.classification"] == "restricted"
    assert properties["com.example.property.labels"] == ["sensor", "gotham"]


def test_gotham_external_id_strategies_fail_closed_when_required_metadata_is_missing() -> None:
    envelope = _envelope(message_id=None)

    assert gotham_external_id(envelope, config=_config(external_id_strategy="idempotency_key"))
    with pytest.raises(PermanentSinkError, match="message ID"):
        gotham_external_id(envelope, config=_config(external_id_strategy="message_id"))
    with pytest.raises(PermanentSinkError, match="stream metadata"):
        gotham_external_id(
            NatsEnvelope(
                subject="mission.sensor.event",
                data=b"{}",
                headers={},
                stream=None,
                consumer=None,
                stream_sequence=None,
                consumer_sequence=None,
                timestamp=None,
                message_id=None,
                redelivered=False,
                pending=0,
            ),
            config=_config(external_id_strategy="stream_sequence"),
        )


def test_gotham_batch_rejects_duplicate_external_ids_and_size_overflow() -> None:
    config = _config(external_id_strategy="message_id")
    first = _envelope(message_id="same", stream_sequence=1)
    duplicate = _envelope(message_id="same", stream_sequence=2)

    with pytest.raises(PermanentSinkError, match="duplicate external IDs"):
        prepare_gotham_batch((first, duplicate), config=config)

    with pytest.raises(PermanentSinkError, match="max_object_bytes"):
        prepare_gotham_batch(
            (_envelope(data=b'{"large":"' + (b"x" * 2048) + b'"}'),),
            config=_config(max_object_bytes=1024),
        )


@pytest.mark.asyncio
async def test_gotham_sink_batches_objects_and_accepts_success() -> None:
    client = FakeGothamClient()
    sink = _sink(config=_config(batch_size=1), client=client)

    await sink.start()
    await sink.write_batch((_envelope(stream_sequence=1), _envelope(stream_sequence=2)))
    await sink.stop()

    assert len(client.calls) == 2
    assert client.calls[0][1] == 10.0


@pytest.mark.asyncio
async def test_gotham_sink_fails_closed_on_rejected_or_ambiguous_result() -> None:
    rejected = _sink(
        client=FakeGothamClient(
            result=GothamObjectWriteResult(accepted_objects=0, rejected_objects=1)
        )
    )

    with pytest.raises(PermanentSinkError, match="rejected"):
        await rejected.write_batch((_envelope(),))

    ambiguous = _sink(client=FakeGothamClient(result=GothamObjectWriteResult(accepted_objects=0)))

    with pytest.raises(DestinationUnavailableError, match="confirm every object"):
        await ambiguous.write_batch((_envelope(),))


@pytest.mark.asyncio
async def test_gotham_sink_preserves_temporary_client_failures() -> None:
    sink = _sink(client=FakeGothamClient(error=DestinationUnavailableError("temporary")))

    with pytest.raises(DestinationUnavailableError, match="temporary"):
        await sink.write_batch((_envelope(),))


def _certification_case(client: FakeGothamClient) -> SinkCertificationCase:
    def make_sink() -> GothamSink:
        return _sink(client=client)

    return SinkCertificationCase(
        name="gotham",
        sink_factory=make_sink,
        messages=(_envelope(),),
        duplicate_messages=(_envelope(),),
    )


@pytest.mark.asyncio
async def test_gotham_sink_passes_lifecycle_and_write_certification() -> None:
    client = FakeGothamClient()

    await certify_sink_lifecycle(_certification_case(client))
    await certify_sink_write_success(_certification_case(client))

    assert client.calls


@pytest.mark.asyncio
async def test_gotham_sink_passes_duplicate_redelivery_certification() -> None:
    client = FakeGothamClient()

    await certify_sink_duplicate_redelivery(_certification_case(client))

    assert len(client.calls) == 2
    first_external_id = client.calls[0][0][0].external_id
    second_external_id = client.calls[1][0][0].external_id
    assert first_external_id == second_external_id


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "SENSOR_EVENTS"
    consumer: str = "gotham"
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
async def test_gotham_sink_failure_prevents_core_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="SENSOR_EVENTS",
        consumer="gotham",
        subject="mission.sensor.*",
        sink=_sink(client=FakeGothamClient(error=DestinationUnavailableError("temporary"))),
        delivery=DeliveryConfig(max_retries=1, retry_backoff_ms=0),
    )

    await runner.process_raw_batch([message])

    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_gotham_sink_success_allows_core_ack_after_acceptance() -> None:
    events: list[str] = []
    message = FakeMessage(events)

    class RecordingClient(FakeGothamClient):
        async def create_objects(
            self,
            objects: Sequence[GothamObjectWrite],
            *,
            timeout_seconds: float,
        ) -> GothamObjectWriteResult:
            events.append("accepted")
            return await super().create_objects(objects, timeout_seconds=timeout_seconds)

    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="SENSOR_EVENTS",
        consumer="gotham",
        subject="mission.sensor.*",
        sink=_sink(client=RecordingClient()),
    )

    await runner.process_raw_batch([message])

    assert events == ["accepted", "ack"]
    assert message.acked
