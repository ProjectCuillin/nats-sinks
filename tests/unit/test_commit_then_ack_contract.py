# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nats_sinks import (
    DestinationUnavailableError,
    NatsEnvelope,
    PermanentSinkError,
    TemporarySinkError,
)
from nats_sinks.core.config import (
    AckConfirmationConfig,
    ConfigurationError,
    ConsumerManagementConfig,
    CustodyConfig,
    DeadLetterConfig,
    DeliveryConfig,
    InProgressConfig,
    JetStreamAdvisoryConfig,
    MessageMetadataConfig,
    MetricsConfig,
    MissionMetadataConfig,
    PreSinkPolicyConfig,
    PriorityLaneConfig,
    PriorityLanesConfig,
    SecurityLabelProfileConfig,
    SizePolicyConfig,
)
from nats_sinks.core.errors import AckError, DeadLetterError
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
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
        self.termed = False
        self.nak_delays: list[float | None] = []

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.events.append("nak")
        self.nacked = True
        self.nak_delays.append(delay)

    async def term(self) -> None:
        self.events.append("term")
        self.termed = True

    async def in_progress(self) -> None:
        self.events.append("in_progress")


class ConfirmedAckMessage(FakeMessage):
    async def ack_sync(self, **kwargs: float) -> object:
        timeout = kwargs.get("timeout", 1.0)
        self.events.append(f"ack_sync:{timeout:.3f}")
        self.acked = True
        return self


class ConfirmedAckTimeoutMessage(FakeMessage):
    async def ack_sync(self, **kwargs: float) -> object:
        timeout = kwargs.get("timeout", 1.0)
        self.events.append(f"ack_sync_timeout:{timeout:.3f}")
        raise TimeoutError("ack confirmation timed out")


class ConfirmedAckFailingMessage(FakeMessage):
    async def ack_sync(self, **kwargs: float) -> object:
        timeout = kwargs.get("timeout", 1.0)
        self.events.append(f"ack_sync_failed:{timeout:.3f}")
        raise RuntimeError("ack confirmation failed")


class AckFailingMessage(FakeMessage):
    async def ack(self) -> None:
        self.events.append("ack_failed")
        raise RuntimeError("ack connection closed")


class TermFailingMessage(FakeMessage):
    async def term(self) -> None:
        self.events.append("term_failed")
        raise RuntimeError("term connection closed")


class InProgressFailingMessage(FakeMessage):
    async def in_progress(self) -> None:
        self.events.append("in_progress_failed")
        raise RuntimeError("in-progress connection closed")


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


class MetadataRecordingSink:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[NatsEnvelope] = []

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages.extend(messages)
        self.events.append("write")
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class FailingPayloadEncryptor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def encrypt_batch(self, envelopes: Sequence[NatsEnvelope]) -> list[NatsEnvelope]:
        del envelopes
        self.events.append("encrypt")
        raise RuntimeError("crypto provider unavailable")


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


class SlowRecordingSink(RecordingSink):
    def __init__(
        self,
        events: list[str],
        *,
        delay_seconds: float = 0.02,
        error: BaseException | None = None,
    ) -> None:
        super().__init__(events, error=error)
        self.delay_seconds = delay_seconds

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        assert len(messages) == 1
        self.events.append("write")
        await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        self.events.append("commit")


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


class FakeAdvisorySubscription:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def unsubscribe(self) -> None:
        self.events.append("advisory_unsubscribe")


class FakeNatsConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.subscribed_subjects: list[str] = []

    async def subscribe(self, subject: str, *, cb: object) -> FakeAdvisorySubscription:
        del cb
        self.events.append("advisory_subscribe")
        self.subscribed_subjects.append(subject)
        return FakeAdvisorySubscription(self.events)

    async def close(self) -> None:
        self.events.append("nats_close")


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

    async def consumer_info(self, stream: str, consumer: str) -> object:
        return SimpleNamespace(
            config=SimpleNamespace(
                name=consumer,
                durable_name=consumer,
                filter_subject="orders.*",
                filter_subjects=None,
                ack_policy="explicit",
                deliver_policy="all",
                replay_policy="instant",
                deliver_subject=None,
                headers_only=None,
                ack_wait=None,
                max_deliver=None,
                max_ack_pending=None,
                max_waiting=None,
            )
        )

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
async def test_advisory_monitor_isolated_from_sink_ack_order() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    nats_connection = FakeNatsConnection(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        jetstream=FakeJetStream(events),
        nats_connection=nats_connection,
        advisories=JetStreamAdvisoryConfig(
            enabled=True,
            subjects=("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",),
        ),
    )

    await runner.start()
    await runner.process_raw_batch([message])
    await runner.stop()

    assert events == [
        "advisory_subscribe",
        "write",
        "commit",
        "ack",
        "advisory_unsubscribe",
        "nats_close",
    ]
    assert nats_connection.subscribed_subjects == ["$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*"]
    assert message.acked


