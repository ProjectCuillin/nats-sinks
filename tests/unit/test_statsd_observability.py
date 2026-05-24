# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the StatsD observability connector.

The StatsD connector is intentionally best-effort and observational.  It reads
only local metrics snapshots, applies the shared observability policy, and
renders bounded StatsD datagrams without subjects, payloads, classification
labels, mission metadata, or destination details.
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
    increment_metric,
    observe_metric,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.statsd import (
    DISABLED_STATSD_TEXT,
    EMPTY_STATSD_TEXT,
    build_statsd_datagrams,
    export_statsd_metrics,
    filter_statsd_metric_rows,
    render_statsd_lines,
    statsd_metric_name,
)


class FakeSocket:
    """Small fake socket used instead of a live StatsD endpoint."""

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
        "statsd": {
            "enabled": True,
            "transport": "udp",
            "host": "127.0.0.1",
            "port": 8125,
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_statsd_export_is_safe_noop(tmp_path: Path) -> None:
    result = export_statsd_metrics(_snapshot(tmp_path / "metrics.json"), ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_STATSD_TEXT.strip()


def test_statsd_lines_contain_only_allowed_metrics(tmp_path: Path) -> None:
    rendered = render_statsd_lines(_snapshot(tmp_path / "metrics.json"), _policy())

    assert rendered == "mission_ops.messages_fetched_total:12|g\n"
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered


def test_statsd_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_statsd_metrics(snapshot, policy)

    assert filter_statsd_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_STATSD_TEXT.strip()


def test_statsd_observation_summaries_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_statsd_metric_rows(snapshot, without_observations) == []
    rendered = render_statsd_lines(snapshot, with_observations)

    assert "mission_ops.sink_batch_write_seconds.count:1|g" in rendered
    assert "mission_ops.sink_batch_write_seconds.last:0.25|g" in rendered


def test_statsd_metric_name_normalizes_prefix(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(statsd={"enabled": True, "metric_prefix": "mission.ops-edge"})

    row = filter_statsd_metric_rows(snapshot, policy)[0]

    assert statsd_metric_name(row, policy) == "mission.ops-edge.messages_fetched_total"


def test_statsd_datagram_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        statsd={
            "enabled": True,
            "metric_prefix": "a" * 128,
            "max_datagram_bytes": 128,
        },
    )

    with pytest.raises(ConfigurationError, match="max_datagram_bytes"):
        build_statsd_datagrams(_large_snapshot(tmp_path / "metrics.json"), policy)


def test_statsd_export_sends_udp_datagrams_with_timeout(tmp_path: Path) -> None:
    fake_socket = FakeSocket()
    factories: list[tuple[int, int]] = []

    def socket_factory(family: int, kind: int) -> FakeSocket:
        factories.append((family, kind))
        return fake_socket

    result = export_statsd_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(statsd={"enabled": True, "timeout_seconds": 3.5}),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert result.datagrams == 1
    assert result.message == "StatsD export delivered"
    assert factories == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert fake_socket.timeout == 3.5
    assert fake_socket.closed is True
    assert fake_socket.sent == [(b"mission_ops.messages_fetched_total:12|g", ("127.0.0.1", 8125))]


def test_statsd_export_sends_unix_datagrams(tmp_path: Path) -> None:
    fake_socket = FakeSocket()
    socket_path = tmp_path / "nats-sinks-statsd.sock"

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        return fake_socket

    result = export_statsd_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            statsd={
                "enabled": True,
                "transport": "unixgram",
                "socket_path": str(socket_path),
            }
        ),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert fake_socket.sent == [(b"mission_ops.messages_fetched_total:12|g", str(socket_path))]


def test_statsd_export_retries_bounded_failures(tmp_path: Path) -> None:
    sleeps: list[float] = []
    sockets: list[FakeSocket] = []

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        fake_socket = FakeSocket(fail=True)
        sockets.append(fake_socket)
        return fake_socket

    result = export_statsd_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(statsd={"enabled": True, "max_retries": 2, "retry_backoff_seconds": 0.5}),
        socket_factory=socket_factory,
        sleep=sleeps.append,
    )

    assert result.delivered is False
    assert result.attempts == 3
    assert result.message == "StatsD export failed with OSError"
    assert sleeps == [0.5, 0.5]
    assert all(fake_socket.closed for fake_socket in sockets)


def test_statsd_policy_rejects_unsafe_settings() -> None:
    with pytest.raises(ValueError, match="socket_path is required"):
        ObservabilityPolicy(enabled=True, statsd={"enabled": True, "transport": "unixgram"})

    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(statsd={"host": "127.0.0.1 /bad"})

    with pytest.raises(ValueError, match="metric_prefix"):
        ObservabilityPolicy(statsd={"metric_prefix": "bad prefix"})

    with pytest.raises(ValueError, match="max_datagram_bytes"):
        ObservabilityPolicy(statsd={"max_datagram_bytes": 70_000})
