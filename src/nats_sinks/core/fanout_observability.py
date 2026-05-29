# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Aggregate observability helpers for fan-out delivery.

Fan-out delivery can touch several child sinks for one NATS message. Operators
need evidence about routing and ACK-gate outcomes, but that evidence must not
leak payloads, private subjects, classification values, file paths, connection
strings, or sink instance names by default. This module therefore records only
aggregate counts and bounded timing observations.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from nats_sinks.core.ack_gate import FanoutAckGateResult
from nats_sinks.core.metrics import (
    MetricNames,
    MetricsRecorder,
    increment_metric,
    observe_metric,
    set_metric_value,
)
from nats_sinks.core.routing_policy import RouteSelection

FanoutLogOutcome = Literal["routed", "no_route", "acked", "ack_blocked"]


@dataclass(frozen=True, slots=True)
class FanoutObservabilitySummary:
    """Payload-free fan-out evidence returned to tests and callers."""

    route_matches: int = 0
    messages_routed: int = 0
    messages_no_route: int = 0
    child_sinks_selected: int = 0
    required_success: int = 0
    required_failure: int = 0
    optional_success: int = 0
    optional_failure: int = 0
    optional_timeout: int = 0
    messages_acked: int = 0
    messages_ack_blocked: int = 0


def record_fanout_route_selection(
    metrics: MetricsRecorder | None,
    selection: RouteSelection,
    *,
    logger: logging.Logger | None = None,
) -> FanoutObservabilitySummary:
    """Record route-selection metrics without exposing route or sink names."""

    route_matches = len(selection.matched_routes)
    child_sinks_selected = len(selection.targets)
    messages_routed = 1 if child_sinks_selected else 0
    messages_no_route = 1 if selection.action in {"reject", "ignore"} else 0

    _increment(metrics, MetricNames.FANOUT_ROUTE_MATCHES_TOTAL, route_matches, logger=logger)
    _increment(metrics, MetricNames.FANOUT_MESSAGES_ROUTED_TOTAL, messages_routed, logger=logger)
    _increment(
        metrics,
        MetricNames.FANOUT_MESSAGES_NO_ROUTE_TOTAL,
        messages_no_route,
        logger=logger,
    )
    _increment(
        metrics,
        MetricNames.FANOUT_CHILD_SINKS_SELECTED_TOTAL,
        child_sinks_selected,
        logger=logger,
    )
    _set_value(
        metrics,
        MetricNames.CURRENT_FANOUT_CHILD_SINKS_SELECTED,
        float(child_sinks_selected),
        logger=logger,
    )

    if child_sinks_selected:
        _log(
            logger,
            logging.INFO,
            "fan-out route selected child sink targets",
            outcome="routed",
            route_matches=route_matches,
            child_sinks_selected=child_sinks_selected,
        )
    elif messages_no_route:
        _log(
            logger,
            logging.WARNING if selection.action == "reject" else logging.INFO,
            "fan-out routing selected no child sink targets",
            outcome="no_route",
            route_matches=route_matches,
            child_sinks_selected=0,
        )

    return FanoutObservabilitySummary(
        route_matches=route_matches,
        messages_routed=messages_routed,
        messages_no_route=messages_no_route,
        child_sinks_selected=child_sinks_selected,
    )