@pytest.mark.asyncio
async def test_successful_batch_records_clear_metrics_without_affecting_ack_order() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert metrics.counters[MetricNames.MESSAGES_FETCHED_TOTAL] == 1
    assert metrics.counters[MetricNames.BATCHES_FETCHED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_PREPARED_TOTAL] == 1
    assert metrics.counters[MetricNames.LEGACY_MESSAGES_RECEIVED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_BATCHES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.LEGACY_BATCHES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1
    assert metrics.observations[MetricNames.SINK_BATCH_WRITE_SECONDS]
    assert metrics.observations[MetricNames.LEGACY_BATCH_WRITE_SECONDS]
    assert metrics.gauges[MetricNames.CURRENT_BATCH_MESSAGES] == 1.0
    assert metrics.gauges[MetricNames.LEGACY_CURRENT_BATCH_SIZE] == 1.0
    assert MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS in metrics.gauges
    assert MetricNames.LEGACY_LAST_SUCCESS_TIMESTAMP in metrics.gauges


@pytest.mark.asyncio
async def test_in_progress_heartbeat_runs_only_during_active_sink_write() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(events, delay_seconds=0.03),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=50,
                shutdown_timeout_ms=1000,
            )
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events[0] == "write"
    assert "in_progress" in events
    assert events[-2:] == ["commit", "ack"]
    assert events.index("write") < events.index("in_progress") < events.index("commit")
    assert message.acked
    assert metrics.counters[MetricNames.IN_PROGRESS_ATTEMPTS_TOTAL] >= 1
    assert metrics.counters[MetricNames.IN_PROGRESS_SUCCESSES_TOTAL] >= 1
    assert metrics.counters[MetricNames.IN_PROGRESS_FAILURES_TOTAL] == 0
    assert metrics.gauges[MetricNames.CURRENT_IN_PROGRESS_BATCHES_ACTIVE] == 0.0
    assert metrics.observations[MetricNames.IN_PROGRESS_HEARTBEAT_SECONDS]


def test_in_progress_heartbeat_requires_ack_wait_at_runner_boundary() -> None:
    events: list[str] = []

    with pytest.raises(
        ConfigurationError,
        match=r"requires consumer_management\.ack_wait_seconds",
    ):
        JetStreamSinkRunner(
            nats_url="nats://localhost:4222",
            stream="ORDERS",
            consumer="oracle",
            subject="orders.*",
            sink=SlowRecordingSink(events),
            delivery=DeliveryConfig(
                in_progress=InProgressConfig(
                    enabled=True,
                    interval_ms=1,
                    max_heartbeats=1,
                )
            ),
        )


@pytest.mark.asyncio
async def test_in_progress_heartbeat_does_not_replace_temporary_failure_path() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(events, delay_seconds=0.03, error=TemporarySinkError("try again")),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            retry_jitter="none",
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=50,
                shutdown_timeout_ms=1000,
            ),
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert "in_progress" in events
    assert events[-1] == "nak"
    assert not message.acked
    assert message.nacked
    assert metrics.counters[MetricNames.IN_PROGRESS_SUCCESSES_TOTAL] >= 1
    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_NACKED_TOTAL] == 1


@pytest.mark.asyncio
async def test_in_progress_heartbeat_stops_before_permanent_failure_dlq_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(
            events,
            delay_seconds=0.03,
            error=PermanentSinkError("invalid mission record"),
        ),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=50,
                shutdown_timeout_ms=1000,
            )
        ),
        dead_letter=DeadLetterConfig(enabled=True, subject="ORDERS.DLQ"),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert "in_progress" in events
    assert events[-2:] == ["dlq", "ack"]
    assert events.index("in_progress") < events.index("dlq")
    assert message.acked
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.gauges[MetricNames.CURRENT_IN_PROGRESS_BATCHES_ACTIVE] == 0.0


