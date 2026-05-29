# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import gzip
import json
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
from nats_sinks.s3 import (
    S3Client,
    S3PutObjectRequest,
    S3Sink,
    S3SinkConfig,
    prepare_s3_object,
    prepare_s3_sidecar_object,
    s3_key_for_envelope,
    s3_object_value_for_envelope,
    s3_sidecar_key_for_object,
)
from nats_sinks.sinks.base import Sink
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_write_success,
)


class FakeS3Client:
    def __init__(
        self,
        *,
        failures: Sequence[BaseException] = (),
        delay_seconds: float = 0.0,
    ) -> None:
        self.failures = list(failures)
        self.delay_seconds = delay_seconds
        self.closed = False
        self.calls: list[S3PutObjectRequest] = []
        self.objects: dict[str, bytes] = {}

    async def put_object(self, request: S3PutObjectRequest) -> bool:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.failures:
            raise self.failures.pop(0)
        self.calls.append(request)
        if request.if_none_match and request.key in self.objects:
            return False
        self.objects[request.key] = request.body
        return True

    async def close(self) -> None:
        self.closed = True


def _envelope(
    *,
    data: bytes = b'{"event_id":"S3-1","status":"ok"}',
    message_id: str | None = "s3-message-1",
    stream: str | None = "S3_EVENTS",
    stream_sequence: int | None = 42,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="mission.sensor.alpha",
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="s3-consumer",
        stream_sequence=stream_sequence,
        consumer_sequence=7,
        timestamp=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
        message_id=message_id,
        redelivered=False,
        pending=0,
        priority="high",
        classification="NATO SECRET",
        labels=("sensor", "audit"),
        mission_metadata={"profile": "example", "phase": "find"},
        security_labels={"profile": "demo", "classification": "NATO SECRET"},
    )


def _config(**overrides: Any) -> S3SinkConfig:
    values: dict[str, Any] = {"bucket": "nats-sinks-events"}
    values.update(overrides)
    return S3SinkConfig.model_validate(values)


def _sink_with_fake_client(client: FakeS3Client, *, config: S3SinkConfig | None = None) -> S3Sink:
    return S3Sink(config=config or _config(), client_factory=lambda _config: client)


def test_s3_config_rejects_unsafe_values() -> None:
    with pytest.raises(PydanticValidationError, match=r"sink\.bucket"):
        _config(bucket="Bad_Bucket")

    with pytest.raises(PydanticValidationError, match=r"sink\.prefix"):
        _config(prefix="../events")

    with pytest.raises(PydanticValidationError, match="endpoint_url must use https"):
        _config(endpoint_url="http://object-store.example.invalid")

    assert (
        _config(
            endpoint_url="http://127.0.0.1:9000",
            allow_http_for_local_testing=True,
        ).endpoint_url
        == "http://127.0.0.1:9000"
    )

    with pytest.raises(PydanticValidationError, match="credentials"):
        _config(endpoint_url="https://user:pass@object-store.example.invalid")

    with pytest.raises(PydanticValidationError, match="environment"):
        _config(credential_mode="environment", aws_access_key_id_env="AWS_ACCESS_KEY_ID")

    with pytest.raises(PydanticValidationError, match="profile_name"):
        _config(credential_mode="profile")

    with pytest.raises(PydanticValidationError, match="secret-bearing"):
        _config(object_metadata={"Authorization": "value"})

    with pytest.raises(PydanticValidationError, match="sidecar"):
        _config(metadata_mode="sidecar", duplicate_policy="fail_existing")

    with pytest.raises(PydanticValidationError, match="compressed object suffix"):
        _config(compression="gzip", object_suffix=".json")


def test_s3_optional_fanout_targets_receive_safe_defaults() -> None:
    config = AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "EVENTS",
                "consumer": "s3-sink",
                "subject": "events.>",
            },
            "sink": {"type": "fanout"},
            "sinks": {"s3_archive": {"type": "s3", "bucket": "nats-sinks-events"}},
            "routing": {
                "enabled": True,
                "routes": [
                    {
                        "name": "archive",
                        "match": {"subject": "events.>"},
                        "targets": [{"sink": "s3_archive", "required": False}],
                    }
                ],
            },
        }
    )

    target = config.routing.routes[0].targets[0]
    assert config.routing.target_sink_types == {"s3_archive": "s3"}
    assert target.minimum_wait_ms == 1_000
    assert target.timeout_ms == 5_000


