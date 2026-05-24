# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the observability management CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.observability import app
from nats_sinks.core.metrics import JsonFileMetrics, MetricNames, increment_metric
from nats_sinks.observability import ObservabilityPolicy

runner = CliRunner()


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


def _snapshot(path: Path) -> Path:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 7)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, 1)
    return path


def test_init_prometheus_policy_generates_disabled_policy(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"

    result = runner.invoke(
        app,
        [
            "init-prometheus-policy",
            str(config),
            str(policy),
            "--output-file",
            str(tmp_path / "nats_sinks.prom"),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(policy.read_text(encoding="utf-8"))
    assert data["enabled"] is False
    assert data["prometheus"]["enabled"] is False
    assert data["subjects"][0]["subject"] == "orders.*"


def test_validate_and_show_effective_policy(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    runner.invoke(app, ["init-prometheus-policy", str(config), str(policy)])

    validate = runner.invoke(app, ["validate-policy", str(policy)])
    show = runner.invoke(app, ["show-effective-policy", str(policy), "--format", "summary"])

    assert validate.exit_code == 0
    assert "Observability policy is valid." in validate.stdout
    assert show.exit_code == 0
    assert "prometheus_enabled=false" in show.stdout


def test_list_metrics_and_subjects_are_script_friendly(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    runner.invoke(app, ["init-prometheus-policy", str(config), str(policy)])

    metrics = runner.invoke(app, ["list-metrics", "--format", "names"])
    subjects = runner.invoke(app, ["list-subjects", str(policy), "--format", "shell"])

    assert metrics.exit_code == 0
    assert "messages_fetched_total" in metrics.stdout
    assert subjects.exit_code == 0
    assert "NATS_SINKS_SUBJECT_1_ORDERS=orders.*" in subjects.stdout


def test_prometheus_textfile_disabled_policy_does_not_need_snapshot(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    output = tmp_path / "nats_sinks.prom"
    runner.invoke(
        app,
        [
            "init-prometheus-policy",
            str(config),
            str(policy),
            "--output-file",
            str(output),
        ],
    )

    result = runner.invoke(
        app,
        ["prometheus-textfile", str(tmp_path / "missing.json"), str(policy), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "disabled by observability policy" in result.stdout


def test_prometheus_textfile_enabled_policy_writes_allowed_metrics(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = tmp_path / "observability.prometheus.json"
    output = tmp_path / "nats_sinks.prom"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
                "allowed_metric_patterns": [],
                "denied_metrics": [],
                "denied_metric_patterns": [],
                "include_observations": False,
                "include_legacy": False,
                "subjects": [],
                "prometheus": {
                    "enabled": True,
                    "output_file": str(output),
                    "include_help": True,
                    "include_type": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["prometheus-textfile", str(snapshot), str(policy)])

    assert result.exit_code == 0
    assert output.exists()
    rendered = output.read_text(encoding="utf-8")
    assert "mission_ops_messages_fetched_total 7" in rendered
    assert "oracle_duplicates_total" not in rendered


def test_prometheus_http_disabled_policy_refuses_to_start(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    runner.invoke(app, ["init-prometheus-policy", str(config), str(policy)])

    result = runner.invoke(app, ["prometheus-http", str(tmp_path / "missing.json"), str(policy)])

    assert result.exit_code == 2
    assert "disabled by observability policy" in result.stderr


def test_prometheus_http_dry_run_outputs_allowed_metrics(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = tmp_path / "observability.prometheus.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
                "allowed_metric_patterns": [],
                "denied_metrics": [],
                "denied_metric_patterns": [],
                "include_observations": False,
                "include_legacy": False,
                "subjects": [],
                "prometheus": {
                    "enabled": False,
                    "include_help": True,
                    "include_type": True,
                    "http_endpoint": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": 9108,
                        "path": "/metrics",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["prometheus-http", str(snapshot), str(policy), "--dry-run"])

    assert result.exit_code == 0
    assert "mission_ops_messages_fetched_total 7" in result.stdout
    assert "oracle_duplicates_total" not in result.stdout


def test_prometheus_http_startup_uses_policy_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = tmp_path / "observability.prometheus.json"
    calls: list[tuple[str, str, int]] = []
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
                "allowed_metric_patterns": [],
                "denied_metrics": [],
                "denied_metric_patterns": [],
                "include_observations": False,
                "include_legacy": False,
                "subjects": [],
                "prometheus": {
                    "enabled": False,
                    "http_endpoint": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": 9200,
                        "path": "/mission-metrics",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_serve(
        snapshot_file: Path,
        loaded_policy: ObservabilityPolicy,
        *,
        allow_stale: bool,
    ) -> None:
        endpoint = loaded_policy.prometheus.http_endpoint
        calls.append((str(snapshot_file), endpoint.path, endpoint.port))
        assert allow_stale is False

    monkeypatch.setattr("nats_sinks.cli.observability.serve_prometheus_http", fake_serve)

    result = runner.invoke(app, ["prometheus-http", str(snapshot), str(policy)])

    assert result.exit_code == 0
    assert calls == [(str(snapshot), "/mission-metrics", 9200)]
    assert "Serving Prometheus metrics on 127.0.0.1:9200/mission-metrics" in result.stdout


def test_otlp_export_disabled_policy_does_not_need_snapshot(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    runner.invoke(app, ["init-prometheus-policy", str(config), str(policy)])

    result = runner.invoke(app, ["otlp-export", str(tmp_path / "missing.json"), str(policy)])

    assert result.exit_code == 0
    assert "disabled by observability policy" in result.stdout


def test_otlp_export_dry_run_outputs_policy_filtered_json(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = tmp_path / "observability.prometheus.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
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
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["otlp-export", str(snapshot), str(policy), "--dry-run"])

    assert result.exit_code == 0
    assert "mission_ops_messages_fetched_total" in result.stdout
    assert "oracle_duplicates_total" not in result.stdout


def test_elastic_export_disabled_policy_does_not_need_snapshot(tmp_path: Path) -> None:
    config = _config_file(tmp_path / "config.json")
    policy = tmp_path / "observability.prometheus.json"
    runner.invoke(app, ["init-prometheus-policy", str(config), str(policy)])

    result = runner.invoke(app, ["elastic-export", str(tmp_path / "missing.json"), str(policy)])

    assert result.exit_code == 0
    assert "disabled by observability policy" in result.stdout


def test_elastic_export_dry_run_outputs_profiled_otlp_json(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "metrics.json")
    policy = tmp_path / "observability.prometheus.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
                "allowed_metric_patterns": [],
                "denied_metrics": [],
                "denied_metric_patterns": [],
                "include_observations": False,
                "include_legacy": False,
                "subjects": [],
                "elastic": {
                    "enabled": True,
                    "endpoint": "http://127.0.0.1:4318/v1/metrics",
                    "data_stream_dataset": "nats_sinks.metrics",
                    "data_stream_namespace": "default",
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["elastic-export", str(snapshot), str(policy), "--dry-run"])

    assert result.exit_code == 0
    assert "mission_ops_messages_fetched_total" in result.stdout
    assert "oracle_duplicates_total" not in result.stdout
    assert "data_stream.dataset" in result.stdout
    assert "nats-sinks.observability.elastic" in result.stdout


def test_elastic_export_rejects_stale_snapshot_without_override(tmp_path: Path) -> None:
    snapshot = tmp_path / "metrics.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.metrics.snapshot.v1",
                "namespace": "mission_ops",
                "generated_at_epoch_seconds": 1.0,
                "counters": {"messages_fetched_total": 7},
                "gauges": {},
                "observations": {},
            }
        ),
        encoding="utf-8",
    )
    policy = tmp_path / "observability.prometheus.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "allowed_metrics": ["messages_fetched_total"],
                "allowed_metric_patterns": [],
                "denied_metrics": [],
                "denied_metric_patterns": [],
                "include_observations": False,
                "include_legacy": False,
                "subjects": [],
                "elastic": {
                    "enabled": True,
                    "endpoint": "http://127.0.0.1:4318/v1/metrics",
                    "stale_after_seconds": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["elastic-export", str(snapshot), str(policy), "--dry-run"])

    assert result.exit_code == 3
    assert "Metrics snapshot is stale" in result.stderr
    assert "127.0.0.1" not in result.stderr


def test_nats_monitoring_poll_dry_run_outputs_sanitized_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = tmp_path / "observability.prometheus.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "subjects": [],
                "prometheus": {"enabled": False},
                "nats_server_monitoring": {
                    "enabled": True,
                    "base_url": "https://nats-monitoring.example.test",
                    "allowed_endpoints": ["/jsz"],
                    "allowed_fields": ["jetstream.stats.messages"],
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_collect(loaded_policy: ObservabilityPolicy) -> dict[str, object]:
        assert loaded_policy.nats_server_monitoring.allowed_endpoints == ["/jsz"]
        return {
            "schema": "nats_sinks.observability.nats_monitoring.snapshot.v1",
            "generated_at_epoch_seconds": 1_797_820_000.0,
            "endpoints": [
                {
                    "endpoint": "/jsz",
                    "status_code": 200,
                    "fields": {"jetstream.stats.messages": 17},
                }
            ],
        }

    monkeypatch.setattr(
        "nats_sinks.cli.observability.collect_nats_monitoring_snapshot",
        fake_collect,
    )

    result = runner.invoke(app, ["nats-monitoring-poll", str(policy), "--dry-run"])

    assert result.exit_code == 0
    assert "nats_sinks.observability.nats_monitoring.snapshot.v1" in result.stdout
    assert "nats-monitoring.example.test" not in result.stdout
    assert "jetstream.stats.messages" in result.stdout


def test_nats_monitoring_prometheus_writes_policy_filtered_text(tmp_path: Path) -> None:
    snapshot = tmp_path / "nats-monitoring.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.nats_monitoring.snapshot.v1",
                "generated_at_epoch_seconds": 1_797_820_000.0,
                "endpoints": [
                    {
                        "endpoint": "/jsz",
                        "status_code": 200,
                        "fields": {
                            "server_id": "server-a",
                            "jetstream.stats.messages": 17,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    policy = tmp_path / "observability.prometheus.json"
    output = tmp_path / "nats-monitoring.prom"
    policy.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.observability.policy.v1",
                "enabled": True,
                "namespace": "mission_ops",
                "subjects": [],
                "prometheus": {"enabled": False},
                "nats_server_monitoring": {
                    "enabled": True,
                    "base_url": "https://nats-monitoring.example.test",
                    "allowed_endpoints": ["/jsz"],
                    "allowed_fields": ["server_id", "jetstream.stats.messages"],
                    "prometheus_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["nats-monitoring-prometheus", str(snapshot), str(policy), "--output", str(output)],
    )

    assert result.exit_code == 0
    rendered = output.read_text(encoding="utf-8")
    assert "mission_ops_nats_monitoring_jsz_jetstream_stats_messages 17" in rendered
    assert "server-a" not in rendered