@pytest.mark.asyncio
async def test_in_progress_heartbeat_failure_is_metric_only_and_does_not_ack_early() -> None:
    events: list[str] = []
    message = InProgressFailingMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(events, delay_seconds=0.03),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=50,
                shutdown_timeout_ms=1000,
            )
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "in_progress_failed", "commit", "ack"]
    assert message.acked
    assert metrics.counters[MetricNames.IN_PROGRESS_ATTEMPTS_TOTAL] == 1
    assert metrics.counters[MetricNames.IN_PROGRESS_SUCCESSES_TOTAL] == 0
    assert metrics.counters[MetricNames.IN_PROGRESS_FAILURES_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1


@pytest.mark.asyncio
async def test_in_progress_heartbeat_stops_at_bounded_maximum_count() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(events, delay_seconds=0.03),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=2,
                shutdown_timeout_ms=1000,
            )
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events.count("in_progress") == 2
    assert events[-2:] == ["commit", "ack"]
    assert metrics.counters[MetricNames.IN_PROGRESS_ATTEMPTS_TOTAL] == 2
    assert metrics.counters[MetricNames.IN_PROGRESS_SUCCESSES_TOTAL] == 2
    assert metrics.counters[MetricNames.IN_PROGRESS_MAX_HEARTBEATS_REACHED_TOTAL] == 1
    assert metrics.gauges[MetricNames.CURRENT_IN_PROGRESS_BATCHES_ACTIVE] == 0.0


@pytest.mark.asyncio
async def test_in_progress_heartbeat_stops_when_processing_is_cancelled() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=SlowRecordingSink(events, delay_seconds=1.0),
        consumer_management=ConsumerManagementConfig(ack_wait_seconds=1.0),
        delivery=DeliveryConfig(
            in_progress=InProgressConfig(
                enabled=True,
                interval_ms=1,
                max_heartbeats=50,
                shutdown_timeout_ms=1000,
            )
        ),
        metrics=metrics,
    )

    task = asyncio.create_task(runner.process_raw_batch([message]))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "in_progress" in events
    assert not message.acked
    assert not message.nacked
    assert metrics.gauges[MetricNames.CURRENT_IN_PROGRESS_BATCHES_ACTIVE] == 0.0


@pytest.mark.asyncio
async def test_successful_batch_records_freshness_metrics_before_ack_without_changing_order() -> (
    None
):
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {"Nats-Time-Stamp": "2020-01-01T00:00:00Z"}
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        metrics=metrics,
        metrics_config=MetricsConfig(event_stale_after_seconds=1.0),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert metrics.observations[MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS]
    assert metrics.observations[MetricNames.EVENT_AGE_AT_STORE_SECONDS]
    assert metrics.counters[MetricNames.EVENTS_STALE_AT_RECEIVE_TOTAL] == 1


@pytest.mark.asyncio
async def test_disabled_freshness_metrics_do_not_emit_runner_freshness_values() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.metadata.timestamp = datetime(2026, 5, 23, 10, 0, tzinfo=UTC)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        metrics=metrics,
        metrics_config=MetricsConfig(event_freshness_enabled=False),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS not in metrics.observations
    assert MetricNames.EVENT_CREATION_TIMESTAMP_MISSING_TOTAL not in metrics.counters


@pytest.mark.asyncio
async def test_runner_applies_priority_and_classification_before_sink_write() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {"X-Priority": "high"}
    sink = MetadataRecordingSink(events)
    metadata_config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "classification": {
                "header": "X-Classification",
                "default": "internal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default;orders",
            },
        }
    )
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        message_metadata=metadata_config,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert sink.messages[0].priority == "high"
    assert sink.messages[0].classification == "internal"
    assert sink.messages[0].labels == ("default", "orders")
    assert message.acked


