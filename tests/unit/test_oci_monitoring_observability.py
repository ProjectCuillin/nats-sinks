# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the OCI Monitoring observability connector.

The connector is intentionally observational. It reads only local metrics
snapshots, applies the shared observability policy, and renders or sends OCI
Monitoring `PostMetricData` requests without subjects, payloads,
classifications, mission metadata, destination details, tenancy OCIDs, regions,
or credentials in public dry-run output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

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
from nats_sinks.observability.oci_monitoring import (
    DISABLED_OCI_MONITORING_TEXT,
    EMPTY_OCI_MONITORING_TEXT,
    OCI_MONITORING_PROFILE_NAME,
    REDACTED_OCI_VALUE,
    build_oci_monitoring_metric_data,
    build_oci_monitoring_post_metric_data_requests,
    export_oci_monitoring_metrics,
    filter_oci_monitoring_metric_rows,
    render_oci_monitoring_post_metric_data_requests_json,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.subject_family import attach_labeled_metric_rows

TEST_COMPARTMENT_OCID = "ocid1.compartment.oc1..examplecompartment"


class FakeOciMonitoringClient:
    """Small fake OCI Monitoring client used instead of a live tenancy."""

    def __init__(self, *, fail_times: int = 0, rejected_metrics: bool = False) -> None:
        self.fail_times = fail_times
        self.rejected_metrics = rejected_metrics
        self.calls: list[object] = []

    def post_metric_data(self, post_metric_data_details: object, **kwargs: object) -> object:
        _ = kwargs
        self.calls.append(post_metric_data_details)
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("simulated OCI Monitoring timeout")
        if self.rejected_metrics:
            return {"data": {"failed_metrics": [{"name": "redacted"}]}}
        return {"data": {"failed_metrics": []}}


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
        "oci_monitoring": {
            "enabled": True,
            "metric_namespace": "nats_sinks_metrics",
            "region": "eu-frankfurt-1",
            "compartment_id": TEST_COMPARTMENT_OCID,
            "dimensions": {"deployment": "edge"},
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_oci_monitoring_export_is_safe_noop(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")

    result = export_oci_monitoring_metrics(snapshot, ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_OCI_MONITORING_TEXT.strip()


def test_oci_monitoring_requests_contain_only_allowed_metrics(tmp_path: Path) -> None:
    requests = build_oci_monitoring_post_metric_data_requests(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
    )
    rendered = json.dumps(requests, sort_keys=True)
    datapoints = cast(
        list[dict[str, object]],
        cast(dict[str, object], requests[0]["metric_data"][0])["datapoints"],
    )

    assert OCI_MONITORING_PROFILE_NAME == "oci_monitoring"
    assert requests == [
        {
            "batch_atomicity": "ATOMIC",
            "metric_data": [
                {
                    "namespace": "nats_sinks_metrics",
                    "compartment_id": TEST_COMPARTMENT_OCID,
                    "name": "mission_ops_messages_fetched_total",
                    "dimensions": {"deployment": "edge"},
                    "datapoints": [
                        {
                            "timestamp": datapoints[0]["timestamp"],
                            "value": 12.0,
                            "count": 1,
                        }
                    ],
                }
            ],
        }
    ]
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered


def test_oci_monitoring_dry_run_redacts_compartment_and_region(tmp_path: Path) -> None:
    rendered = render_oci_monitoring_post_metric_data_requests_json(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
    ).decode("utf-8")
    body = json.loads(rendered)

    assert body[0]["metric_data"][0]["compartment_id"] == REDACTED_OCI_VALUE
    assert TEST_COMPARTMENT_OCID not in rendered
    assert "eu-frankfurt-1" not in rendered


def test_oci_monitoring_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_oci_monitoring_metrics(snapshot, policy)

    assert filter_oci_monitoring_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_OCI_MONITORING_TEXT.strip()


def test_oci_monitoring_observations_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_oci_monitoring_metric_rows(snapshot, without_observations) == []
    data = build_oci_monitoring_metric_data(snapshot, with_observations)

    metric_names = {datum["name"] for datum in data}
    assert "mission_ops_sink_batch_write_seconds_count" in metric_names
    assert "mission_ops_sink_batch_write_seconds_last" in metric_names


def test_oci_monitoring_default_dimension_is_present(tmp_path: Path) -> None:
    data = build_oci_monitoring_metric_data(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
            }
        ),
    )

    assert data[0]["dimensions"] == {"source": "nats_sinks"}


def test_oci_monitoring_prepared_labels_are_dimensions_only_when_explicitly_enabled(
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
        oci_monitoring={
            "enabled": True,
            "metric_namespace": "nats_sinks_metrics",
            "region": "eu-frankfurt-1",
            "compartment_id": TEST_COMPARTMENT_OCID,
            "dimensions": {"deployment": "edge"},
            "include_metric_labels_as_dimensions": True,
        },
    )

    without_dimensions = build_oci_monitoring_metric_data(snapshot, policy_without_label_dimensions)
    with_dimensions = build_oci_monitoring_metric_data(snapshot, policy_with_label_dimensions)

    assert len(without_dimensions) == 1
    assert len(with_dimensions) == 2
    labeled = next(datum for datum in with_dimensions if datum["datapoints"][0]["value"] == 4.0)
    assert labeled["dimensions"] == {"deployment": "edge", "subject_family": "sensor_track"}


def test_oci_monitoring_request_batch_size_splits_requests(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(
        allowed_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL, MetricNames.MESSAGES_ACKED_TOTAL],
        oci_monitoring={
            "enabled": True,
            "metric_namespace": "nats_sinks_metrics",
            "region": "eu-frankfurt-1",
            "compartment_id": TEST_COMPARTMENT_OCID,
            "dimensions": {"deployment": "edge"},
            "max_metrics_per_request": 1,
        },
    )

    requests = build_oci_monitoring_post_metric_data_requests(snapshot, policy)

    assert len(requests) == 2
    assert all(len(request["metric_data"]) == 1 for request in requests)


def test_oci_monitoring_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        oci_monitoring={
            "enabled": True,
            "metric_namespace": "nats_sinks_metrics",
            "region": "eu-frankfurt-1",
            "compartment_id": TEST_COMPARTMENT_OCID,
            "dimensions": {"deployment": "edge"},
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_oci_monitoring_post_metric_data_requests_json(
            _large_snapshot(tmp_path / "metrics.json"),
            policy,
        )


def test_oci_monitoring_export_uses_fake_client_and_reports_counts(tmp_path: Path) -> None:
    client = FakeOciMonitoringClient()
    result = export_oci_monitoring_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
        client=client,
        request_model_factory=lambda request: request,
    )

    assert result.attempted is True
    assert result.delivered is True
    assert result.attempts == 1
    assert result.requests == 1
    assert result.metrics == 1
    assert result.message == "OCI Monitoring export delivered"
    first_call = cast(dict[str, object], client.calls[0])
    metric_data = cast(list[dict[str, object]], first_call["metric_data"])
    assert metric_data[0]["namespace"] == "nats_sinks_metrics"


def test_oci_monitoring_export_retries_bounded_failures(tmp_path: Path) -> None:
    client = FakeOciMonitoringClient(fail_times=1)
    sleeps: list[float] = []
    result = export_oci_monitoring_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
                "dimensions": {"deployment": "edge"},
                "max_retries": 2,
                "retry_backoff_seconds": 0.1,
            }
        ),
        client=client,
        request_model_factory=lambda request: request,
        sleep=sleeps.append,
    )

    assert result.delivered is True
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert sleeps == [0.1]


