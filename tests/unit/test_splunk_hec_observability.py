# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Splunk HEC observability connector.

The connector is intentionally observational.  It reads only local metrics
snapshots, applies the shared observability policy, and renders a Splunk HEC
metric event without subjects, payloads, classification labels, mission
metadata, or destination details.
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
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.splunk_hec import (
    DISABLED_SPLUNK_HEC_TEXT,
    EMPTY_SPLUNK_HEC_TEXT,
    SPLUNK_HEC_PROFILE_NAME,
    build_splunk_hec_event,
    export_splunk_hec_metrics,
    filter_splunk_hec_metric_rows,
    render_splunk_hec_event_json,
    resolve_splunk_hec_headers,
)


class FakeResponse:
    """Small context-manager response used instead of a live HEC endpoint."""

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
        "splunk_hec": {
            "enabled": True,
            "endpoint": "https://splunk-hec.example.test/services/collector/event",
            "token_env": "SPLUNK_HEC_TOKEN",
        },
    }
    base.update(overrides)
    return ObservabilityPolicy.model_validate(base)


def test_disabled_splunk_hec_export_is_safe_noop(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")

    result = export_splunk_hec_metrics(snapshot, ObservabilityPolicy())

    assert result.attempted is False
    assert result.delivered is False
    assert result.message == DISABLED_SPLUNK_HEC_TEXT.strip()


def test_splunk_hec_event_contains_only_allowed_metrics(tmp_path: Path) -> None:
    event = build_splunk_hec_event(_snapshot(tmp_path / "metrics.json"), _policy())
    rendered = json.dumps(event, sort_keys=True)

    assert event["event"] == "metric"
    assert event["source"] == "nats-sinks"
    assert event["sourcetype"] == "nats_sinks:metrics"
    assert event["host"] == "nats-sinks"
    assert event["fields"]["nats_sinks_observability_profile"] == SPLUNK_HEC_PROFILE_NAME  # type: ignore[index]
    assert "metric_name:mission_ops_messages_fetched_total" in event["fields"]  # type: ignore[operator]
    assert "messages_acked_total" not in rendered
    assert "subject" not in rendered.lower()
    assert "message_id" not in rendered
    assert "classification" not in rendered
    assert "splunk-hec.example.test" not in rendered
    assert "SPLUNK_HEC_TOKEN" not in rendered


def test_splunk_hec_deny_list_wins_over_allow_list(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = _policy(denied_metrics=[MetricNames.MESSAGES_FETCHED_TOTAL])

    result = export_splunk_hec_metrics(snapshot, policy)

    assert filter_splunk_hec_metric_rows(snapshot, policy) == []
    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_SPLUNK_HEC_TEXT.strip()


def test_splunk_hec_observations_follow_shared_policy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    without_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=False,
    )
    with_observations = _policy(
        allowed_metrics=[MetricNames.SINK_BATCH_WRITE_SECONDS],
        include_observations=True,
    )

    assert filter_splunk_hec_metric_rows(snapshot, without_observations) == []
    event = build_splunk_hec_event(snapshot, with_observations)
    fields = event["fields"]

    assert "metric_name:mission_ops_sink_batch_write_seconds_count" in fields  # type: ignore[operator]
    assert "metric_name:mission_ops_sink_batch_write_seconds_last" in fields  # type: ignore[operator]


def test_splunk_hec_payload_size_limit_fails_closed(tmp_path: Path) -> None:
    policy = _policy(
        allowed_metric_patterns=["*"],
        include_observations=True,
        splunk_hec={
            "enabled": True,
            "endpoint": "https://splunk-hec.example.test/services/collector/event",
            "token_env": "SPLUNK_HEC_TOKEN",
            "max_request_bytes": 1024,
        },
    )

    with pytest.raises(ConfigurationError, match="max_request_bytes"):
        render_splunk_hec_event_json(_large_snapshot(tmp_path / "metrics.json"), policy)


def test_splunk_hec_token_and_headers_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLUNK_HEC_TOKEN", "test-token")
    monkeypatch.setenv("SPLUNK_HEC_CHANNEL", "test-channel")
    policy = _policy(
        splunk_hec={
            "enabled": True,
            "endpoint": "https://splunk-hec.example.test/services/collector/event",
            "token_env": "SPLUNK_HEC_TOKEN",
            "headers_env": {"X-Splunk-Request-Channel": "SPLUNK_HEC_CHANNEL"},
        }
    )

    headers = resolve_splunk_hec_headers(policy)

    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Splunk test-token"
    assert headers["X-Splunk-Request-Channel"] == "test-channel"


def test_splunk_hec_missing_token_environment_variable_fails_closed() -> None:
    policy = _policy()

    with pytest.raises(ConfigurationError, match="SPLUNK_HEC_TOKEN"):
        resolve_splunk_hec_headers(policy)


def test_splunk_hec_event_can_include_safe_index(tmp_path: Path) -> None:
    event = build_splunk_hec_event(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            splunk_hec={
                "enabled": True,
                "endpoint": "https://splunk-hec.example.test/services/collector/event",
                "token_env": "SPLUNK_HEC_TOKEN",
                "index": "metrics_secure",
            }
        ),
    )

    assert event["index"] == "metrics_secure"