@pytest.mark.asyncio
async def test_priority_lanes_reorder_sink_batch_without_ack_before_commit() -> None:
    events: list[str] = []
    routine = FakeMessage(events)
    routine.headers = {"X-Priority": "routine"}
    routine.metadata.sequence = FakeSequence(stream=1, consumer=1)
    urgent = FakeMessage(events)
    urgent.headers = {"X-Priority": "urgent"}
    urgent.metadata.sequence = FakeSequence(stream=2, consumer=2)
    sink = MetadataRecordingSink(events)
    metadata_config = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "routine",
            },
        }
    )
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        message_metadata=metadata_config,
        delivery=DeliveryConfig(
            priority_lanes=PriorityLanesConfig(
                enabled=True,
                default_lane="routine",
                lanes=[
                    PriorityLaneConfig(name="urgent", priorities=("urgent",), weight=2),
                    PriorityLaneConfig(name="routine", priorities=("routine",), weight=1),
                ],
            )
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([routine, urgent])

    assert [message.stream_sequence for message in sink.messages] == [2, 1]
    assert events == ["write", "commit", "ack", "ack"]
    assert routine.acked
    assert urgent.acked
    assert metrics.counters[MetricNames.PRIORITY_LANE_MESSAGES_TOTAL] == 2
    assert metrics.gauges[MetricNames.CURRENT_PRIORITY_LANES_ACTIVE] == 2.0


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
async def test_temporary_failure_records_failure_and_nak_metrics() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("try again")),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert not message.acked
    assert message.nacked
    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_NACKED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_temporary_failure_uses_exponential_backoff_delay_from_delivery_attempt() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.metadata.num_delivered = 3
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("try again")),
        delivery=DeliveryConfig(
            retry_backoff_ms=1000,
            retry_backoff_max_ms=10_000,
            retry_backoff_mode="exponential",
            retry_backoff_multiplier=2.0,
            retry_jitter="none",
        ),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "nak"]
    assert message.nak_delays == [4.0]
    assert not message.acked


@pytest.mark.asyncio
async def test_temporary_failure_retry_jitter_can_be_disabled_for_predictable_operations() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.metadata.num_delivered = 2
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("try again")),
        delivery=DeliveryConfig(
            retry_backoff_ms=500,
            retry_backoff_max_ms=10_000,
            retry_backoff_mode="linear",
            retry_jitter="none",
        ),
    )

    await runner.process_raw_batch([message])

    assert message.nak_delays == [1.0]


@pytest.mark.asyncio
async def test_temporary_failure_does_not_nak_when_active_retry_budget_is_exhausted() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.metadata.num_delivered = 3
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("try again")),
        delivery=DeliveryConfig(max_retries=2, retry_jitter="none"),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write"]
    assert not message.acked
    assert not message.nacked
    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_NACKED_TOTAL] == 0


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


