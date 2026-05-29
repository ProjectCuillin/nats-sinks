# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for aggregate fan-out observability helpers."""

from __future__ import annotations

import logging

import pytest

from nats_sinks.core.ack_gate import FanoutAckGateResult, FanoutTargetResult
from nats_sinks.core.config import RoutingMatchPolicyConfig
from nats_sinks.core.fanout_observability import (
    record_fanout_ack_gate_result,
    record_fanout_required_failure,
    record_fanout_route_selection,
)
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
from nats_sinks.core.routing_policy import select_route_targets
from nats_sinks.testing import fanout_certification_envelope, fanout_certification_policy


def test_fanout_route_selection_records_match_and_selected_children(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics = InMemoryMetrics()
    logger = logging.getLogger("nats_sinks.tests.fanout_observability")
    selection = select_route_targets(fanout_certification_envelope(), fanout_certification_policy())

    caplog.set_level(logging.INFO, logger=logger.name)
    summary = record_fanout_route_selection(metrics, selection, logger=logger)

    assert summary.route_matches == 1
    assert summary.messages_routed == 1
    assert summary.child_sinks_selected == 2
    assert metrics.counters[MetricNames.FANOUT_ROUTE_MATCHES_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_MESSAGES_ROUTED_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_CHILD_SINKS_SELECTED_TOTAL] == 2
    assert metrics.gauges[MetricNames.CURRENT_FANOUT_CHILD_SINKS_SELECTED] == 2.0
    assert "fan-out route selected child sink targets" in caplog.text
    assert "oracle_secret" not in caplog.text
    assert "file_audit" not in caplog.text
    assert "NATO SECRET" not in caplog.text
    assert "FANOUT-CERT-1" not in caplog.text


def test_fanout_route_selection_records_no_route_without_subject_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics = InMemoryMetrics()
    logger = logging.getLogger("nats_sinks.tests.fanout_observability")
    policy = RoutingMatchPolicyConfig(
        enabled=True,
        no_match="reject",
        routes=(
            {
                "name": "known_subject",
                "match": {"subject": "mission.sensor.>"},
                "targets": ["oracle_secret"],
            },
        ),
    )
    selection = select_route_targets(
        fanout_certification_envelope(subject="mission.other.alpha", headers={}),
        policy,
    )

    caplog.set_level(logging.WARNING, logger=logger.name)
    summary = record_fanout_route_selection(metrics, selection, logger=logger)

    assert summary.messages_no_route == 1
    assert summary.child_sinks_selected == 0
    assert metrics.counters[MetricNames.FANOUT_MESSAGES_NO_ROUTE_TOTAL] == 1
    assert metrics.gauges[MetricNames.CURRENT_FANOUT_CHILD_SINKS_SELECTED] == 0.0
    assert "fan-out routing selected no child sink targets" in caplog.text
    assert "mission.other.alpha" not in caplog.text
    assert "oracle_secret" not in caplog.text


def test_fanout_ack_gate_records_success_optional_timeout_and_ack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics = InMemoryMetrics()
    logger = logging.getLogger("nats_sinks.tests.fanout_observability")
    result = FanoutAckGateResult(
        required=(FanoutTargetResult(sink="oracle_secret", required=True, status="committed"),),
        optional=(
            FanoutTargetResult(sink="file_audit", required=False, status="timed_out"),
            FanoutTargetResult(
                sink="file_side_copy",
                required=False,
                status="failed",
                error_type="RuntimeError",
            ),
            FanoutTargetResult(sink="file_fast_copy", required=False, status="committed"),
        ),
    )

    caplog.set_level(logging.WARNING, logger=logger.name)
    summary = record_fanout_ack_gate_result(
        metrics,
        result,
        ack_wait_seconds=0.125,
        batch_seconds=0.25,
        logger=logger,
    )

    assert summary.required_success == 1
    assert summary.optional_success == 1
    assert summary.optional_failure == 1
    assert summary.optional_timeout == 1
    assert summary.messages_acked == 1
    assert metrics.counters[MetricNames.FANOUT_REQUIRED_CHILD_SUCCESS_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_OPTIONAL_CHILD_SUCCESS_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_OPTIONAL_CHILD_FAILURE_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_MESSAGES_ACKED_TOTAL] == 1
    assert metrics.observations[MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS] == [0.125]
    assert metrics.observations[MetricNames.FANOUT_BATCH_SECONDS] == [0.25]
    assert "optional child sink issues" in caplog.text
    assert "oracle_secret" not in caplog.text
    assert "file_audit" not in caplog.text


def test_fanout_required_failure_records_ack_blocked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics = InMemoryMetrics()
    logger = logging.getLogger("nats_sinks.tests.fanout_observability")

    caplog.set_level(logging.WARNING, logger=logger.name)
    summary = record_fanout_required_failure(
        metrics,
        ack_wait_seconds=0.05,
        batch_seconds=0.10,
        logger=logger,
    )

    assert summary.required_failure == 1
    assert summary.messages_ack_blocked == 1
    assert metrics.counters[MetricNames.FANOUT_REQUIRED_CHILD_FAILURE_TOTAL] == 1
    assert metrics.counters[MetricNames.FANOUT_MESSAGES_ACK_BLOCKED_TOTAL] == 1
    assert metrics.observations[MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS] == [0.05]
    assert metrics.observations[MetricNames.FANOUT_BATCH_SECONDS] == [0.10]
    assert "original message remains unacknowledged" in caplog.text
    assert "required sink" not in caplog.text


def test_fanout_observability_rejects_invalid_timing_values() -> None:
    metrics = InMemoryMetrics()
    result = FanoutAckGateResult(required=(), optional=())

    with pytest.raises(ValueError, match="finite non-negative"):
        record_fanout_ack_gate_result(metrics, result, ack_wait_seconds=-0.001)


def test_fanout_metric_recorder_failure_does_not_escape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenMetrics:
        def increment(self, name: str, value: int = 1) -> None:
            del name, value
            raise RuntimeError("synthetic metrics failure")

        def observe(self, name: str, value: float) -> None:
            del name, value
            raise RuntimeError("synthetic metrics failure")

        def set_value(self, name: str, value: float) -> None:
            del name, value
            raise RuntimeError("synthetic metrics failure")

    logger = logging.getLogger("nats_sinks.tests.fanout_observability")
    selection = select_route_targets(fanout_certification_envelope(), fanout_certification_policy())

    caplog.set_level(logging.WARNING, logger=logger.name)
    record_fanout_route_selection(BrokenMetrics(), selection, logger=logger)

    assert "fan-out metric update failed; delivery decision unchanged" in caplog.text
    assert "synthetic metrics failure" not in caplog.text