def test_splunk_hec_export_posts_request_with_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLUNK_HEC_TOKEN", "test-token")
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

    result = export_splunk_hec_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            splunk_hec={
                "enabled": True,
                "endpoint": "https://splunk-hec.example.test/services/collector/event",
                "token_env": "SPLUNK_HEC_TOKEN",
                "timeout_seconds": 7,
            }
        ),
        opener=fake_opener,
    )

    assert result.delivered is True
    assert result.status_code == 200
    assert result.message == "Splunk HEC export delivered"
    assert calls[0][0] == "https://splunk-hec.example.test/services/collector/event"
    assert calls[0][1] == 7
    assert b"metric_name:mission_ops_messages_fetched_total" in calls[0][2]
    assert calls[0][3] == "Splunk test-token"


def test_splunk_hec_export_retries_bounded_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLUNK_HEC_TOKEN", "test-token")
    sleeps: list[float] = []
    attempts = 0

    def fake_opener(_req: object, *, timeout: float) -> FakeResponse:
        nonlocal attempts
        assert timeout == 5.0
        attempts += 1
        raise error.URLError("hec unavailable")

    result = export_splunk_hec_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(
            splunk_hec={
                "enabled": True,
                "endpoint": "https://splunk-hec.example.test/services/collector/event",
                "token_env": "SPLUNK_HEC_TOKEN",
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
    assert result.message == "Splunk HEC export failed with URLError"


def test_splunk_hec_empty_allow_list_is_safe_noop(tmp_path: Path) -> None:
    result = export_splunk_hec_metrics(
        _snapshot(tmp_path / "metrics.json"),
        _policy(allowed_metrics=[]),
    )

    assert result.attempted is False
    assert result.delivered is True
    assert result.message == EMPTY_SPLUNK_HEC_TEXT.strip()


def test_splunk_hec_policy_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="token_env"):
        ObservabilityPolicy(
            enabled=True,
            splunk_hec={
                "enabled": True,
                "endpoint": "https://splunk-hec.example.test/services/collector/event",
            },
        )

    with pytest.raises(ValueError, match="/services/collector/event"):
        ObservabilityPolicy(
            splunk_hec={
                "endpoint": "https://splunk-hec.example.test/services/collector/raw",
                "token_env": "SPLUNK_HEC_TOKEN",
            }
        )

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(
            splunk_hec={
                "endpoint": "http://splunk-hec.example.test/services/collector/event",
                "token_env": "SPLUNK_HEC_TOKEN",
            }
        )

    with pytest.raises(ValueError, match="Authorization"):
        ObservabilityPolicy(
            splunk_hec={
                "headers_env": {"Authorization": "SPLUNK_HEC_TOKEN"},
            }
        )

    with pytest.raises(ValueError, match="metadata values"):
        ObservabilityPolicy(splunk_hec={"sourcetype": "bad value"})

    with pytest.raises(ValueError, match="index"):
        ObservabilityPolicy(splunk_hec={"index": "bad.index"})

    with pytest.raises(ValueError, match="verify_tls"):
        ObservabilityPolicy(splunk_hec={"verify_tls": False})
