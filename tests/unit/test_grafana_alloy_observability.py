# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Grafana Alloy observability profile.

The profile is intentionally collector-oriented.  nats-sinks sends only
policy-approved OTLP metrics to an Alloy receiver; Alloy handles downstream
Grafana Cloud, Mimir, or LGTM forwarding.  These tests avoid a live Alloy
process while proving the profile validates configuration, generates safe River
snippets, keeps disabled sharing as a no-op, and exports through the shared
OTLP path.
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
)
from nats_sinks.observability.grafana_alloy import (
    DISABLED_GRAFANA_ALLOY_TEXT,
    EMPTY_GRAFANA_ALLOY_TEXT,
    GRAFANA_ALLOY_OTLP_SCOPE_NAME,
    build_grafana_alloy_otlp_metrics_document,
    export_grafana_alloy_metrics,
    filter_grafana_alloy_metric_rows,
    render_grafana_alloy_config,
    render_grafana_alloy_otlp_metrics_json,
    resolve_grafana_alloy_headers,
)
from nats_sinks.observability.policy import ObservabilityPolicy


class FakeResponse:
    """Small context-manager response used instead of a live Alloy receiver."""

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
        "grafana_alloy": {
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_grafana_alloy_export_is_safe_noop(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")

    result = export_grafana_alloy_metrics(snapshot, ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_GRAFANA_ALLOY_TEXT.strip()


def test_grafana_alloy_document_contains_only_allowed_metrics_and_static_profile(
    tmp_path: Path,
) -> None:
    document = build_grafana_alloy_otlp_metrics_document(
        _snapshot(tmp_path / "metrics.json"),
        _policy(),
    )
    rendered = json.dumps(document, sort_keys=True)

    assert "mission_ops_messages_fetched_total" in rendered
    assert "messages_acked_total" not in rendered
    assert GRAFANA_ALLOY_OTLP_SCOPE_NAME in rendered
    assert "nats_sinks.observability.profile" in rendered
    assert "grafana_alloy" in rendered
    assert "telemetry.collector" in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered


def test_grafana_alloy_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_grafana_alloy_metrics(snapshot, policy)

    assert filter_grafana_alloy_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_GRAFANA_ALLOY_TEXT.strip()


def test_grafana_alloy_observations_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_grafana_alloy_metric_rows(snapshot, without_observations) == []
    names = [row.name for row in filter_grafana_alloy_metric_rows(snapshot, with_observations)]

    assert "sink_batch_write_seconds.count" in names
    assert "sink_batch_write_seconds.last" in names


def test_grafana_alloy_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        grafana_alloy={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_grafana_alloy_otlp_metrics_json(_large_snapshot(tmp_path / "metrics.json"), policy)


def test_grafana_alloy_header_values_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_ALLOY_AUTH_HEADER", "Bearer local-test-token")
    policy = _policy(
        grafana_alloy={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "headers_env": {"Authorization": "LOCAL_ALLOY_AUTH_HEADER"},
        }
    )

    headers = resolve_grafana_alloy_headers(policy)

    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer local-test-token"


def test_grafana_alloy_missing_header_environment_variable_fails_closed() -> None:
    policy = _policy(
        grafana_alloy={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "headers_env": {"Authorization": "MISSING_LOCAL_ALLOY_AUTH_HEADER"},
        }
    )

    with pytest.raises(ConfigurationError, match="MISSING_LOCAL_ALLOY_AUTH_HEADER"):
        resolve_grafana_alloy_headers(policy)


def test_grafana_alloy_config_generation_uses_safe_env_references() -> None:
    policy = _policy(
        grafana_alloy={
            "enabled": True,
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "upstream_auth_mode": "basic",
            "upstream_auth_username_env": "GRAFANA_CLOUD_OTLP_USERNAME",
            "upstream_auth_password_env": "GRAFANA_CLOUD_OTLP_API_KEY",
        }
    )

    rendered = render_grafana_alloy_config(policy)

    assert 'otelcol.receiver.otlp "nats_sinks"' in rendered
    assert 'endpoint = "127.0.0.1:4318"' in rendered
    assert 'otelcol.processor.batch "nats_sinks_batch"' in rendered
    assert 'otelcol.exporter.otlphttp "grafana_cloud"' in rendered
    assert 'endpoint = sys.env("GRAFANA_CLOUD_OTLP_ENDPOINT")' in rendered
    assert 'username = sys.env("GRAFANA_CLOUD_OTLP_USERNAME")' in rendered
    assert 'password = sys.env("GRAFANA_CLOUD_OTLP_API_KEY")' in rendered
    assert "local-test-token" not in rendered


def test_grafana_alloy_export_posts_request_with_timeout(tmp_path: Path) -> None:
    calls: list[tuple[str, float, bytes]] = []

    def fake_opener(req: object, *, timeout: float) -> FakeResponse:
        calls.append((req.full_url, timeout, req.data))  # type: ignore[attr-defined]
        return FakeResponse(200)

    result = export_grafana_alloy_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            grafana_alloy={
                "enabled": True,
                "endpoint": "http://127.0.0.1:4318/v1/metrics",
                "timeout_seconds": 7,
            }
        ),
        opener=fake_opener,
    )

    assert result.delivered is True
    assert result.status_code == 200
    assert result.message == "Grafana Alloy export delivered"
    assert calls[0][0] == "http://127.0.0.1:4318/v1/metrics"
    assert calls[0][1] == 7
    assert b"grafana_alloy" in calls[0][2]


def test_grafana_alloy_export_retries_bounded_failures(tmp_path: Path) -> None:
    sleeps: list[float] = []
    attempts = 0

    def fake_opener(_req: object, *, timeout: float) -> FakeResponse:
        nonlocal attempts
        assert timeout == 5.0
        attempts += 1
        raise error.URLError("alloy unavailable")

    result = export_grafana_alloy_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            grafana_alloy={
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


def test_grafana_alloy_empty_allow_list_is_safe_noop(tmp_path: Path) -> None:
    result = export_grafana_alloy_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(allowed_metrics=[]),
    )

    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_GRAFANA_ALLOY_TEXT.strip()


def test_grafana_alloy_policy_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="endpoint is required"):
        ObservabilityPolicy(enabled=True, grafana_alloy={"enabled": True})

    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(
            grafana_alloy={"endpoint": "https://user:secret@example.test/v1/metrics"}
        )

    with pytest.raises(ValueError, match="/v1/metrics"):
        ObservabilityPolicy(grafana_alloy={"endpoint": "http://127.0.0.1:4318/other"})

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(grafana_alloy={"endpoint": "http://alloy.example.test/v1/metrics"})

    with pytest.raises(ValueError, match="component labels"):
        ObservabilityPolicy(grafana_alloy={"receiver_label": "bad-label"})

    with pytest.raises(ValueError, match="basic upstream auth"):
        ObservabilityPolicy(grafana_alloy={"upstream_auth_mode": "basic"})