def test_s3_key_strategies_are_deterministic_and_bounded() -> None:
    envelope = _envelope()

    assert s3_key_for_envelope(envelope, config=_config(prefix="events")) == (
        "events/idempotency-key/stream-sequence_S3_EVENTS_42.json"
    )
    assert (
        s3_key_for_envelope(
            envelope,
            config=_config(key_strategy="stream_sequence", key_prefix="archive"),
        )
        == "archive:stream-sequence/S3_EVENTS/42.json"
    )
    assert (
        s3_key_for_envelope(
            envelope,
            config=_config(key_strategy="message_id"),
        )
        == "message-id/s3-message-1.json"
    )
    assert s3_key_for_envelope(
        envelope,
        config=_config(key_strategy="payload_sha256"),
    ).startswith("payload-sha256/mission.sensor.alpha/")

    with pytest.raises(SerializationError, match="requires stream metadata"):
        s3_key_for_envelope(
            _envelope(stream=None, stream_sequence=None),
            config=_config(key_strategy="stream_sequence"),
        )

    with pytest.raises(SerializationError, match="requires a message ID"):
        s3_key_for_envelope(
            _envelope(message_id=None),
            config=_config(key_strategy="message_id"),
        )

    with pytest.raises(SerializationError, match="max_key_bytes"):
        s3_key_for_envelope(
            envelope,
            config=_config(prefix="p" * 128, key_prefix="k" * 128, max_key_bytes=64),
        )


def test_s3_envelope_object_preserves_full_event_metadata() -> None:
    stored_at = datetime(2026, 5, 29, 12, 5, tzinfo=UTC)
    value = s3_object_value_for_envelope(_envelope(), config=_config(), stored_at=stored_at)

    assert value["schema"] == "nats_sinks.s3.object.v1"
    assert value["schema_version"] == 1
    assert value["subject"] == "mission.sensor.alpha"
    assert value["stream"] == "S3_EVENTS"
    assert value["stream_sequence"] == 42
    assert value["message_id"] == "s3-message-1"
    assert value["priority"] == "high"
    assert value["classification"] == "NATO SECRET"
    assert value["labels"] == "sensor;audit"
    assert value["payload"] == {"event_id": "S3-1", "status": "ok"}
    assert value["mission_metadata"] == {"phase": "find", "profile": "example"}
    assert value["security_labels"] == {"classification": "NATO SECRET", "profile": "demo"}
    assert value["stored_at_epoch_ns"] == 1_780_056_300_000_000_000


def test_s3_payload_only_and_gzip_object_preparation() -> None:
    envelope = _envelope()
    payload_value = s3_object_value_for_envelope(
        envelope,
        config=_config(object_format="payload"),
    )
    assert payload_value == {"event_id": "S3-1", "status": "ok"}

    prepared = prepare_s3_object(
        envelope,
        config=_config(
            compression="gzip",
            object_suffix=".json.gz",
            object_metadata={"purpose": "test"},
        ),
    )

    assert prepared.content_encoding == "gzip"
    assert json.loads(gzip.decompress(prepared.body).decode("utf-8"))["payload"] == {
        "event_id": "S3-1",
        "status": "ok",
    }
    assert prepared.metadata["purpose"] == "test"
    assert "NATO SECRET" not in prepared.metadata.values()


def test_s3_sidecar_preparation_uses_deterministic_key() -> None:
    config = _config(metadata_mode="sidecar")
    envelope = _envelope()
    primary = prepare_s3_object(envelope, config=config)
    sidecar = prepare_s3_sidecar_object(envelope, config=config, object_key=primary.key)

    assert sidecar.key == s3_sidecar_key_for_object(primary.key, config=config)
    assert sidecar.key.endswith(".metadata.json")
    sidecar_value = json.loads(sidecar.body.decode("utf-8"))
    assert sidecar_value["object_key"] == primary.key
    assert sidecar_value["payload_info"]["sha256"]


