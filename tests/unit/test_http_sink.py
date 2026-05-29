# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
)
from nats_sinks.http import (
    HttpRequest,
    HttpResponse,
    HttpSink,
    HttpSinkConfig,
    http_envelope_value,
    http_idempotency_key,
    prepare_http_body,
)
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_lifecycle,
    certify_sink_write_success,
)


class FakeHttpClient:
    def __init__(
        self,
        responses: Sequence[HttpResponse] | None = None,
        errors: Sequence[BaseException] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.calls: list[tuple[HttpRequest, float, int]] = []

    async def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        self.calls.append((request, timeout_seconds, max_response_bytes))
        if self.errors:
            raise self.errors.pop(0)
        if self.responses:
            return self.responses.pop(0)
        return HttpResponse(status=202, body=b'{"accepted":true}')


def _config(**overrides: Any) -> HttpSinkConfig:
    values: dict[str, Any] = {
        "url": "https://events.example.invalid/nats-sink",
        "endpoint_allowed_hosts": ["events.example.invalid"],
        "retry": {"max_retries": 0},
    }
    values.update(overrides)
    return HttpSinkConfig.model_validate(values)


def _envelope(
    *,
    stream_sequence: int = 1,
    message_id: str | None = "message-1",
    data: bytes = b'{"event_id":"EVT-1","status":"ok"}',
) -> NatsEnvelope:
    return certification_envelope(
        subject="integration.http.event",
        stream="HTTP_EVENTS",
        stream_sequence=stream_sequence,
        message_id=message_id,
        data=data,
        priority="normal",
        classification="unclassified",
        labels=("http", "certification"),
    )


def test_http_config_requires_https_unless_loopback_testing() -> None:
    with pytest.raises(ValueError, match="https outside local loopback"):
        _config(url="http://events.example.invalid/nats-sink")

    config = _config(
        url="http://127.0.0.1:18080/nats-sink",
        allow_http_for_local_testing=True,
        endpoint_allowed_hosts=[],
    )

    assert config.url.startswith("http://127.0.0.1")


def test_http_config_validates_endpoint_headers_and_statuses() -> None:
    with pytest.raises(ValueError, match="host is not in endpoint_allowed_hosts"):
        _config(endpoint_allowed_hosts=["other.example.invalid"])

    with pytest.raises(ValueError, match="sensitive header"):
        _config(headers={"Authorization": "Bearer placeholder"})

    env_header = _config(headers_env={"Authorization": "HTTP_TOKEN"})
    assert env_header.headers_env == {"Authorization": "HTTP_TOKEN"}

    with pytest.raises(ValueError, match="overlap"):
        _config(success_statuses=[202], retry_statuses=[202])

    with pytest.raises(ValueError, match="framework-owned header"):
        _config(headers={"Content-Length": "10"})


def test_http_mapping_preserves_payload_metadata_and_idempotency() -> None:
    envelope = _envelope()
    config = _config()

    value = http_envelope_value(envelope, config=config)
    prepared = prepare_http_body(envelope, config=config)

    assert value["schema"] == "nats_sinks.http.message.v1"
    assert value["idempotency_key"] == "stream-sequence:HTTP_EVENTS:1"
    assert value["subject"] == "integration.http.event"
    assert value["payload"] == {"event_id": "EVT-1", "status": "ok"}
    assert value["payload_info"]["sha256"]
    assert value["labels_list"] == ["http", "certification"]
    assert isinstance(value["metadata"], dict)
    assert prepared.idempotency_key == value["idempotency_key"]
    assert prepared.body.startswith(b'{"classification"')


def test_http_payload_body_format_sends_only_normalized_payload() -> None:
    prepared = prepare_http_body(_envelope(), config=_config(body_format="payload"))

    assert prepared.body == b'{"event_id":"EVT-1","status":"ok"}'


def test_http_idempotency_strategies_fail_closed_when_required_metadata_is_missing() -> None:
    envelope = _envelope(message_id=None)

    assert http_idempotency_key(
        envelope,
        config=_config(idempotency={"strategy": "idempotency_key"}),
    )

    with pytest.raises(PermanentSinkError, match="requires a message ID"):
        http_idempotency_key(envelope, config=_config(idempotency={"strategy": "message_id"}))

    with pytest.raises(PermanentSinkError, match="requires stream metadata"):
        http_idempotency_key(
            certification_envelope(stream=None, stream_sequence=None),
            config=_config(idempotency={"strategy": "stream_sequence"}),
        )

    optional = http_idempotency_key(
        certification_envelope(stream=None, stream_sequence=None),
        config=_config(idempotency={"strategy": "stream_sequence", "required": False}),
    )
    assert optional is None


def test_http_mapping_rejects_oversized_request_body() -> None:
    with pytest.raises(PermanentSinkError, match="max_request_bytes"):
        prepare_http_body(
            _envelope(data=b'{"large":"' + (b"x" * 2048) + b'"}'),
            config=_config(max_request_bytes=512),
        )


@pytest.mark.asyncio
async def test_http_sink_sends_request_with_env_backed_idempotent_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_AUTHORIZATION", "Bearer placeholder-token")
    client = FakeHttpClient()
    sink = HttpSink(
        url=_config().url,
        config=_config(
            headers={"X-Static-Route": "internal"},
            headers_env={"Authorization": "HTTP_AUTHORIZATION"},
        ),
        client=client,
    )

    await sink.start()
    await sink.write_batch((_envelope(),))
    await sink.stop()

    request, timeout_seconds, max_response_bytes = client.calls[0]
    assert request.method == "POST"
    assert request.url == "https://events.example.invalid/nats-sink"
    assert request.headers["Idempotency-Key"] == "stream-sequence:HTTP_EVENTS:1"
    assert request.headers["Authorization"] == "Bearer placeholder-token"
    assert request.headers["X-Static-Route"] == "internal"
    assert timeout_seconds == 10.0
    assert max_response_bytes == 65_536


@pytest.mark.asyncio
async def test_http_sink_retries_retryable_status_when_configured() -> None:
    client = FakeHttpClient(
        responses=[
            HttpResponse(status=503, body=b""),
            HttpResponse(status=202, body=b""),
        ]
    )
    sink = HttpSink(
        url=_config().url,
        config=_config(retry={"max_retries": 1, "backoff_ms": 0, "jitter": "none"}),
        client=client,
    )

    await sink.write_batch((_envelope(),))

    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_http_sink_classifies_permanent_and_temporary_failures() -> None:
    permanent = HttpSink(
        url=_config().url,
        config=_config(),
        client=FakeHttpClient(responses=[HttpResponse(status=400, body=b"")]),
    )

    with pytest.raises(PermanentSinkError, match="non-success HTTP status 400"):
        await permanent.write_batch((_envelope(),))

    temporary = HttpSink(
        url=_config().url,
        config=_config(),
        client=FakeHttpClient(responses=[HttpResponse(status=503, body=b"")]),
    )

    with pytest.raises(DestinationUnavailableError, match="retryable HTTP status 503"):
        await temporary.write_batch((_envelope(),))


@pytest.mark.asyncio
async def test_http_sink_treats_missing_env_header_as_configuration_error() -> None:
    sink = HttpSink(
        url=_config().url,
        config=_config(headers_env={"Authorization": "HTTP_MISSING_TOKEN"}),
        client=FakeHttpClient(),
    )

    with pytest.raises(ConfigurationError, match="environment variable is not set"):
        await sink.write_batch((_envelope(),))


@pytest.mark.asyncio
async def test_http_sink_preserves_client_timeout_as_temporary_failure() -> None:
    sink = HttpSink(
        url=_config().url,
        config=_config(),
        client=FakeHttpClient(errors=[TimeoutError("timed out")]),
    )

    with pytest.raises(DestinationUnavailableError, match="timed out"):
        await sink.write_batch((_envelope(),))


def _certification_case(client: FakeHttpClient) -> SinkCertificationCase:
    def make_sink() -> HttpSink:
        return HttpSink(
            url=_config().url,
            config=_config(),
            client=client,
        )

    return SinkCertificationCase(
        name="http",
        sink_factory=make_sink,
        messages=(_envelope(),),
        duplicate_messages=(_envelope(),),
    )


@pytest.mark.asyncio
async def test_http_sink_satisfies_sink_lifecycle_and_write_certification() -> None:
    client = FakeHttpClient()
    case = _certification_case(client)

    await certify_sink_lifecycle(case)
    await certify_sink_write_success(case)

    assert client.calls


@pytest.mark.asyncio
async def test_http_sink_duplicate_redelivery_reuses_idempotency_key() -> None:
    client = FakeHttpClient()
    await certify_sink_duplicate_redelivery(_certification_case(client))

    keys = [call[0].headers["Idempotency-Key"] for call in client.calls]
    assert keys == ["stream-sequence:HTTP_EVENTS:1", "stream-sequence:HTTP_EVENTS:1"]