def test_oci_monitoring_export_failure_summary_is_sanitized(tmp_path: Path) -> None:
    client = FakeOciMonitoringClient(fail_times=3)

    result = export_oci_monitoring_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
                "dimensions": {"deployment": "edge"},
                "max_retries": 1,
            }
        ),
        client=client,
        request_model_factory=lambda request: request,
    )

    assert result.delivered is False
    assert result.attempts == 2
    assert "simulated" not in result.message
    assert "eu-frankfurt-1" not in result.message
    assert TEST_COMPARTMENT_OCID not in result.message
    assert result.message == "OCI Monitoring export failed with TimeoutError"


def test_oci_monitoring_export_rejected_metrics_are_failed_safely(tmp_path: Path) -> None:
    client = FakeOciMonitoringClient(rejected_metrics=True)

    result = export_oci_monitoring_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
        client=client,
        request_model_factory=lambda request: request,
    )

    assert result.delivered is False
    assert result.message == "OCI Monitoring export failed with OciMonitoringRejectedMetricError"


def test_oci_monitoring_policy_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="region"):
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "compartment_id": TEST_COMPARTMENT_OCID,
            }
        )
    with pytest.raises(ValueError, match="compartment_id"):
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
            }
        )
    with pytest.raises(ValueError, match="reserved"):
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "oci_forbidden",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
            }
        )
    with pytest.raises(ValueError, match="sensitive"):
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
                "dimensions": {"classification": "NATO_SECRET"},
            }
        )
    with pytest.raises(ValueError, match="at least one"):
        _policy(
            oci_monitoring={
                "enabled": True,
                "metric_namespace": "nats_sinks_metrics",
                "region": "eu-frankfurt-1",
                "compartment_id": TEST_COMPARTMENT_OCID,
                "dimensions": {},
            }
        )
