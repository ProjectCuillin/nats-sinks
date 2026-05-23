# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Event freshness metrics tests.

Freshness metrics sit in the core runtime because every sink benefits from the
same timing evidence.  These tests keep that evidence aggregate-only and prove
that missing, malformed, stale, and future-dated timestamps remain safe
observations rather than delivery failures.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nats_sinks import NatsEnvelope
from nats_sinks.core.freshness import record_event_freshness_metrics
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames


def _envelope(
    *,
    headers: dict[str, str] | None = None,
    timestamp: datetime | None = None,
    received_at: datetime,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject="orders.created",
        data=b"{}",
        headers=headers or {},
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=timestamp,
        message_id=None,
        redelivered=False,
        pending=0,
        received_at=received_at,
    )


def test_records_event_age_at_receive_and_store_from_nats_time_stamp() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(
        headers={"Nats-Time-Stamp": "2026-05-23T10:00:00Z"},
        received_at=datetime(2026, 5, 23, 10, 1, tzinfo=UTC),
    )

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 2, tzinfo=UTC),
        enabled=True,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.observations[MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS] == [60.0]
    assert metrics.observations[MetricNames.EVENT_AGE_AT_STORE_SECONDS] == [120.0]


def test_missing_creation_timestamp_is_counted_without_observing_age() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(received_at=datetime(2026, 5, 23, 10, 1, tzinfo=UTC))

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 2, tzinfo=UTC),
        enabled=True,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.counters[MetricNames.EVENT_CREATION_TIMESTAMP_MISSING_TOTAL] == 1
    assert MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS not in metrics.observations


def test_malformed_header_is_counted_and_falls_back_to_jetstream_timestamp() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(
        headers={"Nats-Time-Stamp": "not-a-timestamp"},
        timestamp=datetime(2026, 5, 23, 10, 0, tzinfo=UTC),
        received_at=datetime(2026, 5, 23, 10, 1, tzinfo=UTC),
    )

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 2, tzinfo=UTC),
        enabled=True,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.counters[MetricNames.EVENT_CREATION_TIMESTAMP_MALFORMED_TOTAL] == 1
    assert metrics.observations[MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS] == [60.0]


def test_future_timestamp_records_skew_and_clamps_age_to_zero() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(
        headers={"Nats-Time-Stamp": "2026-05-23T10:01:00Z"},
        received_at=datetime(2026, 5, 23, 10, 0, tzinfo=UTC),
    )

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 0, 30, tzinfo=UTC),
        enabled=True,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.counters[MetricNames.EVENT_CREATION_TIMESTAMP_FUTURE_TOTAL] == 1
    assert metrics.observations[MetricNames.EVENT_SOURCE_CLOCK_SKEW_SECONDS] == [60.0]
    assert metrics.observations[MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS] == [0.0]
    assert metrics.observations[MetricNames.EVENT_AGE_AT_STORE_SECONDS] == [0.0]


def test_stale_threshold_counts_old_events_at_receive_and_store() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(
        headers={"Nats-Time-Stamp": "2026-05-23T10:00:00Z"},
        received_at=datetime(2026, 5, 23, 10, 10, tzinfo=UTC),
    )

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 11, tzinfo=UTC),
        enabled=True,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.counters[MetricNames.EVENTS_STALE_AT_RECEIVE_TOTAL] == 1
    assert metrics.counters[MetricNames.EVENTS_STALE_AT_STORE_TOTAL] == 1


def test_disabled_freshness_metrics_do_not_emit_values() -> None:
    metrics = InMemoryMetrics()
    envelope = _envelope(
        headers={"Nats-Time-Stamp": "2026-05-23T10:00:00Z"},
        received_at=datetime(2026, 5, 23, 10, 1, tzinfo=UTC),
    )

    record_event_freshness_metrics(
        metrics,
        [envelope],
        stored_at=datetime(2026, 5, 23, 10, 2, tzinfo=UTC),
        enabled=False,
        stale_after_seconds=300.0,
        future_skew_tolerance_seconds=5.0,
    )

    assert metrics.counters == {}
    assert metrics.observations == {}
