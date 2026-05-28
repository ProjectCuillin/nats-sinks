# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Amazon CloudWatch observability connector.

The connector is intentionally observational. It reads only local metrics
snapshots, applies the shared observability policy, and renders or sends
CloudWatch `PutMetricData` requests without subjects, payloads, classifications,
mission metadata, destination details, account IDs, regions, or credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
from nats_sinks.observability.cloudwatch import (
    CLOUDWATCH_PROFILE_NAME,
    DISABLED_CLOUDWATCH_TEXT,
    EMPTY_CLOUDWATCH_TEXT,
    build_cloudwatch_metric_data,
    build_cloudwatch_put_metric_data_requests,
    export_cloudwatch_metrics,
    filter_cloudwatch_metric_rows,
    render_cloudwatch_put_metric_data_requests_json,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.subject_family import attach_labeled_metric_rows


class FakeCloudWatchClient:
    """Small fake CloudWatch client used instead of a live AWS account."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[dict[str, object]] = []

    def put_metric_data(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("simulated CloudWatch timeout")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


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
    base: dict[str, Any] = {
        "enabled": True,
        "namespace": "mission_ops",
        "allowed_metrics": [MetricNames.MESSAGES_FETCHED_TOTAL],
        "allowed_metric_patterns": [],
        "denied_metrics": [],
        "denied_metric_patterns": [],
        "include_observations": False,
        "include_legacy": False,
        "subjects": [],
        "cloudwatch": {
            "enabled": True,
            "metric_namespace": "nats-sinks/metrics",
            "region": "eu-west-1",
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_cloudwatch_export_is_safe_noop(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")

    result = export_cloudwatch_metrics(snapshot, ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_CLOUDWATCH_TEXT.strip()


def test_cloudwatch_requests_contain_only_allowed_metrics(tmp_path: Path) -> None:
    requests = build_cloudwatch_put_metric_data_requests(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
    )
    rendered = json.dumps(requests, sort_keys=True)

    assert CLOUDWATCH_PROFILE_NAME == "cloudwatch"
    assert requests == [
        {
            "Namespace": "nats-sinks/metrics",
            "MetricData": [
                {
                    "MetricName": "mission_ops_messages_fetched_total",
                    "Value": 12.0,
                    "Unit": "None",
                    "StorageResolution": 60,
                }
            ],
        }
    ]
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered
    assert "eu-west-1" not in rendered
    assert "AWS_ACCESS_KEY_ID" not in rendered


def test_cloudwatch_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_cloudwatch_metrics(snapshot, policy)

    assert filter_cloudwatch_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_CLOUDWATCH_TEXT.strip()


def test_cloudwatch_observations_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_cloudwatch_metric_rows(snapshot, without_observations) == []
    data = build_cloudwatch_metric_data(snapshot, with_observations)

    metric_names = {datum["MetricName"] for datum in data}
    assert "mission_ops_sink_batch_write_seconds_count" in metric_names
    assert "mission_ops_sink_batch_write_seconds_last" in metric_names


def test_cloudwatch_static_dimensions_are_low_cardinality(tmp_path: Path) -> None:
    data = build_cloudwatch_metric_data(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "nats-sinks/metrics",
                "region": "eu-west-1",
                "dimensions": {
                    "deployment": "edge",
                    "environment": "test",
                },
            }
        ),
    )

    assert data[0]["Dimensions"] == [  # type: ignore[index]
        {"Name": "deployment", "Value": "edge"},
        {"Name": "environment", "Value": "test"},
    ]


def test_cloudwatch_prepared_labels_are_dimensions_only_when_explicitly_enabled(
    tmp_path: Path,
) -> None:
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
    policy_without_label_dimensions = _policy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "sensor.>", "action": "allow", "label": "sensor_track"}],
        }
    )
    policy_with_label_dimensions = _policy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "sensor.>", "action": "allow", "label": "sensor_track"}],
        },
        cloudwatch={
            "enabled": True,
            "metric_namespace": "nats-sinks/metrics",
            "region": "eu-west-1",
            "include_metric_labels_as_dimensions": True,
        },
    )

    without_dimensions = build_cloudwatch_metric_data(snapshot, policy_without_label_dimensions)
    with_dimensions = build_cloudwatch_metric_data(snapshot, policy_with_label_dimensions)

    assert len(without_dimensions) == 1
    assert len(with_dimensions) == 2
    labeled = next(datum for datum in with_dimensions if datum.get("Value") == 4.0)
    assert labeled["Dimensions"] == [{"Name": "subject_family", "Value": "sensor_track"}]


def test_cloudwatch_request_batch_size_splits_requests(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL, MetricNames.MESSAGES_ACKED_TOTAL],
        cloudwatch={
            "enabled": True,
            "metric_namespace": "nats-sinks/metrics",
            "region": "eu-west-1",
            "max_metrics_per_request": 1,
        },
    )

    requests = build_cloudwatch_put_metric_data_requests(snapshot, policy)

    assert len(requests) == 2
    assert all(len(request["MetricData"]) == 1 for request in requests)


def test_cloudwatch_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        cloudwatch={
            "enabled": True,
            "metric_namespace": "nats-sinks/metrics",
            "region": "eu-west-1",
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_cloudwatch_put_metric_data_requests_json(
            _large_snapshot(tmp_path / "metrics.json"),
            policy,
        )


def test_cloudwatch_export_uses_fake_client_and_reports_counts(tmp_path: Path) -> None:
    client = FakeCloudWatchClient()
    result = export_cloudwatch_metrics(
        _snapshot(tmp_path / "metrics.json"), _policy(), client=client
    )

    assert result.attempted is True
    assert result.delivered is True
    assert result.attempts == 1
    assert result.requests == 1
    assert result.metrics == 1
    assert result.message == "Amazon CloudWatch export delivered"
    assert client.calls[0]["Namespace"] == "nats-sinks/metrics"


def test_cloudwatch_export_retries_bounded_failures(tmp_path: Path) -> None:
    client = FakeCloudWatchClient(fail_times=1)
    sleeps: list[float] = []
    result = export_cloudwatch_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "nats-sinks/metrics",
                "region": "eu-west-1",
                "max_retries": 2,
                "retry_backoff_seconds": 0.1,
            }
        ),
        client=client,
        sleep=sleeps.append,
    )

    assert result.delivered is True
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert sleeps == [0.1]


def test_cloudwatch_export_failure_summary_is_sanitized(tmp_path: Path) -> None:
    client = FakeCloudWatchClient(fail_times=3)

    result = export_cloudwatch_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "nats-sinks/metrics",
                "region": "eu-west-1",
                "max_retries": 1,
            }
        ),
        client=client,
    )

    assert result.delivered is False
    assert result.attempts == 2
    assert "simulated" not in result.message
    assert "eu-west-1" not in result.message
    assert result.message == "Amazon CloudWatch export failed with TimeoutError"


def test_cloudwatch_policy_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="region"):
        _policy(cloudwatch={"enabled": True, "metric_namespace": "nats-sinks/metrics"})
    with pytest.raises(ValueError, match="AWS/"):
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "AWS/nats-sinks",
                "region": "eu-west-1",
            }
        )
    with pytest.raises(ValueError, match="sensitive"):
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "nats-sinks/metrics",
                "region": "eu-west-1",
                "dimensions": {"classification": "NATO_SECRET"},
            }
        )
    with pytest.raises(ValueError, match="too_long"):
        _policy(
            cloudwatch={
                "enabled": True,
                "metric_namespace": "nats-sinks/metrics",
                "region": "eu-west-1",
                "dimensions": {f"dimension{i}": "value" for i in range(11)},
            }
        )