@pytest.mark.asyncio
async def test_custody_generation_failure_does_not_ack_or_call_sink() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers["Nats-Sinks-Previous-Custody-Hash"] = "not-a-digest"
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        custody=CustodyConfig(enabled=True, include_previous_hash=True),
    )

    await runner.process_raw_batch([message])

    assert events == []
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_message_normalization_failure_records_specific_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()

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
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGE_NORMALIZATION_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_payload_encryption_failure_does_not_write_or_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        payload_encryptor=FailingPayloadEncryptor(events),  # type: ignore[arg-type]
    )

    await runner.process_raw_batch([message])

    assert events == ["encrypt", "nak"]
    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_payload_encryption_failure_records_specific_metric() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        payload_encryptor=FailingPayloadEncryptor(events),  # type: ignore[arg-type]
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.PAYLOAD_ENCRYPTION_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_pre_sink_policy_rejection_goes_to_dlq_before_ack_without_sink_write() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {}
    metrics = InMemoryMetrics()
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        pre_sink_policy=PreSinkPolicyConfig.model_validate(
            {
                "enabled": True,
                "rules": [{"subject": "orders.*", "require_classification": True}],
            }
        ),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"
    assert metrics.counters[MetricNames.POLICY_MESSAGES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.POLICY_BATCHES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 0


@pytest.mark.asyncio
async def test_pre_sink_policy_mixed_batch_dlqs_rejected_and_writes_accepted() -> None:
    events: list[str] = []
    accepted = FakeMessage(events)
    accepted.headers = {"X-Classification": "NATO RESTRICTED"}
    accepted.metadata.sequence = FakeSequence(stream=1, consumer=1)
    rejected = FakeMessage(events)
    rejected.headers = {}
    rejected.metadata.sequence = FakeSequence(stream=2, consumer=2)
    sink = MetadataRecordingSink(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        message_metadata=MessageMetadataConfig.model_validate(
            {"classification": {"header": "X-Classification"}}
        ),
        pre_sink_policy=PreSinkPolicyConfig.model_validate(
            {
                "enabled": True,
                "rules": [{"subject": "orders.*", "require_classification": True}],
            }
        ),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([accepted, rejected])

    assert events == ["dlq", "ack", "write", "commit", "ack"]
    assert accepted.acked
    assert rejected.acked
    assert [message.stream_sequence for message in sink.messages] == [1]
    assert metrics.counters[MetricNames.POLICY_MESSAGES_PASSED_TOTAL] == 1
    assert metrics.counters[MetricNames.POLICY_MESSAGES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 2


@pytest.mark.asyncio
async def test_pre_sink_policy_dlq_failure_does_not_ack_or_write() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {}
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        pre_sink_policy=PreSinkPolicyConfig.model_validate(
            {
                "enabled": True,
                "rules": [{"subject": "orders.*", "require_classification": True}],
            }
        ),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events, fail_publish=True),
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["dlq"]
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_pre_sink_policy_rejection_without_dlq_leaves_message_unacked() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {}
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        pre_sink_policy=PreSinkPolicyConfig.model_validate(
            {
                "enabled": True,
                "rules": [{"subject": "orders.*", "require_classification": True}],
            }
        ),
        dead_letter=DeadLetterConfig(enabled=False),
    )

    await runner.process_raw_batch([message])

    assert events == []
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_size_policy_rejection_goes_to_dlq_before_ack_without_sink_write() -> None:
    events: list[str] = []
    message = FakeMessage(events, data=b"oversized")
    metrics = InMemoryMetrics()
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        size_policy=SizePolicyConfig(enabled=True, max_payload_bytes=4),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"
    assert metrics.counters[MetricNames.SIZE_POLICY_MESSAGES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.SIZE_POLICY_BATCHES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 0


@pytest.mark.asyncio
async def test_size_policy_mixed_batch_dlqs_rejected_and_writes_accepted() -> None:
    events: list[str] = []
    accepted = FakeMessage(events, data=b"ok")
    accepted.metadata.sequence = FakeSequence(stream=1, consumer=1)
    rejected = FakeMessage(events, data=b"too-large")
    rejected.metadata.sequence = FakeSequence(stream=2, consumer=2)
    sink = MetadataRecordingSink(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        size_policy=SizePolicyConfig(enabled=True, max_payload_bytes=4),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([accepted, rejected])

    assert events == ["dlq", "ack", "write", "commit", "ack"]
    assert accepted.acked
    assert rejected.acked
    assert [message.data for message in sink.messages] == [b"ok"]
    assert metrics.counters[MetricNames.SIZE_POLICY_MESSAGES_PASSED_TOTAL] == 1
    assert metrics.counters[MetricNames.SIZE_POLICY_MESSAGES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 2


@pytest.mark.asyncio
async def test_size_policy_dlq_failure_does_not_ack_or_write() -> None:
    events: list[str] = []
    message = FakeMessage(events, data=b"oversized")
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        size_policy=SizePolicyConfig(enabled=True, max_payload_bytes=4),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events, fail_publish=True),
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["dlq"]
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_size_policy_rejection_without_dlq_leaves_message_unacked() -> None:
    events: list[str] = []
    message = FakeMessage(events, data=b"oversized")
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        size_policy=SizePolicyConfig(enabled=True, max_payload_bytes=4),
        dead_letter=DeadLetterConfig(enabled=False),
    )

    await runner.process_raw_batch([message])

    assert events == []
    assert not message.acked
    assert not message.nacked


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
    assert not message.termed
    assert js.published[0][0] == "orders.dlq"


@pytest.mark.asyncio
async def test_permanent_failure_records_dlq_and_ack_metrics() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "ack"]
    assert metrics.counters[MetricNames.MESSAGES_FAILED_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 0


@pytest.mark.asyncio
async def test_permanent_failure_dlq_uses_confirmed_ack_after_publication() -> None:
    events: list[str] = []
    message = ConfirmedAckMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        delivery=DeliveryConfig(
            ack_confirmation=AckConfirmationConfig(enabled=True, timeout_ms=400)
        ),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "ack_sync:0.400"]
    assert message.acked
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_SUCCESSES_TOTAL] == 1


