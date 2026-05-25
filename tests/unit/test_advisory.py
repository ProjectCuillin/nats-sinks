# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

import pytest

from nats_sinks.core.advisory import (
    JetStreamAdvisoryMonitor,
    advisory_kind_from_subject,
    observe_jetstream_advisory_message,
    parse_jetstream_advisory,
    validate_advisory_subject,
)
from nats_sinks.core.config import JetStreamAdvisoryConfig
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames


@dataclass
class FakeAdvisoryMessage:
    subject: str
    data: bytes
    acked: bool = False

    async def ack(self) -> None:
        self.acked = True


class FakeSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeNatsConnection:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []
        self.subscription = FakeSubscription()

    async def subscribe(self, subject: str, *, cb: object) -> FakeSubscription:
        self.subscriptions.append((subject, cb))
        return self.subscription


def test_advisory_subject_validation_accepts_known_patterns() -> None:
    assert (
        validate_advisory_subject("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*")
        == "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*"
    )


@pytest.mark.parametrize(
    "subject",
    [
        "orders.created",
        "$JS.EVENT.METRIC.CONSUMER.ACK.*.*",
        "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.>.EXTRA",
        "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.\n",
    ],
)
def test_advisory_subject_validation_rejects_unsafe_patterns(subject: str) -> None:
    with pytest.raises(ValidationError):
        validate_advisory_subject(subject)


def test_advisory_kind_from_subject_uses_low_cardinality_categories() -> None:
    assert (
        advisory_kind_from_subject("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C")
        == "max_deliver"
    )
    assert advisory_kind_from_subject("$JS.EVENT.ADVISORY.STREAM.QUORUM_LOST.ORDERS") == (
        "stream_quorum_lost"
    )
    assert advisory_kind_from_subject("$JS.EVENT.ADVISORY.UNKNOWN.ORDERS") == "unsupported"


def test_parse_advisory_prefers_documented_type_over_subject() -> None:
    advisory = parse_jetstream_advisory(
        subject="$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C",
        data=b'{"type":"io.nats.jetstream.advisory.v1.nak","stream":"ORDERS"}',
    )

    assert advisory.kind == "nak"
    assert advisory.advisory_type == "io.nats.jetstream.advisory.v1.nak"


def test_parse_advisory_rejects_malformed_and_oversized_payloads() -> None:
    with pytest.raises(ValidationError, match="valid JSON"):
        parse_jetstream_advisory(
            subject="$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C",
            data=b"{",
        )

    with pytest.raises(ValidationError, match="byte limit"):
        parse_jetstream_advisory(
            subject="$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C",
            data=b'{"type":"io.nats.jetstream.advisory.v1.max_deliver"}',
            max_payload_bytes=8,
        )


def test_observe_advisory_records_sanitized_metric_without_ack() -> None:
    config = JetStreamAdvisoryConfig(
        enabled=True,
        subjects=("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",),
    )
    metrics = InMemoryMetrics()
    message = FakeAdvisoryMessage(
        subject="$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C",
        data=b'{"type":"io.nats.jetstream.advisory.v1.max_deliver","stream":"ORDERS"}',
    )

    advisory = observe_jetstream_advisory_message(message, config=config, metrics=metrics)

    assert advisory is not None
    assert advisory.kind == "max_deliver"
    assert metrics.counters[MetricNames.JETSTREAM_ADVISORIES_RECEIVED_TOTAL] == 1
    assert metrics.counters[MetricNames.JETSTREAM_ADVISORY_MAX_DELIVER_TOTAL] == 1
    assert not message.acked


def test_observe_advisory_filters_unapproved_subjects() -> None:
    config = JetStreamAdvisoryConfig(
        enabled=True,
        subjects=("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",),
    )
    metrics = InMemoryMetrics()

    advisory = observe_jetstream_advisory_message(
        FakeAdvisoryMessage(
            subject="$JS.EVENT.ADVISORY.CONSUMER.MSG_NAKED.ORDERS.C",
            data=b'{"type":"io.nats.jetstream.advisory.v1.nak"}',
        ),
        config=config,
        metrics=metrics,
    )

    assert advisory is None
    assert metrics.counters[MetricNames.JETSTREAM_ADVISORIES_FILTERED_TOTAL] == 1
    assert MetricNames.JETSTREAM_ADVISORY_NAK_TOTAL not in metrics.counters


def test_observe_advisory_parse_failure_is_metric_only() -> None:
    config = JetStreamAdvisoryConfig(
        enabled=True,
        subjects=("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",),
    )
    metrics = InMemoryMetrics()

    advisory = observe_jetstream_advisory_message(
        FakeAdvisoryMessage(
            subject="$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.ORDERS.C",
            data=b"{",
        ),
        config=config,
        metrics=metrics,
    )

    assert advisory is None
    assert metrics.counters[MetricNames.JETSTREAM_ADVISORY_PARSE_ERRORS_TOTAL] == 1


@pytest.mark.asyncio
async def test_advisory_monitor_subscribes_and_unsubscribes_when_enabled() -> None:
    connection = FakeNatsConnection()
    metrics = InMemoryMetrics()
    config = JetStreamAdvisoryConfig(
        enabled=True,
        subjects=("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",),
    )
    monitor = JetStreamAdvisoryMonitor(connection, config=config, metrics=metrics)

    await monitor.start()
    await monitor.stop()

    assert len(connection.subscriptions) == 1
    assert connection.subscriptions[0][0] == "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*"
    assert connection.subscription.unsubscribed
