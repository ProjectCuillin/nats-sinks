# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Push-consumer guardrail and runner tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.config import (
    ConsumerManagementConfig,
    DeadLetterConfig,
    DeliveryConfig,
    PushConsumerConfig,
)
from nats_sinks.core.consumer_management import (
    build_push_consumer_config,
    detect_push_consumer_capabilities,
    ensure_jetstream_push_consumer,
)
from nats_sinks.core.errors import ConfigurationError, PermanentSinkError, TemporarySinkError
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.sinks.base import Sink


class NotFoundError(Exception):
    """Test double whose class name matches nats-py missing-consumer errors."""


class FakePushMessage:
    """Small raw NATS message double used by push-runner tests."""

    def __init__(
        self,
        events: list[str],
        *,
        on_ack: object | None = None,
        on_nak: object | None = None,
    ) -> None:
        self.events = events
        self.on_ack = on_ack
        self.on_nak = on_nak
        self.subject = "orders.created"
        self.data = b'{"id": "push-1"}'
        self.headers = {}
        self.metadata = SimpleNamespace(
            stream="ORDERS",
            consumer="orders-sink",
            sequence=SimpleNamespace(stream=1, consumer=1),
            num_delivered=1,
            num_pending=0,
            timestamp=None,
        )
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True
        if callable(self.on_ack):
            self.on_ack()

    async def nak(self, *, delay: float | None = None) -> None:
        del delay
        self.events.append("nak")
        self.nacked = True
        if callable(self.on_nak):
            self.on_nak()


class RecordingSink(Sink):
    """Sink double that proves durable write happens before ACK."""

    def __init__(self, events: list[str], message: FakePushMessage | None = None) -> None:
        self.events = events
        self.message = message

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: object) -> None:
        del messages
        if self.message is not None:
            assert not self.message.acked
        self.events.append("write")
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class FailingSink(Sink):
    """Sink double that fails after recording a write attempt."""

    def __init__(self, events: list[str], error: Exception) -> None:
        self.events = events
        self.error = error

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: object) -> None:
        del messages
        self.events.append("write")
        raise self.error

    async def stop(self) -> None:
        return None


def _push_consumer_info(**overrides: object) -> SimpleNamespace:
    config = {
        "name": "orders-sink",
        "durable_name": "orders-sink",
        "filter_subject": "orders.*",
        "filter_subjects": None,
        "ack_policy": "explicit",
        "deliver_policy": "all",
        "replay_policy": "instant",
        "deliver_subject": "_INBOX.nats_sinks.orders",
        "deliver_group": None,
        "headers_only": None,
        "ack_wait": None,
        "max_deliver": None,
        "backoff": None,
        "max_ack_pending": 2,
        "max_waiting": None,
        "flow_control": False,
        "idle_heartbeat": None,
        "num_replicas": None,
        "mem_storage": None,
        "metadata": None,
    }
    config.update(overrides)
    return SimpleNamespace(config=SimpleNamespace(**config))


class FakePushJetStream:
    """JetStream double with the nats-py push-subscribe capability surface."""

    def __init__(
        self,
        *,
        existing: object | None = None,
        messages: list[FakePushMessage] | None = None,
    ) -> None:
        self.existing = existing
        self.messages = messages or []
        self.added_configs: list[object] = []
        self.published: list[tuple[str, bytes, dict[str, str]]] = []
        self.subscribe_options: dict[str, object] = {}

    async def consumer_info(self, stream: str, consumer: str) -> object:
        del stream, consumer
        if self.existing is None:
            raise NotFoundError("missing")
        return self.existing

    async def add_consumer(self, stream: str, *, config: object) -> object:
        del stream
        self.added_configs.append(config)
        self.existing = SimpleNamespace(config=config)
        return SimpleNamespace(config=config)

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Any | None = None,
        durable: str | None = None,
        stream: str | None = None,
        config: object | None = None,
        manual_ack: bool = False,
        flow_control: bool = False,
        idle_heartbeat: float | None = None,
        pending_msgs_limit: int = 0,
        pending_bytes_limit: int = 0,
    ) -> object:
        self.subscribe_options = {
            "subject": subject,
            "queue": queue,
            "durable": durable,
            "stream": stream,
            "config": config,
            "manual_ack": manual_ack,
            "flow_control": flow_control,
            "idle_heartbeat": idle_heartbeat,
            "pending_msgs_limit": pending_msgs_limit,
            "pending_bytes_limit": pending_bytes_limit,
        }
        assert cb is not None
        for message in self.messages:
            await cb(message)
        return SimpleNamespace()

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        self.published.append((subject, payload, headers))
        for message in self.messages:
            message.events.append("dlq")