@pytest.mark.asyncio
async def test_dlq_ack_confirmation_timeout_after_publication_allows_redelivery() -> None:
    events: list[str] = []
    message = ConfirmedAckTimeoutMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        delivery=DeliveryConfig(ack_confirmation=AckConfirmationConfig(enabled=True)),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    with pytest.raises(AckError, match="failed to ACK JetStream message"):
        await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "ack_sync_timeout:1.000"]
    assert not message.acked
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_TIMEOUTS_TOTAL] == 1


@pytest.mark.asyncio
async def test_ackterm_after_dlq_publish_is_explicit_opt_in() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(
            enabled=True,
            subject="orders.dlq",
            ack_term_after_publish=True,
        ),
        jetstream=js,
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "term"]
    assert not message.acked
    assert message.termed
    assert js.published[0][0] == "orders.dlq"
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 1


@pytest.mark.asyncio
async def test_ackterm_after_dlq_publish_records_confirmation_limitation() -> None:
    events: list[str] = []
    message = ConfirmedAckMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(
            enabled=True,
            subject="orders.dlq",
            ack_term_after_publish=True,
        ),
        delivery=DeliveryConfig(ack_confirmation=AckConfirmationConfig(enabled=True)),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "term"]
    assert not message.acked
    assert message.termed
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_ATTEMPTS_TOTAL] == 0
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_UNSUPPORTED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 1


@pytest.mark.asyncio
async def test_successful_sink_write_never_uses_ackterm() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        dead_letter=DeadLetterConfig(
            enabled=True,
            subject="orders.dlq",
            ack_term_after_publish=True,
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert message.acked
    assert not message.termed
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 0


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
async def test_headers_only_dlq_payload_records_omitted_body_without_fake_payload() -> None:
    events: list[str] = []
    message = FakeMessage(events, data=b"")
    message.headers = {"Nats-Msg-Size": "4096"}
    message.metadata.sequence.stream = None  # type: ignore[assignment]
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

    payload = json.loads(js.published[0][1])
    assert events == ["write", "dlq", "ack"]
    assert payload["payload"] == {
        "present": False,
        "omitted": True,
        "omitted_reason": "headers_only",
        "original_size_bytes": 4096,
        "delivered_size_bytes": 0,
        "nats_msg_size_header": "4096",
        "nats_msg_size_header_malformed": False,
    }
    assert "payload_base64" not in payload
    assert payload["payload_unavailable_reason"] == "headers_only"
    assert payload["idempotency_key"] is None
    assert payload["idempotency_key_unavailable_reason"] == "payload_omitted"


@pytest.mark.asyncio
async def test_invalid_mission_metadata_goes_to_dlq_before_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {
        "Nats-Msg-Id": "m-1",
        "Nats-Sinks-Mission-Metadata": "{not-json",
    }
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        mission_metadata=MissionMetadataConfig(enabled=True),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
    )

    await runner.process_raw_batch([message])

    assert events == ["dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"


@pytest.mark.asyncio
async def test_invalid_security_labels_go_to_dlq_before_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    message.headers = {
        "Nats-Msg-Id": "m-1",
        "Nats-Sinks-Security-Labels": "{not-json",
    }
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        security_labels=SecurityLabelProfileConfig(enabled=True),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
    )

    await runner.process_raw_batch([message])

    assert events == ["dlq", "ack"]
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
    assert not message.termed


