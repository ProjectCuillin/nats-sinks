# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Priority-lane scheduling for already-fetched JetStream batches.

The priority scheduler is intentionally part of the core runtime because it
changes the order in which a sink receives messages.  Sinks still do not ACK,
NAK, or inspect raw NATS client messages.  The scheduler receives immutable
`NatsEnvelope` objects, validates their normalized priority metadata, orders the
current batch, and returns a new list for sink delivery.

This module deliberately avoids global ordering claims.  JetStream still
controls which messages are delivered to the pull consumer.  Priority lanes only
shape a bounded batch after the fetch has completed, which keeps memory use
bounded and preserves the central invariant: commit first, ACK last.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from nats_sinks.core.config import PriorityLaneConfig, PriorityLanesConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.message_metadata import normalise_metadata_value
from nats_sinks.core.metrics import (
    MetricNames,
    MetricsRecorder,
    increment_metric,
    set_metric_value,
)

ASCII_CONTROL_MAX = 31
ASCII_DELETE = 127


@dataclass(frozen=True, slots=True)
class PriorityLaneAssignment:
    """Validated lane decision for one envelope.

    The assignment stores only the lane name and whether the default lane was
    used.  It intentionally does not carry subjects, message IDs, payload data,
    classification values, labels, or priority strings into metrics.
    """

    envelope: NatsEnvelope
    lane_name: str
    defaulted: bool


def order_by_priority_lanes(
    messages: Sequence[NatsEnvelope],
    config: PriorityLanesConfig,
    *,
    metrics: MetricsRecorder | None = None,
) -> list[NatsEnvelope]:
    """Return messages ordered by configured priority lanes.

    When priority lanes are disabled, the input order is returned unchanged.
    When enabled, each envelope is assigned to one lane and the current batch is
    emitted using weighted round-robin.  The weighted pass prevents lower lanes
    from being completely starved inside a mixed batch while still allowing
    high-priority lanes to make faster progress.
    """

    if not messages:
        return []
    if not config.enabled:
        return list(messages)

    assignments = [assign_priority_lane(message, config, metrics=metrics) for message in messages]
    if metrics is not None:
        increment_metric(metrics, MetricNames.PRIORITY_LANE_BATCHES_TOTAL)
        increment_metric(metrics, MetricNames.PRIORITY_LANE_MESSAGES_TOTAL, len(assignments))
        active_lanes = {assignment.lane_name for assignment in assignments}
        set_metric_value(
            metrics, MetricNames.CURRENT_PRIORITY_LANES_ACTIVE, float(len(active_lanes))
        )

    lanes_by_name = {lane.name: lane for lane in config.lanes}
    buckets = _assignment_buckets(assignments, lanes_by_name)
    return [
        assignment.envelope
        for assignment in _weighted_round_robin_assignments(config.lanes, buckets)
    ]


def assign_priority_lane(
    message: NatsEnvelope,
    config: PriorityLanesConfig,
    *,
    metrics: MetricsRecorder | None = None,
) -> PriorityLaneAssignment:
    """Assign one envelope to a validated lane.

    Missing priority values, and unknown values when the policy allows them, go
    to the configured default lane.  Unsafe values with control characters or
    excessive length are always rejected because accepting them would make logs,
    metrics, and policy review less reliable.
    """

    priority = _validated_priority_value(message.priority, config)
    if priority is None:
        _record_defaulted(metrics)
        return PriorityLaneAssignment(
            envelope=message,
            lane_name=config.default_lane,
            defaulted=True,
        )

    lane_name = _priority_lane_name(priority, config)
    if lane_name is not None:
        return PriorityLaneAssignment(envelope=message, lane_name=lane_name, defaulted=False)

    if config.unknown_priority_action == "reject":
        _record_rejected(metrics)
        raise ValidationError("message priority is not allowed by delivery.priority_lanes")

    _record_defaulted(metrics)
    return PriorityLaneAssignment(
        envelope=message,
        lane_name=config.default_lane,
        defaulted=True,
    )


def _validated_priority_value(
    priority: object | None,
    config: PriorityLanesConfig,
) -> str | None:
    """Normalize and bound a priority value received from a message envelope."""

    rendered = normalise_metadata_value(priority)
    if rendered is None:
        return None
    if len(rendered) > config.max_priority_value_length:
        raise ValidationError("message priority exceeds delivery.priority_lanes length limit")
    if _contains_control_character(rendered):
        raise ValidationError("message priority contains control characters")
    return rendered.casefold()


def _contains_control_character(value: str) -> bool:
    """Return whether a metadata value contains unsafe control characters."""

    return any(
        ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE for character in value
    )


def _priority_lane_name(priority: str, config: PriorityLanesConfig) -> str | None:
    """Return the lane configured for a normalized priority value."""

    for lane in config.lanes:
        if priority in lane.priorities:
            return lane.name
    return None


def _assignment_buckets(
    assignments: Sequence[PriorityLaneAssignment],
    lanes_by_name: dict[str, PriorityLaneConfig],
) -> dict[str, deque[PriorityLaneAssignment]]:
    """Group assignments by lane while tolerating only configured lane names."""

    buckets: dict[str, deque[PriorityLaneAssignment]] = {
        lane_name: deque() for lane_name in lanes_by_name
    }
    for assignment in assignments:
        if assignment.lane_name not in buckets:
            raise ValidationError("priority lane assignment referenced an unknown lane")
        buckets[assignment.lane_name].append(assignment)
    return buckets


def _weighted_round_robin_assignments(
    lanes: Iterable[PriorityLaneConfig],
    buckets: dict[str, deque[PriorityLaneAssignment]],
) -> list[PriorityLaneAssignment]:
    """Drain lane buckets using deterministic weighted round-robin."""

    ordered: list[PriorityLaneAssignment] = []
    remaining = sum(len(bucket) for bucket in buckets.values())
    lane_list = list(lanes)

    while remaining:
        progressed = False
        for lane in lane_list:
            bucket = buckets[lane.name]
            for _ in range(lane.weight):
                if not bucket:
                    break
                ordered.append(bucket.popleft())
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return ordered


def _record_defaulted(metrics: MetricsRecorder | None) -> None:
    """Record a default-lane decision without exposing priority or subject text."""

    if metrics is not None:
        increment_metric(metrics, MetricNames.PRIORITY_LANE_DEFAULTED_TOTAL)


def _record_rejected(metrics: MetricsRecorder | None) -> None:
    """Record a rejected priority value without exposing the rejected content."""

    if metrics is not None:
        increment_metric(metrics, MetricNames.PRIORITY_LANE_REJECTED_TOTAL)
