# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Priority-lane scheduler tests.

Priority lanes change only the order of an already-fetched batch.  These tests
therefore focus on deterministic ordering, fail-closed validation, and safe
aggregate metrics rather than NATS server-side delivery behavior.
"""

from __future__ import annotations

import pytest

from nats_sinks import NatsEnvelope, ValidationError
from nats_sinks.core.config import PriorityLaneConfig, PriorityLanesConfig
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
from nats_sinks.core.priority import assign_priority_lane, order_by_priority_lanes


def envelope(sequence: int, *, priority: str | None) -> NatsEnvelope:
    """Build a compact immutable envelope for scheduler tests."""

    return NatsEnvelope(
        subject="orders.created",
        data=f'{{"sequence":{sequence}}}'.encode(),
        headers={},
        stream="ORDERS",
        consumer="priority-test",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=f"m-{sequence}",
        redelivered=False,
        pending=0,
        priority=priority,
    )


def priority_policy() -> PriorityLanesConfig:
    """Return a small weighted policy used by multiple tests."""

    return PriorityLanesConfig(
        enabled=True,
        default_lane="routine",
        lanes=[
            PriorityLaneConfig(name="urgent", priorities=("urgent", "immediate"), weight=2),
            PriorityLaneConfig(name="routine", priorities=("normal", "routine"), weight=1),
        ],
    )


def test_disabled_priority_lanes_preserve_batch_order() -> None:
    messages = [
        envelope(1, priority="routine"),
        envelope(2, priority="urgent"),
        envelope(3, priority=None),
    ]

    ordered = order_by_priority_lanes(messages, PriorityLanesConfig(enabled=False))

    assert [message.stream_sequence for message in ordered] == [1, 2, 3]


def test_weighted_round_robin_prevents_low_lane_starvation_inside_batch() -> None:
    messages = [
        envelope(1, priority="urgent"),
        envelope(2, priority="urgent"),
        envelope(3, priority="urgent"),
        envelope(4, priority="routine"),
        envelope(5, priority="routine"),
    ]

    ordered = order_by_priority_lanes(messages, priority_policy())

    assert [message.stream_sequence for message in ordered] == [1, 2, 4, 3, 5]


def test_missing_priority_uses_default_lane_and_records_aggregate_metric() -> None:
    metrics = InMemoryMetrics()
    policy = priority_policy()
    message = envelope(1, priority=None)

    assignment = assign_priority_lane(message, policy, metrics=metrics)

    assert assignment.lane_name == "routine"
    assert assignment.defaulted is True
    assert metrics.counters[MetricNames.PRIORITY_LANE_DEFAULTED_TOTAL] == 1


def test_unknown_priority_defaults_unless_policy_rejects_it() -> None:
    metrics = InMemoryMetrics()
    policy = priority_policy()
    message = envelope(1, priority="operator-invented")

    ordered = order_by_priority_lanes([message], policy, metrics=metrics)

    assert ordered == [message]
    assert metrics.counters[MetricNames.PRIORITY_LANE_DEFAULTED_TOTAL] == 1
    assert metrics.counters[MetricNames.PRIORITY_LANE_MESSAGES_TOTAL] == 1


def test_unknown_priority_can_fail_closed_when_configured() -> None:
    metrics = InMemoryMetrics()
    policy = PriorityLanesConfig(
        enabled=True,
        default_lane="routine",
        unknown_priority_action="reject",
        lanes=[
            PriorityLaneConfig(name="urgent", priorities=("urgent",), weight=2),
            PriorityLaneConfig(name="routine", priorities=("routine",), weight=1),
        ],
    )

    with pytest.raises(ValidationError, match="not allowed"):
        order_by_priority_lanes(
            [envelope(1, priority="spoofed-admin-priority")],
            policy,
            metrics=metrics,
        )

    assert metrics.counters[MetricNames.PRIORITY_LANE_REJECTED_TOTAL] == 1


def test_malformed_priority_is_rejected_before_sink_delivery() -> None:
    policy = priority_policy()

    with pytest.raises(ValidationError, match="control characters"):
        order_by_priority_lanes([envelope(1, priority="urgent\nspoof")], policy)


def test_oversized_priority_is_rejected_before_sink_delivery() -> None:
    policy = PriorityLanesConfig(
        enabled=True,
        default_lane="routine",
        max_priority_value_length=8,
        lanes=[
            PriorityLaneConfig(name="urgent", priorities=("urgent",), weight=2),
            PriorityLaneConfig(name="routine", priorities=("routine",), weight=1),
        ],
    )

    with pytest.raises(ValidationError, match="length limit"):
        order_by_priority_lanes([envelope(1, priority="very-long-priority")], policy)


def test_priority_lane_metrics_do_not_expose_subjects_or_priority_values() -> None:
    metrics = InMemoryMetrics()

    order_by_priority_lanes(
        [
            envelope(1, priority="urgent"),
            envelope(2, priority="routine"),
            envelope(3, priority=None),
        ],
        priority_policy(),
        metrics=metrics,
    )

    assert metrics.counters[MetricNames.PRIORITY_LANE_BATCHES_TOTAL] == 1
    assert metrics.counters[MetricNames.PRIORITY_LANE_MESSAGES_TOTAL] == 3
    assert metrics.gauges[MetricNames.CURRENT_PRIORITY_LANES_ACTIVE] == 2.0
    exported_names = set(metrics.counters) | set(metrics.gauges)
    assert all("orders" not in name for name in exported_names)
    assert all("urgent" not in name for name in exported_names)
