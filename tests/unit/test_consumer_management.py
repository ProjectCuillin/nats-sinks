# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Consumer-management tests for delivery-sensitive JetStream startup logic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nats_sinks.core.config import ConsumerManagementConfig, DeliveryConfig
from nats_sinks.core.consumer_management import (
    build_consumer_config,
    detect_consumer_drift,
    ensure_jetstream_consumer,
)
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.sinks.base import Sink


class FakeMessage:
    """Small raw message double for runner startup contract tests."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.subject = "orders.created"
        self.data = b"{}"
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

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True


class RecordingSink(Sink):
    """Sink double that records durable success before ACK is expected."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: object) -> None:
        del messages
        self.events.append("write")
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class NotFoundError(Exception):
    """Test double whose class name matches nats-py missing-consumer errors."""


def _consumer_info(**overrides: object) -> SimpleNamespace:
    config = {
        "name": "orders-sink",
        "durable_name": "orders-sink",
        "filter_subject": "orders.*",
        "filter_subjects": None,
        "ack_policy": "explicit",
        "deliver_policy": "all",
        "replay_policy": "instant",
        "deliver_subject": None,
        "headers_only": None,
        "ack_wait": None,
        "max_deliver": None,
        "backoff": None,
        "max_ack_pending": None,
        "max_waiting": None,
        "num_replicas": None,
        "mem_storage": None,
        "metadata": None,
    }
    config.update(overrides)
    return SimpleNamespace(config=SimpleNamespace(**config))


class FakeManagedJetStream:
    def __init__(self, *, existing: object | None) -> None:
        self.existing = existing
        self.events: list[str] = []
        self.added_configs: list[object] = []

    async def consumer_info(self, stream: str, consumer: str) -> object:
        self.events.append(f"consumer_info:{stream}:{consumer}")
        if self.existing is None:
            raise NotFoundError("missing")
        return self.existing

    async def add_consumer(self, stream: str, *, config: object) -> object:
        self.events.append(f"add_consumer:{stream}")
        self.added_configs.append(config)
        return SimpleNamespace(config=config)


class FakeManagedSubscription:
    def __init__(self, messages: list[FakeMessage], runner: JetStreamSinkRunner) -> None:
        self.messages = messages
        self.runner = runner

    async def fetch(self, batch: int, **options: object) -> list[FakeMessage]:
        del batch, options
        self.runner.request_stop()
        return self.messages


class FakeRunnerJetStream(FakeManagedJetStream):
    def __init__(self, *, existing: object | None, events: list[str]) -> None:
        super().__init__(existing=existing)
        self.events = events
        self.subscription: FakeManagedSubscription | None = None

    async def pull_subscribe(
        self,
        subject: str,
        *,
        durable: str | None,
        stream: str,
    ) -> FakeManagedSubscription:
        self.events.append(f"pull_subscribe:{stream}:{durable}:{subject}")
        assert self.subscription is not None
        return self.subscription


@pytest.mark.asyncio
async def test_bind_only_fails_when_consumer_is_missing() -> None:
    js = FakeManagedJetStream(existing=None)

    with pytest.raises(ConfigurationError, match="does not exist"):
        await ensure_jetstream_consumer(
            js,
            stream="ORDERS",
            durable_name="orders-sink",
            subject="orders.*",
            durable=True,
            config=ConsumerManagementConfig(mode="bind_only"),
        )

    assert js.events == ["consumer_info:ORDERS:orders-sink"]
    assert js.added_configs == []


@pytest.mark.asyncio
async def test_create_if_missing_creates_durable_pull_consumer() -> None:
    js = FakeManagedJetStream(existing=None)

    result = await ensure_jetstream_consumer(
        js,
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        durable=True,
        config=ConsumerManagementConfig(
            mode="create_if_missing",
            ack_wait_seconds=45,
            max_deliver=7,
            max_ack_pending=500,
            headers_only=False,
        ),
    )

    created = js.added_configs[0]
    assert result.action == "created"
    assert js.events == ["consumer_info:ORDERS:orders-sink", "add_consumer:ORDERS"]
    assert created.durable_name == "orders-sink"
    assert created.filter_subject == "orders.*"
    assert created.ack_policy.value == "explicit"
    assert created.ack_wait == 45.0
    assert created.max_deliver == 7
    assert created.max_ack_pending == 500
    assert created.headers_only is False


@pytest.mark.asyncio
async def test_create_if_missing_binds_compatible_existing_consumer() -> None:
    js = FakeManagedJetStream(existing=_consumer_info())

    result = await ensure_jetstream_consumer(
        js,
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        durable=True,
        config=ConsumerManagementConfig(mode="create_if_missing"),
    )

    assert result.action == "bound"
    assert js.events == ["consumer_info:ORDERS:orders-sink"]
    assert js.added_configs == []


@pytest.mark.asyncio
async def test_incompatible_filter_subject_fails_closed() -> None:
    js = FakeManagedJetStream(existing=_consumer_info(filter_subject="payments.*"))

    with pytest.raises(ConfigurationError, match="filter_subject"):
        await ensure_jetstream_consumer(
            js,
            stream="ORDERS",
            durable_name="orders-sink",
            subject="orders.*",
            durable=True,
            config=ConsumerManagementConfig(mode="create_if_missing"),
        )

    assert js.added_configs == []


