# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for observability sharing policies.

Observability policy is a security boundary.  The generated policy must stay
quiet by default, preserve discovered subject patterns for operator review, and
reject unknown metric names before any connector can publish them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nats_sinks.core.config import load_config
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.observability.policy import (
    ObservabilityPolicy,
    build_policy_from_app_config,
    load_observability_policy,
    observability_policy_template,
    subjects_from_app_config,
    write_observability_policy,
)


def _config_file(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "orders-file-sink",
                    "subject": "orders.*",
                },
                "metrics": {
                    "enabled": True,
                    "namespace": "mission_ops",
                    "snapshot_file": str(path.parent / "metrics.json"),
                },
                "message_metadata": {
                    "rules": [
                        {
                            "subject": "orders.urgent",
                            "priority": "immediate",
                        }
                    ]
                },
                "encryption": {
                    "enabled": False,
                    "rules": [
                        {
                            "subject": "orders.secret",
                            "enabled": False,
                        }
                    ],
                },
                "sink": {
                    "type": "file",
                    "directory": str(path.parent / "events"),
                    "mode": "one_file_per_message",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_generated_policy_is_disabled_and_copies_safe_subject_hints(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path / "config.json"))

    policy = build_policy_from_app_config(
        config,
        output_file="/var/lib/node_exporter/textfile_collector/nats_sinks.prom",
    )

    assert policy.enabled is False
    assert policy.prometheus.enabled is False
    assert policy.prometheus.http_endpoint.enabled is False
    assert policy.otlp.enabled is False
    assert policy.otlp.endpoint is None
    assert policy.prometheus.http_endpoint.host == "127.0.0.1"
    assert policy.prometheus.http_endpoint.path == "/metrics"
    assert policy.nats_server_monitoring.enabled is False
    assert policy.nats_server_monitoring.prometheus_enabled is False
    assert policy.nats_server_monitoring.allowed_endpoints == []
    assert policy.nats_server_monitoring.allowed_fields == []
    assert policy.allowed_metrics == []
    assert policy.allowed_metric_patterns == []
    assert policy.namespace == "mission_ops"
    assert [subject.subject for subject in policy.subjects] == [
        "orders.*",
        "orders.secret",
        "orders.urgent",
    ]


def test_subjects_from_config_preserves_order_without_duplicates(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path / "config.json"))

    assert subjects_from_app_config(config) == ["orders.*", "orders.secret", "orders.urgent"]


def test_policy_template_can_be_written_and_loaded(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path / "config.json"))
    template = observability_policy_template(config, output_file=str(tmp_path / "nats.prom"))
    policy_path = tmp_path / "observability.prometheus.json"

    write_observability_policy(template, policy_path)
    loaded = load_observability_policy(policy_path)

    assert loaded.schema_id == "nats_sinks.observability.policy.v1"
    assert loaded.prometheus.output_file == str(tmp_path / "nats.prom")


def test_policy_writer_refuses_to_overwrite_without_flag(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    write_observability_policy(ObservabilityPolicy(), policy_path)

    with pytest.raises(ConfigurationError, match="already exists"):
        write_observability_policy(ObservabilityPolicy(), policy_path)


def test_policy_rejects_unknown_metric_names() -> None:
    with pytest.raises(ValueError, match="unknown nats-sinks metric name"):
        ObservabilityPolicy(allowed_metrics=["not_a_metric"])


def test_policy_rejects_unsafe_native_http_endpoint_settings() -> None:
    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(prometheus={"http_endpoint": {"host": "127.0.0.1 /bad"}})

    with pytest.raises(ValueError, match="path"):
        ObservabilityPolicy(prometheus={"http_endpoint": {"path": "metrics"}})

    with pytest.raises(ValueError, match="port"):
        ObservabilityPolicy(prometheus={"http_endpoint": {"port": 70_000}})


def test_policy_rejects_unsafe_nats_monitoring_settings() -> None:
    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(nats_server_monitoring={"base_url": "https://user:secret@example.test"})

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(nats_server_monitoring={"base_url": "http://example.test"})

    with pytest.raises(ValueError, match="unsupported NATS monitoring endpoint"):
        ObservabilityPolicy(nats_server_monitoring={"allowed_endpoints": ["/unknown"]})

    with pytest.raises(ValueError, match="explicit dotted JSON field paths"):
        ObservabilityPolicy(nats_server_monitoring={"allowed_fields": ["jetstream.*"]})


def test_policy_rejects_unsafe_otlp_settings() -> None:
    with pytest.raises(ValueError, match="endpoint is required"):
        ObservabilityPolicy(enabled=True, otlp={"enabled": True})

    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(otlp={"endpoint": "https://user:secret@example.test/v1/metrics"})

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(otlp={"endpoint": "http://collector.example.test/v1/metrics"})

    with pytest.raises(ValueError, match="header names"):
        ObservabilityPolicy(otlp={"headers_env": {"Bad Header": "OTLP_TOKEN"}})

    with pytest.raises(ValueError, match="environment variable names"):
        ObservabilityPolicy(otlp={"headers_env": {"Authorization": "bad-env-name"}})