@pytest.mark.asyncio
async def test_s3_sink_writes_primary_object_and_closes_client() -> None:
    client = FakeS3Client()
    sink = _sink_with_fake_client(client, config=_config(object_metadata={"purpose": "archive"}))

    await sink.start()
    await sink.write_batch([_envelope()])
    await sink.stop()

    assert client.closed is True
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request.bucket == "nats-sinks-events"
    assert request.if_none_match is True
    assert request.metadata["purpose"] == "archive"
    assert json.loads(request.body.decode("utf-8"))["payload"] == {
        "event_id": "S3-1",
        "status": "ok",
    }


@pytest.mark.asyncio
async def test_s3_sink_sidecar_heals_after_primary_duplicate() -> None:
    client = FakeS3Client()
    config = _config(metadata_mode="sidecar")
    sink = _sink_with_fake_client(client, config=config)
    envelope = _envelope()
    primary_key = s3_key_for_envelope(envelope, config=config)
    client.objects[primary_key] = b"already written"

    await sink.start()
    await sink.write_batch([envelope])

    assert len(client.calls) == 2
    assert client.calls[0].key == primary_key
    assert client.calls[1].key.endswith(".metadata.json")
    assert client.calls[1].key in client.objects


@pytest.mark.asyncio
async def test_s3_duplicate_policies_skip_replace_and_fail() -> None:
    envelope = _envelope()
    skip_client = FakeS3Client()
    skip_sink = _sink_with_fake_client(skip_client)
    await skip_sink.start()
    await skip_sink.write_batch([envelope, envelope])
    assert len(skip_client.objects) == 1

    replace_client = FakeS3Client()
    replace_sink = _sink_with_fake_client(
        replace_client,
        config=_config(duplicate_policy="replace"),
    )
    await replace_sink.start()
    await replace_sink.write_batch([envelope, envelope])
    assert [call.if_none_match for call in replace_client.calls] == [False, False]

    fail_client = FakeS3Client()
    fail_sink = _sink_with_fake_client(
        fail_client,
        config=_config(duplicate_policy="fail_existing"),
    )
    await fail_sink.start()
    await fail_sink.write_batch([envelope])
    with pytest.raises(PermanentSinkError, match="already exists"):
        await fail_sink.write_batch([envelope])


@pytest.mark.asyncio
async def test_s3_temporary_failure_retries_without_ack_semantics() -> None:
    client = FakeS3Client(failures=[DestinationUnavailableError("synthetic outage")])
    sink = _sink_with_fake_client(client, config=_config(max_retries=1, retry_backoff_ms=0))

    await sink.start()
    await sink.write_batch([_envelope()])

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_s3_permanent_failure_and_timeout_are_classified() -> None:
    permanent_sink = _sink_with_fake_client(
        FakeS3Client(failures=[PermanentSinkError("synthetic rejection")])
    )
    await permanent_sink.start()
    with pytest.raises(PermanentSinkError, match="synthetic rejection"):
        await permanent_sink.write_batch([_envelope()])

    timeout_sink = _sink_with_fake_client(
        FakeS3Client(delay_seconds=0.01),
        config=_config(request_timeout_seconds=0.001),
    )
    await timeout_sink.start()
    with pytest.raises(DestinationUnavailableError, match="timed out"):
        await timeout_sink.write_batch([_envelope()])


def test_s3_from_mapping_wraps_configuration_errors() -> None:
    with pytest.raises(ConfigurationError, match="bucket"):
        S3Sink.from_mapping({"type": "s3", "bucket": "Bad_Bucket"})


@pytest.mark.asyncio
async def test_s3_sink_passes_certification_helpers() -> None:
    client = FakeS3Client()

    def make_sink() -> Sink:
        return _sink_with_fake_client(client)

    def assert_written(_sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        assert len(client.objects) == len(messages)
        value = json.loads(next(iter(client.objects.values())).decode("utf-8"))
        assert value["subject"] == "certification.events.created"
        assert value["payload"] == {"event_id": "CERT-1", "status": "ok"}

    case = SinkCertificationCase(
        name="s3",
        sink_factory=make_sink,
        messages=(certification_envelope(),),
        duplicate_messages=(certification_envelope(),),
        after_write=assert_written,
        after_duplicate_write=assert_written,
    )

    await certify_sink_write_success(case)
    await certify_sink_duplicate_redelivery(case)


def test_s3_client_protocol_is_runtime_shape() -> None:
    client = FakeS3Client()
    assert isinstance(client, S3Client)
