# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Azure Monitor custom metrics observability connector.

The connector is intentionally observational. It reads only local metrics
snapshots, applies the shared observability policy, and renders or sends Azure
custom metric requests without subjects, payloads, classification values,
mission metadata, destination details, resource IDs, locations, or bearer
tokens in dry-run output or result summaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib import error

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
from nats_sinks.observability.azure_monitor import (
    AZURE_MONITOR_PROFILE_NAME,
    DISABLED_AZURE_MONITOR_TEXT,
    EMPTY_AZURE_MONITOR_TEXT,
    azure_monitor_metrics_endpoint,
    build_azure_monitor_metric_documents,
    build_azure_monitor_metric_requests,
    export_azure_monitor_metrics,
    filter_azure_monitor_metric_rows,
    render_azure_monitor_metric_requests_json,
    resolve_azure_monitor_headers,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.subject_family import attach_labeled_metric_rows


class FakeResponse:
    """Small context-manager response used instead of a live Azure endpoint."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code


def _resource_id() -> str:
    return (
        "/subscriptions/00000000-0000-0000-0000-000000000000/"
        "resourceGroups/rg-observability/providers/Microsoft.Storage/storageAccounts/natssinks"
    )


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
        "azure_monitor": {
            "enabled": True,
            "resource_id": _resource_id(),
            "location": "westeurope",
            "token_env": "AZURE_MONITOR_BEARER_TOKEN",
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_azure_monitor_export_is_safe_noop(tmp_path: Path) -> None:
    result = export_azure_monitor_metrics(
        _snapshot(tmp_path / "metrics.json"),
        ObservabilityPolicy(),
    )

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_AZURE_MONITOR_TEXT.strip()


def test_azure_monitor_requests_contain_only_allowed_metrics(tmp_path: Path) -> None:
    requests = build_azure_monitor_metric_requests(_snapshot(tmp_path / "metrics.json"), _policy())
    rendered = json.dumps(requests, sort_keys=True)

    assert AZURE_MONITOR_PROFILE_NAME == "azure_monitor"
    assert requests == [
        {
            "time": requests[0]["time"],
            "data": {
                "baseData": {
                    "metric": "mission_ops_messages_fetched_total",
                    "namespace": "nats-sinks/metrics",
                    "dimNames": [],
                    "series": [
                        {
                            "dimValues": [],
                            "min": 12.0,
                            "max": 12.0,
                            "sum": 12.0,
                            "count": 1,
                        }
                    ],
                }
            },
        }
    ]
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered
    assert "westeurope" not in rendered
    assert _resource_id() not in rendered
    assert "AZURE_MONITOR_BEARER_TOKEN" not in rendered


def test_azure_monitor_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_azure_monitor_metrics(snapshot, policy)

    assert filter_azure_monitor_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_AZURE_MONITOR_TEXT.strip()


def test_azure_monitor_observations_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_azure_monitor_metric_rows(snapshot, without_observations) == []
    documents = build_azure_monitor_metric_documents(snapshot, with_observations)

    metric_names = {document["data"]["baseData"]["metric"] for document in documents}  # type: ignore[index]
    assert "mission_ops_sink_batch_write_seconds_count" in metric_names
    assert "mission_ops_sink_batch_write_seconds_last" in metric_names


def test_azure_monitor_static_dimensions_are_low_cardinality(tmp_path: Path) -> None:
    requests = build_azure_monitor_metric_requests(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "westeurope",
                "token_env": "AZURE_MONITOR_BEARER_TOKEN",
                "dimensions": {
                    "deployment": "edge",
                    "environment": "test",
                },
            }
        ),
    )

    base_data = requests[0]["data"]["baseData"]  # type: ignore[index]
    assert base_data["dimNames"] == ["deployment", "environment"]
    assert base_data["series"][0]["dimValues"] == ["edge", "test"]


def test_azure_monitor_prepared_labels_are_dimensions_only_when_explicitly_enabled(
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
        azure_monitor={
            "enabled": True,
            "resource_id": _resource_id(),
            "location": "westeurope",
            "token_env": "AZURE_MONITOR_BEARER_TOKEN",
            "include_metric_labels_as_dimensions": True,
        },
    )

    without_dimensions = build_azure_monitor_metric_documents(
        snapshot,
        policy_without_label_dimensions,
    )
    with_dimensions = build_azure_monitor_metric_documents(snapshot, policy_with_label_dimensions)

    assert len(without_dimensions) == 1
    assert len(with_dimensions) == 2
    labeled = next(
        document
        for document in with_dimensions
        if document["data"]["baseData"]["series"][0]["sum"] == 4.0  # type: ignore[index]
    )
    base_data = labeled["data"]["baseData"]  # type: ignore[index]
    assert base_data["dimNames"] == ["subject_family"]
    assert base_data["series"][0]["dimValues"] == ["sensor_track"]


def test_azure_monitor_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        azure_monitor={
            "enabled": True,
            "resource_id": _resource_id(),
            "location": "westeurope",
            "token_env": "AZURE_MONITOR_BEARER_TOKEN",
            "metric_namespace": "n" * 255,
            "dimensions": {f"dimension{i}": "v" * 255 for i in range(10)},
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_azure_monitor_metric_requests_json(
            _large_snapshot(tmp_path / "metrics.json"),
            policy,
        )


def test_azure_monitor_token_is_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_BEARER_TOKEN", "test-token")

    headers = resolve_azure_monitor_headers(_policy())

    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer test-token"


def test_azure_monitor_missing_token_environment_variable_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="AZURE_MONITOR_BEARER_TOKEN"):
        resolve_azure_monitor_headers(_policy())


def test_azure_monitor_endpoint_uses_location_and_resource_id() -> None:
    endpoint = azure_monitor_metrics_endpoint(_policy())

    assert endpoint == f"https://westeurope.monitoring.azure.com{_resource_id()}/metrics"


def test_azure_monitor_export_posts_requests_with_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_BEARER_TOKEN", "test-token")
    calls: list[tuple[str, float, bytes, str | None]] = []

    def fake_opener(req: object, *, timeout: float) -> FakeResponse:
        calls.append(
            (
                req.full_url,  # type: ignore[attr-defined]
                timeout,
                req.data,  # type: ignore[attr-defined]
                req.get_header("Authorization"),  # type: ignore[attr-defined]
            )
        )
        return FakeResponse(200)

    result = export_azure_monitor_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(azure_monitor={**_policy().azure_monitor.model_dump(), "timeout_seconds": 7}),
        opener=fake_opener,
    )

    assert result.delivered is True
    assert result.status_code == 200
    assert result.requests == 1
    assert result.metrics == 1
    assert result.message == "Azure Monitor export delivered"
    assert calls[0][0] == f"https://westeurope.monitoring.azure.com{_resource_id()}/metrics"
    assert calls[0][1] == 7
    assert b"mission_ops_messages_fetched_total" in calls[0][2]
    assert calls[0][3] == "Bearer test-token"


def test_azure_monitor_export_retries_bounded_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_BEARER_TOKEN", "test-token")
    sleeps: list[float] = []
    attempts = 0

    def fake_opener(_req: object, *, timeout: float) -> FakeResponse:
        nonlocal attempts
        assert timeout == 5.0
        attempts += 1
        raise error.URLError("azure unavailable")

    result = export_azure_monitor_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "westeurope",
                "token_env": "AZURE_MONITOR_BEARER_TOKEN",
                "max_retries": 2,
                "retry_backoff_seconds": 0.1,
            }
        ),
        opener=fake_opener,
        sleep=sleeps.append,
    )

    assert result.delivered is False
    assert result.attempts == 3
    assert attempts == 3
    assert sleeps == [0.1, 0.1]
    assert result.message == "Azure Monitor export failed with URLError"


def test_azure_monitor_export_failure_summary_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_BEARER_TOKEN", "test-token")

    def fake_opener(_req: object, *, timeout: float) -> FakeResponse:
        assert timeout == 5.0
        raise TimeoutError(f"simulated failure for {_resource_id()}")

    result = export_azure_monitor_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(azure_monitor={**_policy().azure_monitor.model_dump(), "max_retries": 1}),
        opener=fake_opener,
    )

    assert result.delivered is False
    assert result.attempts == 2
    assert "simulated" not in result.message
    assert "westeurope" not in result.message
    assert _resource_id() not in result.message
    assert result.message == "Azure Monitor export failed with TimeoutError"


def test_azure_monitor_policy_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="resource_id"):
        _policy(azure_monitor={"enabled": True, "location": "westeurope", "token_env": "TOKEN"})
    with pytest.raises(ValueError, match="concrete Azure resource"):
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000",
                "location": "westeurope",
                "token_env": "TOKEN",
            }
        )
    with pytest.raises(ValueError, match="location"):
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "https://westeurope.monitoring.azure.com",
                "token_env": "TOKEN",
            }
        )
    with pytest.raises(ValueError, match="token_env"):
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "westeurope",
                "token_env": "not valid",
            }
        )
    with pytest.raises(ValueError, match="sensitive"):
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "westeurope",
                "token_env": "TOKEN",
                "dimensions": {"classification": "NATO_SECRET"},
            }
        )
    with pytest.raises(ValueError, match="too_long"):
        _policy(
            azure_monitor={
                "enabled": True,
                "resource_id": _resource_id(),
                "location": "westeurope",
                "token_env": "TOKEN",
                "dimensions": {f"dimension{i}": "value" for i in range(11)},
            }
        )
