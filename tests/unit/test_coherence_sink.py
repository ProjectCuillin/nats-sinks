# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.coherence import (
    CoherenceSink,
    CoherenceSinkConfig,
    coherence_key_for_envelope,
    coherence_value_for_envelope,
)
from nats_sinks.coherence import sink as coherence_sink_module
from nats_sinks.core.config import AppConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
)
from nats_sinks.sinks.base import Sink
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_write_success,
)


class FakeCoherenceCollection:
    def __init__(self, *, fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.store: dict[str, dict[str, Any]] = {}
        self.put_calls: list[tuple[str, dict[str, Any], int | None]] = []
        self.put_if_absent_calls: list[tuple[str, dict[str, Any], int | None]] = []

    async def put(self, key: str, value: dict[str, Any], ttl: int | None = None) -> Any:
        await self._maybe_wait_or_fail()
        previous = self.store.get(key)
        self.store[key] = value
        self.put_calls.append((key, value, ttl))
        return previous

    async def put_if_absent(
        self,
        key: str,
        value: dict[str, Any],
        ttl: int | None = None,
    ) -> Any:
        await self._maybe_wait_or_fail()
        previous = self.store.get(key)
        if previous is None:
            self.store[key] = value
        self.put_if_absent_calls.append((key, value, ttl))
        return previous

    async def _maybe_wait_or_fail(self) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("synthetic Coherence failure")


class FakeCoherenceSession:
    def __init__(self, collection: FakeCoherenceCollection) -> None:
        self.collection = collection
        self.closed = False
        self.cache_names: list[str] = []
        self.map_names: list[str] = []

    def get_cache(self, name: str) -> FakeCoherenceCollection:
        self.cache_names.append(name)
        return self.collection

    async def get_map(self, name: str) -> FakeCoherenceCollection:
        self.map_names.append(name)
        return self.collection

    def close(self) -> None:
        self.closed = True


def _envelope(
    *,
    data: bytes = b'{"event_id":"COHERENCE-1","status":"ok"}',
    message_id: str | None = "coherence-message-1",
    stream: str | None = "COHERENCE",
    stream_sequence: int | None = 42,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="mission.sensor.alpha",
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="coherence-consumer",
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


def _sink_with_fake_collection(
    collection: FakeCoherenceCollection,
    *,
    config: CoherenceSinkConfig | None = None,
) -> CoherenceSink:
    effective_config = config or CoherenceSinkConfig(cache_name="events", ttl_seconds=60)
    return CoherenceSink(
        config=effective_config,
        session_factory=lambda _config: FakeCoherenceSession(collection),
    )


def test_coherence_config_rejects_unsafe_values() -> None:
    with pytest.raises(PydanticValidationError, match="host:port"):
        CoherenceSinkConfig(address="https://coherence.example.invalid:1408")

    with pytest.raises(PydanticValidationError, match="cache_name"):
        CoherenceSinkConfig(cache_name="../events")

    with pytest.raises(PydanticValidationError, match="ttl_seconds"):
        CoherenceSinkConfig(storage="map", ttl_seconds=60)

    with pytest.raises(PydanticValidationError, match="serializer"):
        CoherenceSinkConfig.model_validate({"type": "coherence", "serializer": "pickle"})


def test_coherence_optional_fanout_targets_receive_safe_defaults() -> None:
    config = AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "EVENTS",
                "consumer": "coherence-sink",
                "subject": "events.>",
            },
            "sink": {"type": "fanout"},
            "sinks": {"coherence_audit": {"type": "coherence"}},
            "routing": {
                "enabled": True,
                "routes": [
                    {
                        "name": "audit",
                        "match": {"subject": "events.>"},
                        "targets": [
                            {
                                "sink": "coherence_audit",
                                "required": False,
                            }
                        ],
                    }
                ],
            },
        }
    )

    target = config.routing.routes[0].targets[0]
    assert config.routing.target_sink_types == {"coherence_audit": "coherence"}
    assert target.minimum_wait_ms == 1_000
    assert target.timeout_ms == 5_000