@pytest.mark.asyncio
async def test_dlq_publish_failure_does_not_ackterm_original() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(
            enabled=True,
            subject="orders.dlq",
            ack_term_after_publish=True,
        ),
        jetstream=FakeJetStream(events, fail_publish=True),
        metrics=metrics,
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["write", "dlq"]
    assert not message.acked
    assert not message.termed
    assert metrics.counters[MetricNames.DLQ_PUBLISH_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 0


@pytest.mark.asyncio
async def test_dlq_publish_failure_records_metric_without_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events, fail_publish=True),
        metrics=metrics,
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert not message.acked
    assert metrics.counters[MetricNames.DLQ_PUBLISH_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_dlq_publish_failure_does_not_attempt_confirmed_ack() -> None:
    events: list[str] = []
    message = ConfirmedAckMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        delivery=DeliveryConfig(ack_confirmation=AckConfirmationConfig(enabled=True)),
        jetstream=FakeJetStream(events, fail_publish=True),
        metrics=metrics,
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["write", "dlq"]
    assert not message.acked
    assert metrics.counters[MetricNames.DLQ_PUBLISH_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_ATTEMPTS_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_ackterm_failure_records_metric_after_dlq_success() -> None:
    events: list[str] = []
    message = TermFailingMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, PermanentSinkError("bad input")),
        dead_letter=DeadLetterConfig(
            enabled=True,
            subject="orders.dlq",
            ack_term_after_publish=True,
        ),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    with pytest.raises(AckError, match="AckTerm"):
        await runner.process_raw_batch([message])

    assert events == ["write", "dlq", "term_failed"]
    assert not message.acked
    assert metrics.counters[MetricNames.MESSAGES_DLQ_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_TERMINATED_TOTAL] == 0
    assert metrics.counters[MetricNames.TERM_ERRORS_TOTAL] == 1


@pytest.mark.asyncio
async def test_ack_failure_records_metric_after_sink_success() -> None:
    events: list[str] = []
    message = AckFailingMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        metrics=metrics,
    )

    with pytest.raises(Exception, match="failed to ACK JetStream message"):
        await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack_failed"]
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_confirmed_ack_after_sink_success_is_optional_and_bounded() -> None:
    events: list[str] = []
    message = ConfirmedAckMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        delivery=DeliveryConfig(
            ack_confirmation=AckConfirmationConfig(enabled=True, timeout_ms=250)
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack_sync:0.250"]
    assert message.acked
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_ATTEMPTS_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_SUCCESSES_TOTAL] == 1
    assert metrics.observations[MetricNames.ACK_CONFIRMATION_SECONDS]


@pytest.mark.asyncio
async def test_confirmed_ack_timeout_after_commit_preserves_redelivery() -> None:
    events: list[str] = []
    message = ConfirmedAckTimeoutMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        delivery=DeliveryConfig(
            ack_confirmation=AckConfirmationConfig(enabled=True, timeout_ms=125)
        ),
        metrics=metrics,
    )

    with pytest.raises(AckError, match="failed to ACK JetStream message"):
        await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack_sync_timeout:0.125"]
    assert not message.acked
    assert metrics.counters[MetricNames.MESSAGES_WRITTEN_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_ATTEMPTS_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_TIMEOUTS_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_ERRORS_TOTAL] == 1


@pytest.mark.asyncio
async def test_confirmed_ack_failure_after_commit_is_counted_separately() -> None:
    events: list[str] = []
    message = ConfirmedAckFailingMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        delivery=DeliveryConfig(
            ack_confirmation=AckConfirmationConfig(enabled=True, timeout_ms=500)
        ),
        metrics=metrics,
    )

    with pytest.raises(AckError, match="failed to ACK JetStream message"):
        await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack_sync_failed:0.500"]
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_FAILURES_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_ERRORS_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


@pytest.mark.asyncio
async def test_confirmed_ack_fails_closed_when_client_lacks_confirmation() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        delivery=DeliveryConfig(ack_confirmation=AckConfirmationConfig(enabled=True)),
        metrics=metrics,
    )

    with pytest.raises(AckError, match="failed to ACK JetStream message"):
        await runner.process_raw_batch([message])

    assert events == ["write", "commit"]
    assert not message.acked
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_UNSUPPORTED_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_ERRORS_TOTAL] == 1


@pytest.mark.asyncio
async def test_confirmed_ack_can_explicitly_fallback_to_standard_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        delivery=DeliveryConfig(
            ack_confirmation=AckConfirmationConfig(
                enabled=True,
                unsupported_action="standard_ack",
            )
        ),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert message.acked
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_UNSUPPORTED_TOTAL] == 1
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_SUCCESSES_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 1


@pytest.mark.asyncio
async def test_confirmed_ack_is_not_attempted_before_sink_success() -> None:
    events: list[str] = []
    message = ConfirmedAckMessage(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events, TemporarySinkError("database unavailable")),
        delivery=DeliveryConfig(ack_confirmation=AckConfirmationConfig(enabled=True)),
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "nak"]
    assert not message.acked
    assert metrics.counters[MetricNames.ACK_CONFIRMATION_ATTEMPTS_TOTAL] == 0
    assert metrics.counters[MetricNames.MESSAGES_ACKED_TOTAL] == 0


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
