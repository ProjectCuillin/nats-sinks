# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from nats_sinks import (
    DestinationUnavailableError,
    NatsEnvelope,
    PermanentSinkError,
    TemporarySinkError,
)
from nats_sinks.core.config import DeadLetterConfig, DeliveryConfig
from nats_sinks.core.errors import DeadLetterError
from nats_sinks.core.runner import JetStreamSinkRunner


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "ORDERS"
    consumer: str = "oracle"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    def __init__(self, events: list[str], *, data: bytes = b'{"order_id":"O-1001"}') -> None:
        self.subject = "orders.created"
        self.data = data
        self.headers = {"Nats-Msg-Id": "m-1"}
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


class RecordingSink:
    def __init__(self, events: list[str], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        assert len(messages) == 1
        self.events.append("write")
        if self.error is not None:
            raise self.error
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class BatchRecordingSink:
    """Test sink that records the exact batch sizes delivered by the runner."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.batch_sizes: list[int] = []

    async def start(self) -> None:
        self.events.append("sink_start")

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.batch_sizes.append(len(messages))
        self.events.append(f"write_{len(messages)}")
        self.events.append("commit")

    async def stop(self) -> None:
        self.events.append("sink_stop")


class JsonParsingSink:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.events.append("write")
        for message in messages:
            message.payload_as_json()
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class ExplodingSink:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        assert len(messages) == 1
        self.events.append("write")
        raise RuntimeError("unexpected database driver failure")

    async def stop(self) -> None:
        return None


class FakeJetStream:
    def __init__(self, events: list[str], fail_publish: bool = False) -> None:
        self.events = events
        self.fail_publish = fail_publish
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        del headers
        self.events.append("dlq")
        if self.fail_publish:
            raise RuntimeError("publish failed")
        self.published.append((subject, payload))


class PartialFetchSubscription:
    """Fake pull subscription that returns fewer messages than requested.

    The production runner delegates waiting behavior to `nats-py` fetch. This
    fake proves that once a partial batch is returned, the runner writes and
    ACKs that smaller batch immediately instead of imposing its own full-batch
    requirement.
    """

    def __init__(self, messages: Sequence[FakeMessage], runner: JetStreamSinkRunner) -> None:
        self.messages = list(messages)
        self.runner = runner
        self.fetch_calls: list[tuple[int, float]] = []

    async def fetch(self, batch: int, **options: float) -> list[FakeMessage]:
        self.fetch_calls.append((batch, options["timeout"]))
        self.runner.request_stop()
        return self.messages


class PullSubscribeJetStream:
    def __init__(self, subscription: PartialFetchSubscription) -> None:
        self.subscription = subscription

    async def pull_subscribe(
        self,
        subject: str,
        *,
        durable: str | None,
        stream: str,
    ) -> PartialFetchSubscription:
        del subject, durable, stream
        return self.subscription


@pytest.mark.asyncio
async def test_sink_success_triggers_ack_after_commit() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert message.acked


@pytest.mark.asyncio
async def test_runner_writes_partial_fetch_before_batch_size_is_reached() -> None:
    events: list[str] = []
    messages = [FakeMessage(events) for _ in range(17)]
    sink = BatchRecordingSink(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        delivery=DeliveryConfig(batch_size=64, batch_timeout_ms=250),
        jetstream=None,
    )
    subscription = PartialFetchSubscription(messages, runner)
    runner._js = PullSubscribeJetStream(subscription)

    await runner.run()

    assert subscription.fetch_calls == [(64, 0.25)]
    assert sink.batch_sizes == [17]
    assert events.count("ack") == 17
    assert all(message.acked for message in messages)
    assert "write_17" in events


@pytest.mark.asyncio
async def test_temporary_failure_does_not_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("try again")),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "nak"]
    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_unexpected_sink_failure_does_not_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=ExplodingSink(events),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "nak"]
    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_message_normalization_failure_does_not_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    message = FakeMessage(events)

    def fail_normalization(_raw_message: object) -> NatsEnvelope:
        raise RuntimeError("broken client message")

    monkeypatch.setattr(
        "nats_sinks.core.runner.envelope_from_nats_message",
        fail_normalization,
    )
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
    )

    await runner.process_raw_batch([message])

    assert events == ["nak"]
    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_permanent_failure_publishes_dlq_before_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"


@pytest.mark.asyncio
async def test_malformed_json_payload_goes_to_dlq_before_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events, data=b"{not-json")
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=JsonParsingSink(events),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"


@pytest.mark.asyncio
async def test_dlq_publish_failure_does_not_ack_original() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events, fail_publish=True),
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["write", "dlq"]
    assert not message.acked


@pytest.mark.asyncio
async def test_oracle_commit_failure_does_not_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, DestinationUnavailableError("Oracle commit failed")),
        delivery=DeliveryConfig(temporary_failure_action="leave_unacked"),
    )

    await runner.process_raw_batch([message])

    assert events == ["write"]
    assert not message.acked
    assert not message.nacked