class PartialPushJetStream:
    """JetStream double missing required push-subscribe keyword arguments."""

    async def subscribe(self, subject: str, *, cb: object) -> object:
        del subject, cb
        return SimpleNamespace()


def test_push_consumer_defaults_to_disabled() -> None:
    config = PushConsumerConfig()

    assert not config.enabled
    assert config.manual_ack
    assert config.deliver_subject is None


def test_push_consumer_enabled_requires_manual_ack_and_deliver_subject() -> None:
    with pytest.raises(PydanticValidationError, match="manual_ack"):
        PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            manual_ack=False,
        )

    with pytest.raises(PydanticValidationError, match="deliver_subject"):
        PushConsumerConfig(enabled=True)


def test_push_consumer_rejects_wildcard_deliver_subject_and_bad_group() -> None:
    with pytest.raises(PydanticValidationError, match="wildcards"):
        PushConsumerConfig(enabled=True, deliver_subject="_INBOX.orders.*")

    with pytest.raises(PydanticValidationError, match="deliver_group"):
        PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            deliver_group=" bad group ",
        )


def test_push_consumer_capability_detection_fails_closed_for_partial_api() -> None:
    result = detect_push_consumer_capabilities(PartialPushJetStream())

    assert not result.supported
    assert set(result.missing) == {
        "config",
        "manual_ack",
        "pending_bytes_limit",
        "pending_msgs_limit",
    }


def test_build_push_consumer_config_sets_delivery_sensitive_fields() -> None:
    config = build_push_consumer_config(
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        consumer_management=ConsumerManagementConfig(max_deliver=5),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            deliver_group="orders-workers",
            pending_msgs_limit=256,
            flow_control=True,
            idle_heartbeat_seconds=2.5,
        ),
    )

    assert config.name == "orders-sink"
    assert config.durable_name == "orders-sink"
    assert config.filter_subject == "orders.*"
    assert config.ack_policy.value == "explicit"
    assert config.deliver_subject == "_INBOX.nats_sinks.orders"
    assert config.deliver_group == "orders-workers"
    assert config.max_ack_pending == 256
    assert config.max_deliver == 5
    assert config.flow_control is True
    assert config.idle_heartbeat == 2.5


@pytest.mark.asyncio
async def test_ensure_push_consumer_creates_bounded_manual_ack_consumer() -> None:
    js = FakePushJetStream()

    result = await ensure_jetstream_push_consumer(
        js,
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        durable=True,
        consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=2,
        ),
    )

    assert result.action == "created"
    assert js.added_configs[0].deliver_subject == "_INBOX.nats_sinks.orders"
    assert js.added_configs[0].max_ack_pending == 2


@pytest.mark.asyncio
async def test_push_consumer_rejects_unbounded_server_ack_pending() -> None:
    js = FakePushJetStream()

    with pytest.raises(ConfigurationError, match="max_ack_pending"):
        await ensure_jetstream_push_consumer(
            js,
            stream="ORDERS",
            durable_name="orders-sink",
            subject="orders.*",
            durable=True,
            consumer_management=ConsumerManagementConfig(max_ack_pending=3),
            push_consumer=PushConsumerConfig(
                enabled=True,
                deliver_subject="_INBOX.nats_sinks.orders",
                pending_msgs_limit=2,
            ),
        )


@pytest.mark.asyncio
async def test_push_callback_uses_bounded_queue_without_ack() -> None:
    message = FakePushMessage([])
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=RecordingSink([]),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=1,
        ),
    )
    runner._push_queue = asyncio.Queue(maxsize=1)
    runner._push_accepting = True

    await runner._handle_push_message(message)

    assert runner._push_queue.qsize() == 1
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_push_subscription_callback_contains_handler_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    message = FakePushMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=RecordingSink([]),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
        ),
    )

    async def _raise_callback_error(raw_message: object) -> None:
        del raw_message
        raise RuntimeError("callback scheduling failed")

    monkeypatch.setattr(runner, "_handle_push_message", _raise_callback_error)

    await runner._push_subscription_callback(message)

    assert events == []
    assert not message.acked
    assert not message.nacked


