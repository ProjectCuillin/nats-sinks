# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the syslog observability bridge.

The syslog bridge is intentionally best-effort and observational.  It reads
only local metrics snapshots, applies the shared observability policy, and
renders bounded RFC 5424-style messages without subjects, payloads,
classification labels, mission metadata, or destination details.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricNames, MetricRow
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.syslog import (
    DISABLED_SYSLOG_TEXT,
    EMPTY_SYSLOG_TEXT,
    SYSLOG_PROFILE_NAME,
    build_syslog_datagrams,
    build_syslog_message,
    export_syslog_metrics,
    filter_syslog_metric_rows,
    render_syslog_messages,
)


class FakeSocket:
    """Small fake socket used instead of a live syslog receiver."""

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


def _snapshot() -> dict[str, object]:
    return {
        "schema": "nats_sinks.metrics.snapshot.v1",
        "namespace": "mission_ops",
        "generated_at_epoch_seconds": 1_790_000_000.0,
        "counters": {
            "messages_fetched_total": 12,
            "messages_acked_total": 11,
        },
        "gauges": {},
        "observations": {
            "sink_batch_write_seconds": {
                "count": 1,
                "sum": 0.25,
                "min": 0.25,
                "max": 0.25,
                "last": 0.25,
            }
        },
    }


def _large_snapshot() -> dict[str, object]:
    return {
        "schema": "nats_sinks.metrics.snapshot.v1",
        "namespace": "mission_ops",
        "generated_at_epoch_seconds": 1_790_000_000.0,
        "counters": {
            "messages_fetched_total": 12,
            "messages_acked_total": 11,
            "sink_batches_written_total": 4,
        },
        "gauges": {},
        "observations": {
            "sink_batch_write_seconds": {
                "count": 1,
                "sum": 0.25,
                "min": 0.25,
                "max": 0.25,
                "last": 0.25,
            }
        },
    }


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
        "syslog": {
            "enabled": True,
            "transport": "udp",
            "host": "127.0.0.1",
            "port": 514,
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_syslog_export_is_safe_noop() -> None:
    result = export_syslog_metrics(_snapshot(), ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_SYSLOG_TEXT.strip()


def test_syslog_messages_contain_only_allowed_metrics() -> None:
    rendered = render_syslog_messages(_snapshot(), _policy())

    assert rendered.startswith("<134>1 2026-09-21T14:13:20.000Z - nats-sinks - metrics ")
    assert '[nats_sinks metric="messages_fetched_total" kind="counter" value="12"' in rendered
    assert f'profile="{SYSLOG_PROFILE_NAME}"' in rendered
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered


def test_syslog_deny_list_wins_over_allow_list() -> None:
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_syslog_metrics(_snapshot(), policy)

    assert filter_syslog_metric_rows(_snapshot(), policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_SYSLOG_TEXT.strip()


def test_syslog_observation_summaries_follow_shared_policy() -> None:
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_syslog_metric_rows(_snapshot(), without_observations) == []
    rendered = render_syslog_messages(_snapshot(), with_observations)

    assert 'metric="sink_batch_write_seconds.count"' in rendered
    assert 'metric="sink_batch_write_seconds.last"' in rendered


def test_syslog_message_escapes_structured_data_values() -> None:
    snapshot = _snapshot()
    policy = _policy()
    row = MetricRow(kind="counter", name='bad"metric]name', value=1)

    message = build_syslog_message(row, snapshot, policy)

    assert 'metric="bad\\"metric\\]name"' in message


def test_syslog_message_size_limit_fails_closed() -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        syslog={
            "enabled": True,
            "app_name": "nats-sinks-with-long-name",
            "max_message_bytes": 128,
        },
    )

    with pytest.raises(ConfigurationError, match="max_message_bytes"):
        build_syslog_datagrams(_large_snapshot(), policy)


def test_syslog_export_sends_udp_datagrams_with_timeout() -> None:
    fake_socket = FakeSocket()
    factories: list[tuple[int, int]] = []

    def socket_factory(family: int, kind: int) -> FakeSocket:
        factories.append((family, kind))
        return fake_socket

    result = export_syslog_metrics(
        _snapshot(),
        _policy(syslog={"enabled": True, "timeout_seconds": 3.5}),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert result.messages == 1
    assert result.message == "Syslog export delivered"
    assert factories == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert fake_socket.timeout == 3.5
    assert fake_socket.closed is True
    assert fake_socket.sent[0][1] == ("127.0.0.1", 514)
    assert fake_socket.sent[0][0].startswith(b"<134>1 ")


def test_syslog_export_sends_unix_datagrams(tmp_path: Path) -> None:
    fake_socket = FakeSocket()
    socket_path = tmp_path / "nats-sinks-syslog.sock"

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        return fake_socket

    result = export_syslog_metrics(
        _snapshot(),
        _policy(
            syslog={
                "enabled": True,
                "transport": "unixgram",
                "socket_path": str(socket_path),
            }
        ),
        socket_factory=socket_factory,
    )

    assert result.delivered is True
    assert fake_socket.sent[0][1] == str(socket_path)


def test_syslog_export_retries_bounded_failures() -> None:
    sleeps: list[float] = []
    sockets: list[FakeSocket] = []

    def socket_factory(_family: int, _kind: int) -> FakeSocket:
        fake_socket = FakeSocket(fail=True)
        sockets.append(fake_socket)
        return fake_socket

    result = export_syslog_metrics(
        _snapshot(),
        _policy(syslog={"enabled": True, "max_retries": 2, "retry_backoff_seconds": 0.5}),
        socket_factory=socket_factory,
        sleep=sleeps.append,
    )

    assert result.delivered is False
    assert result.attempts == 3
    assert result.message == "Syslog export failed with OSError"
    assert sleeps == [0.5, 0.5]
    assert all(fake_socket.closed for fake_socket in sockets)


def test_syslog_policy_rejects_unsafe_settings() -> None:
    with pytest.raises(ValueError, match="socket_path is required"):
        ObservabilityPolicy(enabled=True, syslog={"enabled": True, "transport": "unixgram"})

    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(syslog={"host": "127.0.0.1 /bad"})

    with pytest.raises(ValueError, match="app_name"):
        ObservabilityPolicy(syslog={"app_name": "bad value"})

    with pytest.raises(ValueError, match="structured_data_id"):
        ObservabilityPolicy(syslog={"structured_data_id": "bad value"})

    with pytest.raises(ValueError, match="max_message_bytes"):
        ObservabilityPolicy(syslog={"max_message_bytes": 70_000})
