# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reusable fan-out routing certification helpers.

These helpers are for maintainers and future sink implementers who need to
prove that a routing policy selects the intended logical sinks and that the
ACK gate releases only after required destinations have committed.  They use
synthetic envelopes and in-memory operation plans only; no live NATS server,
database, file path, cloud endpoint, credential, or real payload is required.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from nats_sinks.core.ack_gate import FanoutAckGateResult, wait_for_fanout_ack_gate
from nats_sinks.core.config import RoutePolicyRouteConfig, RoutingMatchPolicyConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.routing_policy import RouteSelection, select_route_targets

FanoutCertificationAction = Literal[
    "disabled",
    "matched",
    "reject",
    "default_route",
    "ignore",
]
FanoutOperationOutcome = Literal["commit", "fail", "hang"]

MAX_FANOUT_CERTIFICATION_DELAY_SECONDS = 5.0


@dataclass(slots=True)
class FanoutAckProbe:
    """Small test probe that records when a synthetic ACK would happen."""

    called: bool = False
    call_count: int = 0

    def ack(self) -> None:
        """Record a synthetic ACK after the fan-out ACK gate has released."""

        self.called = True
        self.call_count += 1


@dataclass(frozen=True, slots=True)
class FanoutOperationPlan:
    """Synthetic operation for one selected logical sink target.

    ``outcome='commit'`` simulates durable success, ``outcome='fail'`` raises a
    controlled exception, and ``outcome='hang'`` waits until the ACK gate
    cancels the task.  Delay values are intentionally bounded so certification
    tests cannot become accidental load tests.
    """

    sink: str
    outcome: FanoutOperationOutcome = "commit"
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Validate synthetic plans before the event loop starts."""

        if not self.sink.strip():
            raise ValueError("fan-out certification operation sink must not be blank")
        if self.delay_seconds < 0:
            raise ValueError("fan-out certification delay must not be negative")
        if self.delay_seconds > MAX_FANOUT_CERTIFICATION_DELAY_SECONDS:
            raise ValueError(
                "fan-out certification delay must not exceed "
                f"{MAX_FANOUT_CERTIFICATION_DELAY_SECONDS:.0f} seconds"
            )


@dataclass(frozen=True, slots=True)
class FanoutCertificationCase:
    """Expected route-selection outcome for one synthetic envelope."""

    name: str
    envelope: NatsEnvelope
    policy: RoutingMatchPolicyConfig
    expected_action: FanoutCertificationAction = "matched"
    expected_routes: tuple[str, ...] = field(default_factory=tuple)
    expected_targets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Reject incomplete certification cases early in unit tests."""

        if not self.name.strip():
            raise ValueError("fan-out certification case name must not be empty")
        if self.expected_action in {"matched", "default_route"} and not self.expected_targets:
            raise ValueError(
                "fan-out certification matched/default cases must define expected targets"
            )


@dataclass(frozen=True, slots=True)
class FanoutCertificationResult:
    """Synthetic fan-out outcome with payload-free evidence."""

    selection: RouteSelection
    ack_gate: FanoutAckGateResult | None
    events: tuple[str, ...]
    acked: bool


def fanout_certification_policy() -> RoutingMatchPolicyConfig:
    """Return the documented NATO SECRET and NATO UNCLASS fan-out policy.

    The policy mirrors the public example used in docs and issue evidence:
    urgent NATO SECRET sensor audit events route to an Oracle Database custody
    target and an optional file audit target, while urgent NATO UNCLASS sensor
    audit events route only to a separate Oracle Database target.
    """

    return RoutingMatchPolicyConfig(
        enabled=True,
        mode="first",
        no_match="reject",
        # Public example names are route target identifiers, not credentials.
        target_sink_types={  # nosec B105
            "oracle_secret": "oracle",
            "file_audit": "file",
            "oracle_unclass": "oracle",
        },
        routes=(
            RoutePolicyRouteConfig.model_validate(
                {
                    "name": "nato_secret_sensor_audit",
                    "match": {
                        "subject": "mission.sensor.>",
                        "priority": ["urgent"],
                        "classification": ["NATO SECRET"],
                        "labels_all": ["sensor", "audit"],
                        "headers": [{"name": "Nats-Sinks-Route", "values": ["mission-audit"]}],
                    },
                    "targets": (
                        "oracle_secret",
                        {
                            "sink": "file_audit",
                            "required": False,
                            "minimum_wait_ms": 25,
                            "timeout_ms": 100,
                        },
                    ),
                }
            ),
            RoutePolicyRouteConfig.model_validate(
                {
                    "name": "nato_unclass_sensor_audit",
                    "match": {
                        "subject": "mission.sensor.>",
                        "priority": ["urgent"],
                        "classification": ["NATO UNCLASS"],
                        "labels_all": ["sensor", "audit"],
                    },
                    "targets": ("oracle_unclass",),
                }
            ),
        ),
    )


