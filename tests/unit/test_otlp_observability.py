# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the OpenTelemetry OTLP observability connector.

The OTLP connector is tested without contacting a real collector.  Network
behavior is represented with small fake opener objects so unit tests stay
deterministic and never leak local infrastructure details.
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
    increment_metric,
    observe_metric,
    set_metric_value,
)
from nats_sinks.observability.otlp import (
    DISABLED_OTLP_TEXT,
    EMPTY_OTLP_TEXT,
    build_otlp_metrics_document,
    export_otlp_metrics,
    filter_otlp_metric_rows,
    render_otlp_metrics_json,
    resolve_otlp_headers,
)
from nats_sinks.observability.policy import ObservabilityPolicy


class FakeResponse:
    """Small context-manager response used instead of a live collector."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code


def _snapshot(path: Path) -> dict[str, object]:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 12)
    increment_metric(metrics, MetricNames.MESSAGES_ACKED_TOTAL, 11)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 4.0)
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
        "otlp": {
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_otlp_export_is_safe_noop(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")

    result = export_otlp_metrics(snapshot, ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_OTLP_TEXT.strip()


def test_otlp_document_contains_only_allowed_metrics(tmp_path: Path) -> None:
    document = build_otlp_metrics_document(_snapshot(tmp_path / "metrics.json"), _policy())
    rendered = json.dumps(document, sort_keys=True)

    assert "mission_ops_messages_fetched_total" in rendered
    assert "messages_acked_total" not in rendered
    assert "service.name" in rendered


def test_otlp_observations_are_policy_controlled(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_otlp_metric_rows(snapshot, without_observations) == []
    names = [row.name for row in filter_otlp_metric_rows(snapshot, with_observations)]

    assert "sink_batch_write_seconds.count" in names
    assert "sink_batch_write_seconds.last" in names


def test_otlp_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    snapshot = _large_snapshot(tmp_path / "metrics.json")
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        otlp={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_otlp_metrics_json(snapshot, policy)


def test_otlp_header_values_are_loaded_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTLP_AUTH_HEADER", "Bearer test-token")
    policy = _policy(
        otlp={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "headers_env": {"Authorization": "OTLP_AUTH_HEADER"},
        }
    )

    headers = resolve_otlp_headers(policy)

    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer test-token"


def test_otlp_missing_header_environment_variable_fails_closed() -> None:
    policy = _policy(
        otlp={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "headers_env": {"Authorization": "MISSING_OTLP_AUTH_HEADER"},
        }
    )

    with pytest.raises(ConfigurationError, match="MISSING_OTLP_AUTH_HEADER"):
        resolve_otlp_headers(policy)


def test_otlp_export_posts_request_with_timeout(tmp_path: Path) -> None:
    calls: list[tuple[str, float, bytes]] = []

    def fake_opener(req: object, *, timeout: float) -> FakeResponse:
        calls.append((req.full_url, timeout, req.data))  # type: ignore[attr-defined]
        return FakeResponse(200)

    result = export_otlp_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            otlp={
                "enabled": True,
                "endpoint": "http://127.0.0.1:4318/v1/metrics",
                "timeout_seconds": 7,
            }
        ),
        opener=fake_opener,
    )

    assert result.delivered is True
    assert result.status_code == 200
    assert calls[0][0] == "http://127.0.0.1:4318/v1/metrics"
    assert calls[0][1] == 7
    assert b"mission_ops_messages_fetched_total" in calls[0][2]


def test_otlp_export_retries_bounded_failures(tmp_path: Path) -> None:
    sleeps: list[float] = []
    attempts = 0

    def fake_opener(_req: object, *, timeout: float) -> FakeResponse:
        nonlocal attempts
        assert timeout == 5.0
        attempts += 1
        raise error.URLError("collector unavailable")

    result = export_otlp_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            otlp={
                "enabled": True,
                "endpoint": "http://127.0.0.1:4318/v1/metrics",
                "max_retries": 2,
                "retry_backoff_seconds": 0.01,
            }
        ),
        opener=fake_opener,
        sleep=sleeps.append,
    )

    assert attempts == 3
    assert sleeps == [0.01, 0.01]
    assert result.delivered is False
    assert result.message == "OTLP export failed with URLError"


def test_otlp_empty_allow_list_is_safe_noop(tmp_path: Path) -> None:
    result = export_otlp_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(allowed_metrics=[]),
    )

    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_OTLP_TEXT.strip()