def record_fanout_ack_gate_result(
    metrics: MetricsRecorder | None,
    result: FanoutAckGateResult,
    *,
    ack_wait_seconds: float,
    batch_seconds: float | None = None,
    acked: bool = True,
    logger: logging.Logger | None = None,
) -> FanoutObservabilitySummary:
    """Record ACK-gate outcome metrics for a successful or partially successful fan-out."""

    required_success = _count_status(result.required, status="committed")
    required_failure = len(result.required) - required_success
    optional_success = _count_status(result.optional, status="committed")
    optional_failure = _count_status(result.optional, status="failed")
    optional_timeout = _count_status(result.optional, status="timed_out")
    messages_acked = 1 if acked else 0
    messages_ack_blocked = 0 if acked else 1

    _increment(
        metrics,
        MetricNames.FANOUT_REQUIRED_CHILD_SUCCESS_TOTAL,
        required_success,
        logger=logger,
    )
    _increment(
        metrics,
        MetricNames.FANOUT_REQUIRED_CHILD_FAILURE_TOTAL,
        required_failure,
        logger=logger,
    )
    _increment(
        metrics,
        MetricNames.FANOUT_OPTIONAL_CHILD_SUCCESS_TOTAL,
        optional_success,
        logger=logger,
    )
    _increment(
        metrics,
        MetricNames.FANOUT_OPTIONAL_CHILD_FAILURE_TOTAL,
        optional_failure,
        logger=logger,
    )
    _increment(
        metrics,
        MetricNames.FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL,
        optional_timeout,
        logger=logger,
    )
    _increment(metrics, MetricNames.FANOUT_MESSAGES_ACKED_TOTAL, messages_acked, logger=logger)
    _increment(
        metrics,
        MetricNames.FANOUT_MESSAGES_ACK_BLOCKED_TOTAL,
        messages_ack_blocked,
        logger=logger,
    )
    _observe_seconds(metrics, MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS, ack_wait_seconds, logger)
    if batch_seconds is not None:
        _observe_seconds(metrics, MetricNames.FANOUT_BATCH_SECONDS, batch_seconds, logger)

    has_optional_issue = bool(optional_failure or optional_timeout)
    _log(
        logger,
        logging.WARNING if has_optional_issue else logging.INFO,
        (
            "fan-out ACK gate released with optional child sink issues"
            if has_optional_issue
            else "fan-out ACK gate released after required child sink success"
        ),
        outcome="acked" if acked else "ack_blocked",
        required_success=required_success,
        required_failure=required_failure,
        optional_success=optional_success,
        optional_failure=optional_failure,
        optional_timeout=optional_timeout,
    )

    return FanoutObservabilitySummary(
        required_success=required_success,
        required_failure=required_failure,
        optional_success=optional_success,
        optional_failure=optional_failure,
        optional_timeout=optional_timeout,
        messages_acked=messages_acked,
        messages_ack_blocked=messages_ack_blocked,
    )


def record_fanout_required_failure(
    metrics: MetricsRecorder | None,
    *,
    ack_wait_seconds: float | None = None,
    batch_seconds: float | None = None,
    logger: logging.Logger | None = None,
) -> FanoutObservabilitySummary:
    """Record a required child-sink failure that prevents the original ACK."""

    _increment(metrics, MetricNames.FANOUT_REQUIRED_CHILD_FAILURE_TOTAL, 1, logger=logger)
    _increment(metrics, MetricNames.FANOUT_MESSAGES_ACK_BLOCKED_TOTAL, 1, logger=logger)
    if ack_wait_seconds is not None:
        _observe_seconds(
            metrics,
            MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS,
            ack_wait_seconds,
            logger,
        )
    if batch_seconds is not None:
        _observe_seconds(metrics, MetricNames.FANOUT_BATCH_SECONDS, batch_seconds, logger)

    _log(
        logger,
        logging.WARNING,
        "fan-out required child sink failed before ACK; original message remains unacknowledged",
        outcome="ack_blocked",
        required_failure=1,
        messages_ack_blocked=1,
    )

    return FanoutObservabilitySummary(required_failure=1, messages_ack_blocked=1)


def _increment(
    metrics: MetricsRecorder | None,
    name: str,
    value: int,
    *,
    logger: logging.Logger | None,
) -> None:
    if value <= 0:
        return
    _metric_update(metrics, lambda recorder: increment_metric(recorder, name, value), logger=logger)


def _set_value(
    metrics: MetricsRecorder | None,
    name: str,
    value: float,
    *,
    logger: logging.Logger | None,
) -> None:
    _metric_update(metrics, lambda recorder: set_metric_value(recorder, name, value), logger=logger)


def _observe_seconds(
    metrics: MetricsRecorder | None,
    name: str,
    value: float,
    logger: logging.Logger | None,
) -> None:
    if value < 0 or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite non-negative number of seconds")
    _metric_update(metrics, lambda recorder: observe_metric(recorder, name, value), logger=logger)


def _metric_update(
    metrics: MetricsRecorder | None,
    update: Callable[[MetricsRecorder], None],
    *,
    logger: logging.Logger | None,
) -> None:
    if metrics is None:
        return
    try:
        update(metrics)
    except Exception as exc:  # pragma: no cover - defensive around external recorders.
        _log(
            logger,
            logging.WARNING,
            "fan-out metric update failed; delivery decision unchanged",
            error_type=type(exc).__name__,
        )


def _count_status(results: tuple[object, ...], *, status: str) -> int:
    return sum(1 for result in results if getattr(result, "status", None) == status)


def _log(
    logger: logging.Logger | None,
    level: int,
    message: str,
    *,
    outcome: FanoutLogOutcome | None = None,
    **extra: object,
) -> None:
    if logger is None:
        return
    safe_extra: dict[str, object] = {"fanout": True}
    if outcome is not None:
        safe_extra["outcome"] = outcome
    safe_extra.update(extra)
    logger.log(level, message, extra=safe_extra)


__all__ = [
    "FanoutObservabilitySummary",
    "record_fanout_ack_gate_result",
    "record_fanout_required_failure",
    "record_fanout_route_selection",
]