def test_coherence_value_preserves_full_event_metadata() -> None:
    stored_at = datetime(2026, 5, 28, 12, 5, tzinfo=UTC)
    value = coherence_value_for_envelope(
        _envelope(),
        config=CoherenceSinkConfig(),
        stored_at=stored_at,
    )

    assert value["schema"] == "nats_sinks.coherence.event.v1"
    assert value["schema_version"] == 1
    assert value["subject"] == "mission.sensor.alpha"
    assert value["stream"] == "COHERENCE"
    assert value["stream_sequence"] == 42
    assert value["message_id"] == "coherence-message-1"
    assert value["priority"] == "high"
    assert value["classification"] == "NATO SECRET"
    assert value["labels"] == "sensor;audit"
    assert value["payload"] == {"event_id": "COHERENCE-1", "status": "ok"}
    assert value["mission_metadata"] == {"phase": "find", "profile": "example"}
    assert value["security_labels"] == {
        "classification": "NATO SECRET",
        "profile": "demo",
    }
    assert value["stored_at_epoch_ns"] == 1_779_969_900_000_000_000
    assert value["metadata"]["timestamps"]["stored_at_epoch_ns"] == 1_779_969_900_000_000_000


def test_coherence_key_strategies_are_deterministic_and_bounded() -> None:
    envelope = _envelope()

    assert coherence_key_for_envelope(envelope, config=CoherenceSinkConfig()) == (
        "stream-sequence:COHERENCE:42"
    )
    assert (
        coherence_key_for_envelope(
            envelope,
            config=CoherenceSinkConfig(key_strategy="message_id", key_prefix="ns"),
        )
        == "ns:message-id:coherence-message-1"
    )
    assert coherence_key_for_envelope(
        envelope,
        config=CoherenceSinkConfig(key_strategy="payload_sha256"),
    ).startswith("payload-sha256:mission.sensor.alpha:")

    with pytest.raises(SerializationError, match="requires stream metadata"):
        coherence_key_for_envelope(
            _envelope(stream=None, stream_sequence=None),
            config=CoherenceSinkConfig(key_strategy="stream_sequence"),
        )

    with pytest.raises(SerializationError, match="requires a message ID"):
        coherence_key_for_envelope(
            _envelope(message_id=None),
            config=CoherenceSinkConfig(key_strategy="message_id"),
        )

    with pytest.raises(SerializationError, match="max_key_bytes"):
        coherence_key_for_envelope(
            envelope,
            config=CoherenceSinkConfig(key_prefix="p" * 128, max_key_bytes=64),
        )


@pytest.mark.asyncio
async def test_coherence_sink_puts_complete_value_with_ttl() -> None:
    collection = FakeCoherenceCollection()
    session = FakeCoherenceSession(collection)
    sink = CoherenceSink(
        config=CoherenceSinkConfig(cache_name="events", ttl_seconds=60),
        session_factory=lambda _config: session,
    )

    await sink.start()
    await sink.write_batch([_envelope()])
    await sink.stop()

    assert session.cache_names == ["events"]
    assert session.closed is True
    assert len(collection.put_if_absent_calls) == 1
    key, value, ttl = collection.put_if_absent_calls[0]
    assert key == "stream-sequence:COHERENCE:42"
    assert ttl == 60
    assert value["payload"] == {"event_id": "COHERENCE-1", "status": "ok"}


@pytest.mark.asyncio
async def test_coherence_sink_skip_existing_preserves_prior_value() -> None:
    collection = FakeCoherenceCollection()
    collection.store["stream-sequence:COHERENCE:42"] = {"already": "committed"}
    sink = _sink_with_fake_collection(collection)

    await sink.start()
    await sink.write_batch([_envelope()])

    assert collection.store["stream-sequence:COHERENCE:42"] == {"already": "committed"}
    assert len(collection.put_if_absent_calls) == 1


