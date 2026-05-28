# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Datadog DogStatsD observability connector.

The connector is intentionally best-effort and observational. It reads only
local metrics snapshots, applies the shared observability policy, and renders
bounded DogStatsD datagrams without subjects, payloads, classification labels,
mission metadata, destination details, or Datadog API credentials.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import (
    METRIC_SPECS,
    JsonFileMetrics,
    MetricNames,
    MetricRow,
    increment_metric,
    observe_metric,
)
from nats_sinks.observability.datadog import (
    DISABLED_DATADOG_TEXT,
    EMPTY_DATADOG_TEXT,
    build_datadog_datagrams,
    datadog_metric_name,
    export_datadog_metrics,
    filter_datadog_metric_rows,
    render_datadog_lines,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.subject_family import attach_labeled_metric_rows


class FakeSocket:
    """Small fake socket used instead of a live DogStatsD endpoint."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.timeout: float | None = None
        self.closed = False
        self.sent: list[tuple[bytes, object]] = []

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendto(self, data: bytes, address: object) -> int:
        if self.fail:
            raise OSError("simulated send failure")
        self.sent.append((data, address))
        return len(data)

    def close(self) -> None:
        self.closed = True


def _snapshot(path: Path) -> dict[str, object]:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 12)
    increment_metric(metrics, MetricNames.MESSAGES_ACKED_TOTAL, 11)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    return metrics.snapshot()


def _large_snapshot(path: Path) -> dict[str, object]:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    for index, spec in enumerate(METRIC_SPECS):
        increment_metric(metrics, spec.name, index + 1)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    return metrics.snapshot()


def _policy(**overrides: object) -> ObservabilityPolicy:
    base: dict[str, object] = {
        "enabled": True,
        "namespace": "mission_ops",
        "allowed_metrics": [MetricNames.MESSAGES_FETCHED_TOTAL],
        "allowed_metric_patterns": [],
        "denied_metrics": [],
        "denied_metric_patterns": [],
        "include_observations": False,
        "include_legacy": False,
        "subjects": [],
        "datadog": {
            "enabled": True,
            "transport": "udp",
            "host": "127.0.0.1",
            "port": 8125,
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_datadog_export_is_safe_noop(tmp_path: Path) -> None:
    result = export_datadog_metrics(_snapshot(tmp_path / "metrics.json"), ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_DATADOG_TEXT.strip()


def test_datadog_lines_contain_only_allowed_metrics(tmp_path: Path) -> None:
    rendered = render_datadog_lines(_snapshot(tmp_path / "metrics.json"), _policy())

    assert rendered == "mission_ops.messages_fetched_total:12|g\n"
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered


def test_datadog_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_datadog_metrics(snapshot, policy)

    assert filter_datadog_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_DATADOG_TEXT.strip()


def test_datadog_observation_summaries_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_datadog_metric_rows(snapshot, without_observations) == []
    rendered = render_datadog_lines(snapshot, with_observations)

    assert "mission_ops.sink_batch_write_seconds.count:1|g" in rendered
    assert "mission_ops.sink_batch_write_seconds.last:0.25|g" in rendered


def test_datadog_metric_name_normalizes_prefix(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(datadog={"enabled": True, "metric_prefix": "mission.ops-edge"})

    row = filter_datadog_metric_rows(snapshot, policy)[0]

    assert datadog_metric_name(row, policy) == "mission.ops-edge.messages_fetched_total"


def test_datadog_static_tags_are_explicit_and_sorted(tmp_path: Path) -> None:
    rendered = render_datadog_lines(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            datadog={
                "enabled": True,
                "tags": {
                    "environment": "test",
                    "service": "nats-sinks",
                },
            }
        ),
    )

    assert rendered == (
        "mission_ops.messages_fetched_total:12|g|#environment:test,service:nats-sinks\n"
    )


def test_datadog_prepared_labels_are_tags_only_when_enabled(tmp_path: Path) -> None:
    snapshot = attach_labeled_metric_rows(
        _snapshot(tmp_path / "metrics.json"),
        [
            MetricRow(
                kind="counter",
                name=MetricNames.MESSAGES_FETCHED_TOTAL,
                value=4.0,
                labels={"subject_family": "sensor_track"},
            )
        ],
    )
    subject_metrics = {
        "enabled": True,
        "rules": [{"subject": "sensor.>", "action": "allow", "label": "sensor_track"}],
    }
    without_label_tags = render_datadog_lines(
        snapshot,
        _policy(subject_metrics=subject_metrics),
    )
    with_label_tags = render_datadog_lines(
        snapshot,
        _policy(
            subject_metrics=subject_metrics,
            datadog={
                "enabled": True,
                "include_metric_labels_as_tags": True,
            },
        ),
    )

    assert "subject_family" not in without_label_tags
    assert "mission_ops.messages_fetched_total:4|g\n" in without_label_tags
    assert "mission_ops.messages_fetched_total:4|g|#subject_family:sensor_track" in with_label_tags


def test_datadog_datagram_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        datadog={
            "enabled": True,
            "metric_prefix": "a" * 128,
            "tags": {"environment": "test"},
            "max_datagram_bytes": 128,
        },
    )

    with pytest.raises(ConfigurationError, match="max_datagram_bytes"):
        build_datadog_datagrams(_large_snapshot(tmp_path / "metrics.json"), policy)


def test_datadog_export_sends_udp_datagrams_with_timeout(tmp_path: Path) -> None:
    fake_socket = FakeSocket()
    factories: list[tuple[int, int]] = []

    def socket_factory(family: int, kind: int) -> FakeSocket:
        factories.append((family, kind))
        return fake_socket

    result = export_datadog_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(datadog={"enabled": True, "timeout_seconds": 3.5}),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert result.datagrams == 1
    assert result.message == "Datadog export delivered"
    assert factories == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert fake_socket.timeout == 3.5
    assert fake_socket.closed is True
    assert fake_socket.sent == [(b"mission_ops.messages_fetched_total:12|g", ("127.0.0.1", 8125))]


def test_datadog_export_sends_unix_datagrams(tmp_path: Path) -> None:
    fake_socket = FakeSocket()
    socket_path = tmp_path / "nats-sinks-datadog.sock"

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        return fake_socket

    result = export_datadog_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            datadog={
                "enabled": True,
                "transport": "unixgram",
                "socket_path": str(socket_path),
            }
        ),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert fake_socket.sent == [(b"mission_ops.messages_fetched_total:12|g", str(socket_path))]


def test_datadog_export_retries_bounded_failures(tmp_path: Path) -> None:
    sleeps: list[float] = []
    sockets: list[FakeSocket] = []

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        fake_socket = FakeSocket(fail=True)
        sockets.append(fake_socket)
        return fake_socket

    result = export_datadog_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(datadog={"enabled": True, "max_retries": 2, "retry_backoff_seconds": 0.5}),
        socket_factory=socket_factory,
        sleep=sleeps.append,
    )

    assert result.delivered is False
    assert result.attempts == 3
    assert result.message == "Datadog export failed with OSError"
    assert sleeps == [0.5, 0.5]
    assert all(fake_socket.closed for fake_socket in sockets)


def test_datadog_policy_rejects_unsafe_settings() -> None:
    with pytest.raises(ValueError, match="socket_path is required"):
        ObservabilityPolicy(enabled=True, datadog={"enabled": True, "transport": "unixgram"})

    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(datadog={"host": "127.0.0.1 /bad"})

    with pytest.raises(ValueError, match="metric_prefix"):
        ObservabilityPolicy(datadog={"metric_prefix": "bad prefix"})

    with pytest.raises(ValueError, match="tags"):
        ObservabilityPolicy(datadog={"tags": {"subject": "sensor_track"}})

    with pytest.raises(ValueError, match="tags"):
        ObservabilityPolicy(datadog={"tags": {"environment": "NATO_SECRET"}})

    with pytest.raises(ValueError, match="max_datagram_bytes"):
        ObservabilityPolicy(datadog={"max_datagram_bytes": 70_000})