def fanout_certification_envelope(
    *,
    subject: str = "mission.sensor.alpha",
    priority: str | None = "urgent",
    classification: str | None = "NATO SECRET",
    labels: Sequence[str] = ("sensor", "audit"),
    headers: Mapping[str, str] | None = None,
) -> NatsEnvelope:
    """Build a deterministic, non-sensitive envelope for fan-out tests."""

    rendered_headers = {
        "Nats-Msg-Id": "fanout-certification-message-1",
        "Nats-Sinks-Route": "mission-audit",
    }
    if headers is not None:
        rendered_headers.update(headers)
    return NatsEnvelope(
        subject=subject,
        data=b'{"event_id":"FANOUT-CERT-1","status":"ok"}',
        headers=rendered_headers,
        stream="FANOUT_CERTIFICATION",
        consumer="fanout-certification",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        message_id="fanout-certification-message-1",
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=tuple(labels),
    )


def certify_fanout_route_selection(case: FanoutCertificationCase) -> RouteSelection:
    """Assert route selection for one synthetic fan-out certification case."""

    selection = select_route_targets(case.envelope, case.policy)
    if selection.action != case.expected_action:
        raise AssertionError(
            f"{case.name}: expected action {case.expected_action!r}, got {selection.action!r}"
        )
    if selection.matched_routes != case.expected_routes:
        raise AssertionError(
            f"{case.name}: expected routes {case.expected_routes!r}, "
            f"got {selection.matched_routes!r}"
        )
    if selection.targets != case.expected_targets:
        raise AssertionError(
            f"{case.name}: expected targets {case.expected_targets!r}, got {selection.targets!r}"
        )
    return selection


async def certify_fanout_ack_order(
    case: FanoutCertificationCase,
    operations: Sequence[FanoutOperationPlan],
    *,
    ack: Callable[[], None] | None = None,
    events: MutableSequence[str] | None = None,
    logger: logging.Logger | None = None,
) -> FanoutCertificationResult:
    """Run route selection and ACK-gate certification with synthetic operations.

    The optional ``ack`` callback is invoked only after the ACK gate returns
    success.  Required-operation failures therefore leave the callback untouched,
    which lets tests prove that partial success does not authorize an ACK.
    """

    recorded_events: MutableSequence[str] = events if events is not None else []
    selection = certify_fanout_route_selection(case)
    if not selection.target_policies:
        return FanoutCertificationResult(
            selection=selection,
            ack_gate=None,
            events=tuple(recorded_events),
            acked=False,
        )

    planned = _operation_mapping(operations, recorded_events)
    gate_result = await wait_for_fanout_ack_gate(
        planned,
        selection.target_policies,
        logger=logger,
    )
    if ack is not None:
        ack()
        recorded_events.append("ack")
    return FanoutCertificationResult(
        selection=selection,
        ack_gate=gate_result,
        events=tuple(recorded_events),
        acked=ack is not None,
    )


def _operation_mapping(
    operations: Sequence[FanoutOperationPlan],
    events: MutableSequence[str],
) -> dict[str, Awaitable[object]]:
    planned: dict[str, Awaitable[object]] = {}
    for operation in operations:
        if operation.sink in planned:
            raise ValueError(f"duplicate fan-out certification operation {operation.sink!r}")
        planned[operation.sink] = _run_operation(operation, events)
    return planned


async def _run_operation(
    operation: FanoutOperationPlan,
    events: MutableSequence[str],
) -> str:
    events.append(f"{operation.sink}:started")
    try:
        if operation.outcome == "hang":
            await asyncio.sleep(3600)
        elif operation.delay_seconds:
            await asyncio.sleep(operation.delay_seconds)
        if operation.outcome == "fail":
            events.append(f"{operation.sink}:failed")
            raise RuntimeError("synthetic fan-out certification failure")
        events.append(f"{operation.sink}:committed")
        return operation.sink
    except asyncio.CancelledError:
        events.append(f"{operation.sink}:cancelled")
        raise


__all__ = [
    "FanoutAckProbe",
    "FanoutCertificationAction",
    "FanoutCertificationCase",
    "FanoutCertificationResult",
    "FanoutOperationOutcome",
    "FanoutOperationPlan",
    "certify_fanout_ack_order",
    "certify_fanout_route_selection",
    "fanout_certification_envelope",
    "fanout_certification_policy",
]