@pytest.mark.asyncio
async def test_push_queue_overflow_naks_without_ack() -> None:
    first = FakePushMessage([])
    second_events: list[str] = []
    second = FakePushMessage(second_events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=RecordingSink([]),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=1,
        ),
    )
    runner._push_queue = asyncio.Queue(maxsize=1)
    runner._push_accepting = True
    await runner._push_queue.put(first)

    await runner._handle_push_message(second)

    assert not second.acked
    assert second.nacked
    assert second_events == ["nak"]


@pytest.mark.asyncio
async def test_push_run_acks_only_after_sink_commit() -> None:
    events: list[str] = []
    runner: JetStreamSinkRunner | None = None

    def _stop_runner() -> None:
        assert runner is not None
        runner.request_stop()

    message = FakePushMessage(events, on_ack=_stop_runner)
    sink = RecordingSink(events, message)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=sink,
        delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
        consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=2,
            pending_bytes_limit=2048,
        ),
    )
    js = FakePushJetStream(messages=[message])
    runner._js = js

    await runner.run()

    assert events == ["write", "commit", "ack"]
    assert message.acked
    assert js.subscribe_options["manual_ack"] is True
    assert js.subscribe_options["pending_msgs_limit"] == 2
    assert js.subscribe_options["pending_bytes_limit"] == 2048


@pytest.mark.asyncio
async def test_push_run_does_not_ack_temporary_sink_failure() -> None:
    events: list[str] = []
    runner: JetStreamSinkRunner | None = None

    def _stop_runner() -> None:
        assert runner is not None
        runner.request_stop()

    message = FakePushMessage(events, on_nak=_stop_runner)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=FailingSink(events, TemporarySinkError("temporary outage")),
        delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
        consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=2,
        ),
    )
    runner._js = FakePushJetStream(messages=[message])

    await runner.run()

    assert events == ["write", "nak"]
    assert not message.acked
    assert message.nacked


@pytest.mark.asyncio
async def test_push_run_publishes_dlq_before_ack_on_permanent_failure() -> None:
    events: list[str] = []
    runner: JetStreamSinkRunner | None = None

    def _stop_runner() -> None:
        assert runner is not None
        runner.request_stop()

    message = FakePushMessage(events, on_ack=_stop_runner)
    js = FakePushJetStream(messages=[message])
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=FailingSink(events, PermanentSinkError("invalid payload")),
        delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
        consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=2,
        ),
    )
    runner._js = js

    await runner.run()

    assert events == ["write", "dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"


@pytest.mark.asyncio
async def test_push_run_passes_flow_control_heartbeat_and_pending_limits() -> None:
    events: list[str] = []
    runner: JetStreamSinkRunner | None = None

    def _stop_runner() -> None:
        assert runner is not None
        runner.request_stop()

    message = FakePushMessage(events, on_ack=_stop_runner)
    js = FakePushJetStream(messages=[message])
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=RecordingSink(events, message),
        delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
        consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            deliver_group="orders-workers",
            pending_msgs_limit=3,
            pending_bytes_limit=4096,
            flow_control=True,
            idle_heartbeat_seconds=2.5,
        ),
    )
    runner._js = js

    await runner.run()

    assert events == ["write", "commit", "ack"]
    assert js.subscribe_options["queue"] == "orders-workers"
    assert js.subscribe_options["flow_control"] is True
    assert js.subscribe_options["idle_heartbeat"] == 2.5
    assert js.subscribe_options["pending_msgs_limit"] == 3
    assert js.subscribe_options["pending_bytes_limit"] == 4096


@pytest.mark.asyncio
async def test_push_run_requires_durable_consumer() -> None:
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        durable=False,
        sink=RecordingSink([]),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
        ),
    )
    runner._js = FakePushJetStream()

    with pytest.raises(ConfigurationError, match="durable=true"):
        await runner.run()


@pytest.mark.asyncio
async def test_push_shutdown_stops_new_intake_before_drain() -> None:
    events: list[str] = []
    message = FakePushMessage(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=RecordingSink([]),
        push_consumer=PushConsumerConfig(
            enabled=True,
            deliver_subject="_INBOX.nats_sinks.orders",
            pending_msgs_limit=1,
        ),
    )
    runner._push_queue = asyncio.Queue(maxsize=1)
    runner.request_stop()

    await runner._handle_push_message(message)

    assert events == ["nak"]
    assert message.nacked
