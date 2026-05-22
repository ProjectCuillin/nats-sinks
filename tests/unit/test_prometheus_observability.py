# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Prometheus observability connector.

The connector should export only metrics explicitly approved by policy.  These
tests intentionally avoid network calls; Prometheus integration is represented
as a local textfile that a separate node_exporter process may scrape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import (
    METRIC_SPECS,
    JsonFileMetrics,
    MetricNames,
    increment_metric,
    observe_metric,
    set_metric_value,
)
from nats_sinks.observability.policy import ObservabilityPolicy, PrometheusTextfilePolicy
from nats_sinks.observability.prometheus import (
    DISABLED_PROMETHEUS_TEXT,
    filter_metric_rows,
    render_prometheus_textfile,
    write_prometheus_textfile,
)
from nats_sinks.observability.prometheus_http import (
    build_prometheus_http_server,
    render_prometheus_http_response,
)


def _snapshot(path: Path) -> dict[str, object]:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 12)
    increment_metric(metrics, MetricNames.MESSAGES_ACKED_TOTAL, 11)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, 2)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 4.0)
    return metrics.snapshot()


def _large_snapshot(path: Path) -> None:
    """Create a snapshot that exceeds the minimum HTTP response cap."""

    metrics = JsonFileMetrics(path, namespace="mission_ops")
    for index, metric_spec in enumerate(METRIC_SPECS):
        increment_metric(metrics, metric_spec.name, index + 1)
    for _ in range(20):
        observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)


def test_disabled_policy_exports_no_metrics() -> None:
    assert render_prometheus_textfile(None, ObservabilityPolicy()) == DISABLED_PROMETHEUS_TEXT


def test_exact_allow_list_exports_only_selected_metric(tmp_path: Path) -> None:
    policy = ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL],
        prometheus=PrometheusTextfilePolicy(enabled=True),
    )

    rendered = render_prometheus_textfile(_snapshot(tmp_path / "metrics.json"), policy)

    assert "mission_ops_messages_fetched_total 12" in rendered
    assert "messages_acked_total" not in rendered
    assert "oracle_duplicates_total" not in rendered


def test_pattern_allow_list_can_export_sink_specific_metrics(tmp_path: Path) -> None:
    policy = ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        allowed_metric_patterns=["oracle_*"],
        prometheus=PrometheusTextfilePolicy(enabled=True),
    )

    rendered = render_prometheus_textfile(_snapshot(tmp_path / "metrics.json"), policy)

    assert "mission_ops_oracle_duplicates_total 2" in rendered
    assert "mission_ops_messages_fetched_total" not in rendered


def test_deny_list_overrides_allow_list(tmp_path: Path) -> None:
    policy = ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        allowed_metric_patterns=["messages_*"],
        denied_metrics=[MetricNames.MESSAGES_ACKED_TOTAL],
        prometheus=PrometheusTextfilePolicy(enabled=True),
    )

    rows = filter_metric_rows(_snapshot(tmp_path / "metrics.json"), policy)

    assert {row.name for row in rows} == {MetricNames.MESSAGES_FETCHED_TOTAL}


def test_observations_are_excluded_by_default_and_optional(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    base_policy = ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        prometheus=PrometheusTextfilePolicy(enabled=True),
    )
    observation_policy = base_policy.model_copy(update={"include_observations": True})

    assert "sink_batch_write_seconds" not in render_prometheus_textfile(snapshot, base_policy)
    assert "mission_ops_sink_batch_write_seconds_count 1" in render_prometheus_textfile(
        snapshot, observation_policy
    )


def test_prometheus_textfile_writer_uses_atomic_output(tmp_path: Path) -> None:
    output = tmp_path / "collector" / "nats_sinks.prom"

    write_prometheus_textfile("nats_sinks_messages_fetched_total 1\n", output)

    assert output.read_text(encoding="utf-8") == "nats_sinks_messages_fetched_total 1\n"
    assert list(output.parent.glob("*.tmp")) == []


def test_disabled_native_http_endpoint_returns_no_metrics(tmp_path: Path) -> None:
    response = render_prometheus_http_response(
        tmp_path / "missing.json",
        ObservabilityPolicy(),
        request_path="/metrics",
    )

    assert response.status_code == 404
    assert response.body.decode("utf-8") == DISABLED_PROMETHEUS_TEXT


def test_native_http_endpoint_serves_allowed_metrics(tmp_path: Path) -> None:
    snapshot = tmp_path / "metrics.json"
    _snapshot(snapshot)
    policy = ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL],
        prometheus=PrometheusTextfilePolicy(
            http_endpoint={"enabled": True, "path": "/metrics"},
        ),
    )

    response = render_prometheus_http_response(snapshot, policy, request_path="/metrics")

    assert response.status_code == 200
    rendered = response.body.decode("utf-8")
    assert "mission_ops_messages_fetched_total 12" in rendered
    assert "messages_acked_total" not in rendered


def test_native_http_endpoint_rejects_wrong_path(tmp_path: Path) -> None:
    policy = ObservabilityPolicy(
        enabled=True,
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL],
        prometheus=PrometheusTextfilePolicy(
            http_endpoint={"enabled": True, "path": "/metrics"},
        ),
    )

    response = render_prometheus_http_response(
        tmp_path / "missing.json",
        policy,
        request_path="/other",
    )

    assert response.status_code == 404
    assert b"not found" in response.body


def test_native_http_endpoint_fails_closed_for_stale_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "metrics.json"
    _snapshot(snapshot)
    policy = ObservabilityPolicy(
        enabled=True,
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL],
        prometheus=PrometheusTextfilePolicy(
            stale_after_seconds=1,
            http_endpoint={"enabled": True},
        ),
    )

    response = render_prometheus_http_response(
        snapshot,
        policy,
        request_path="/metrics",
        now=9_999_999_999,
    )

    assert response.status_code == 503
    assert b"stale" in response.body


def test_native_http_endpoint_enforces_response_size_policy(tmp_path: Path) -> None:
    snapshot = tmp_path / "metrics.json"
    _large_snapshot(snapshot)
    policy = ObservabilityPolicy(
        enabled=True,
        allowed_metric_patterns=["*"],
        include_observations=True,
        include_legacy=True,
        prometheus=PrometheusTextfilePolicy(
            include_help=True,
            include_type=True,
            http_endpoint={"enabled": True, "response_max_bytes": 1024},
        ),
    )

    response = render_prometheus_http_response(snapshot, policy, request_path="/metrics")

    assert response.status_code == 503
    assert b"response_max_bytes" in response.body


def test_native_http_server_requires_explicit_enabled_policy(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="disabled by observability policy"):
        build_prometheus_http_server(tmp_path / "metrics.json", ObservabilityPolicy())