@pytest.mark.asyncio
async def test_push_consumer_drift_fails_closed() -> None:
    js = FakeManagedJetStream(existing=_consumer_info(deliver_subject="deliver.orders"))

    with pytest.raises(ConfigurationError, match="deliver_subject"):
        await ensure_jetstream_consumer(
            js,
            stream="ORDERS",
            durable_name="orders-sink",
            subject="orders.*",
            durable=True,
            config=ConsumerManagementConfig(mode="bind_only"),
        )


@pytest.mark.asyncio
async def test_reconcile_updates_compatible_existing_consumer() -> None:
    js = FakeManagedJetStream(
        existing=_consumer_info(
            ack_wait=30.0,
            max_deliver=5,
            max_ack_pending=100,
            headers_only=False,
        )
    )

    result = await ensure_jetstream_consumer(
        js,
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        durable=True,
        config=ConsumerManagementConfig(
            mode="reconcile",
            ack_wait_seconds=30,
            max_deliver=5,
            max_ack_pending=100,
            headers_only=False,
        ),
    )

    assert result.action == "reconciled"
    assert js.events == ["consumer_info:ORDERS:orders-sink", "add_consumer:ORDERS"]
    assert js.added_configs[0].filter_subject == "orders.*"


def test_detect_consumer_drift_reports_delivery_sensitive_fields() -> None:
    drift = detect_consumer_drift(
        _consumer_info(
            ack_policy="none",
            deliver_policy="new",
            replay_policy="original",
            headers_only=True,
            backoff=[1.0, 2.0],
            max_ack_pending=10,
            num_replicas=1,
            mem_storage=False,
            metadata={"component": "old"},
        ),
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        config=ConsumerManagementConfig(
            deliver_policy="all",
            replay_policy="instant",
            headers_only=False,
            max_ack_pending=100,
            max_deliver=3,
            backoff_seconds=[1, 5],
            num_replicas=3,
            memory_storage=True,
            metadata={"component": "nats-sinks"},
        ),
    )

    assert {item.field for item in drift} == {
        "ack_policy",
        "deliver_policy",
        "replay_policy",
        "headers_only",
        "backoff",
        "max_deliver",
        "max_ack_pending",
        "num_replicas",
        "mem_storage",
        "metadata",
    }


def test_build_consumer_config_uses_explicit_ack_policy() -> None:
    config = build_consumer_config(
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.*",
        config=ConsumerManagementConfig(max_waiting=64),
    )

    assert config.name == "orders-sink"
    assert config.durable_name == "orders-sink"
    assert config.filter_subject == "orders.*"
    assert config.ack_policy.value == "explicit"
    assert config.deliver_policy.value == "all"
    assert config.replay_policy.value == "instant"
    assert config.max_waiting == 64


def test_build_consumer_config_supports_richer_policy_fields() -> None:
    config = build_consumer_config(
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.>",
        config=ConsumerManagementConfig(
            filter_subjects=["orders.created", "orders.updated"],
            max_deliver=5,
            backoff_seconds=[1, 5, 30],
            headers_only=True,
            num_replicas=3,
            memory_storage=True,
            metadata={"component": "nats-sinks", "purpose": "sink-worker"},
        ),
    )

    assert config.filter_subject is None
    assert config.filter_subjects == ["orders.created", "orders.updated"]
    assert config.backoff == [1.0, 5.0, 30.0]
    assert config.max_deliver == 5
    assert config.headers_only is True
    assert config.num_replicas == 3
    assert config.mem_storage is True
    assert config.metadata == {"component": "nats-sinks", "purpose": "sink-worker"}


def test_filter_subjects_must_not_exceed_primary_subject_scope() -> None:
    with pytest.raises(ConfigurationError, match="not contained"):
        build_consumer_config(
            stream="ORDERS",
            durable_name="orders-sink",
            subject="orders.created",
            config=ConsumerManagementConfig(filter_subjects=["orders.>"]),
        )


def test_detect_consumer_drift_accepts_filter_subject_order_differences() -> None:
    drift = detect_consumer_drift(
        _consumer_info(
            filter_subject=None,
            filter_subjects=["orders.updated", "orders.created"],
        ),
        stream="ORDERS",
        durable_name="orders-sink",
        subject="orders.>",
        config=ConsumerManagementConfig(filter_subjects=["orders.created", "orders.updated"]),
    )

    assert drift == ()


@pytest.mark.asyncio
async def test_runner_reconciles_consumer_before_pull_subscribe_and_ack() -> None:
    events: list[str] = []
    message = FakeMessage(events)
    sink = RecordingSink(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=sink,
        delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
        consumer_management=ConsumerManagementConfig(mode="bind_only"),
    )
    js = FakeRunnerJetStream(existing=_consumer_info(), events=events)
    subscription = FakeManagedSubscription([message], runner)
    js.subscription = subscription
    runner._js = js

    await runner.run()

    assert events == [
        "consumer_info:ORDERS:orders-sink",
        "pull_subscribe:ORDERS:orders-sink:orders.*",
        "write",
        "commit",
        "ack",
    ]
    assert message.acked