@pytest.mark.asyncio
async def test_coherence_sink_fail_existing_rejects_duplicate() -> None:
    collection = FakeCoherenceCollection()
    collection.store["stream-sequence:COHERENCE:42"] = {"already": "committed"}
    sink = _sink_with_fake_collection(
        collection,
        config=CoherenceSinkConfig(duplicate_policy="fail_existing"),
    )

    await sink.start()

    with pytest.raises(PermanentSinkError, match="already exists"):
        await sink.write_batch([_envelope()])


@pytest.mark.asyncio
async def test_coherence_sink_replace_overwrites_existing_value() -> None:
    collection = FakeCoherenceCollection()
    collection.store["stream-sequence:COHERENCE:42"] = {"old": True}
    sink = _sink_with_fake_collection(
        collection,
        config=CoherenceSinkConfig(duplicate_policy="replace"),
    )

    await sink.start()
    await sink.write_batch([_envelope()])

    assert len(collection.put_calls) == 1
    assert collection.store["stream-sequence:COHERENCE:42"]["payload"] == {
        "event_id": "COHERENCE-1",
        "status": "ok",
    }


@pytest.mark.asyncio
async def test_coherence_sink_uses_named_map_without_ttl() -> None:
    collection = FakeCoherenceCollection()
    session = FakeCoherenceSession(collection)
    sink = CoherenceSink(
        config=CoherenceSinkConfig(storage="map", cache_name="event_map"),
        session_factory=lambda _config: session,
    )

    await sink.start()
    await sink.write_batch([_envelope()])

    assert session.map_names == ["event_map"]
    assert collection.put_if_absent_calls[0][2] is None


@pytest.mark.asyncio
async def test_coherence_sink_failures_do_not_return_success() -> None:
    failing = _sink_with_fake_collection(FakeCoherenceCollection(fail=True))

    await failing.start()

    with pytest.raises(DestinationUnavailableError, match="batch write failed"):
        await failing.write_batch([_envelope()])

    slow = _sink_with_fake_collection(
        FakeCoherenceCollection(delay_seconds=0.1),
        config=CoherenceSinkConfig(request_timeout_seconds=0.01),
    )
    await slow.start()

    with pytest.raises(DestinationUnavailableError, match="write timed out"):
        await slow.write_batch([_envelope()])


@pytest.mark.asyncio
async def test_coherence_sink_rejects_oversized_values_before_write() -> None:
    collection = FakeCoherenceCollection()
    sink = _sink_with_fake_collection(collection, config=CoherenceSinkConfig(max_value_bytes=10))

    await sink.start()

    with pytest.raises(SerializationError, match="max_value_bytes"):
        await sink.write_batch([_envelope()])

    assert collection.put_calls == []
    assert collection.put_if_absent_calls == []


@pytest.mark.asyncio
async def test_coherence_sink_missing_optional_dependency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(name: str) -> object:
        if name == "coherence":
            raise ImportError("missing test dependency")
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(coherence_sink_module.importlib, "import_module", fail_import)

    sink = CoherenceSink.from_mapping({"type": "coherence"})

    with pytest.raises(ConfigurationError, match=r"nats-sinks\[coherence\]"):
        await sink.start()


@pytest.mark.asyncio
async def test_coherence_sink_passes_certification_helpers() -> None:
    collection = FakeCoherenceCollection()

    def make_sink() -> Sink:
        return _sink_with_fake_collection(collection)

    def assert_written(_sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        assert len(collection.store) == len(messages)
        first = collection.store["stream-sequence:CERTIFICATION:1"]
        assert first["subject"] == "certification.events.created"
        assert first["payload"] == {"event_id": "CERT-1", "status": "ok"}

    case = SinkCertificationCase(
        name="coherence",
        sink_factory=make_sink,
        messages=(certification_envelope(),),
        duplicate_messages=(certification_envelope(),),
        after_write=assert_written,
        after_duplicate_write=assert_written,
    )

    await certify_sink_write_success(case)
    await certify_sink_duplicate_redelivery(case)
