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
    evaluate_subject_observability_policy,
    load_observability_policy,
    observability_policy_template,
    subjects_from_app_config,
    write_observability_policy,
)
from nats_sinks.observability.prometheus import render_prometheus_textfile


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
    assert policy.elastic.enabled is False
    assert policy.elastic.endpoint is None
    assert policy.grafana_alloy.enabled is False
    assert policy.grafana_alloy.endpoint is None
    assert policy.splunk_hec.enabled is False
    assert policy.splunk_hec.endpoint is None
    assert policy.splunk_hec.token_env is None
    assert policy.statsd.enabled is False
    assert policy.statsd.transport == "udp"
    assert policy.statsd.host == "127.0.0.1"
    assert policy.statsd.port == 8125
    assert policy.datadog.enabled is False
    assert policy.datadog.transport == "udp"
    assert policy.datadog.host == "127.0.0.1"
    assert policy.datadog.port == 8125
    assert policy.datadog.include_metric_labels_as_tags is False
    assert policy.syslog.enabled is False
    assert policy.syslog.transport == "udp"
    assert policy.syslog.host == "127.0.0.1"
    assert policy.syslog.port == 514
    assert policy.syslog.hostname == "-"
    assert policy.prometheus.http_endpoint.host == "127.0.0.1"
    assert policy.prometheus.http_endpoint.path == "/metrics"
    assert policy.nats_server_monitoring.enabled is False
    assert policy.nats_server_monitoring.prometheus_enabled is False
    assert policy.nats_server_monitoring.allowed_endpoints == []
    assert policy.nats_server_monitoring.allowed_fields == []
    assert policy.subject_metrics.enabled is False
    assert policy.subject_metrics.default_action == "deny"
    assert policy.subject_metrics.rules == []
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


def test_subject_aware_policy_is_disabled_by_default() -> None:
    policy = ObservabilityPolicy()

    decision = evaluate_subject_observability_policy(
        policy,
        subject="orders.created",
        metric_name="messages_fetched_total",
    )

    assert policy.subject_metrics.enabled is False
    assert decision.allowed is False
    assert decision.reason == "disabled"


def test_subject_aware_policy_allows_reviewed_subject_family() -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [
                {
                    "subject": "orders.*",
                    "label": "orders",
                    "allowed_metrics": ["messages_fetched_total"],
                }
            ],
        }
    )

    decision = evaluate_subject_observability_policy(
        policy,
        subject="orders.created",
        metric_name="messages_fetched_total",
    )

    assert decision.allowed is True
    assert decision.label == "orders"
    assert decision.display_mode == "label"


def test_subject_aware_policy_deny_rules_override_allow_rules() -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [
                {"subject": "orders.*", "label": "orders"},
                {"subject": "orders.secret", "action": "deny"},
            ],
        }
    )

    decision = evaluate_subject_observability_policy(
        policy,
        subject="orders.secret",
        metric_name="messages_fetched_total",
    )

    assert decision.allowed is False
    assert decision.reason == "denied"


def test_subject_aware_policy_default_denies_unknown_subjects() -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders"}],
        }
    )

    decision = evaluate_subject_observability_policy(
        policy,
        subject="payments.created",
        metric_name="messages_fetched_total",
    )

    assert decision.allowed is False
    assert decision.reason == "no_match"


def test_subject_aware_policy_rejects_invalid_subject_patterns() -> None:
    with pytest.raises(ConfigurationError, match="wildcard"):
        ObservabilityPolicy(
            subject_metrics={
                "rules": [{"subject": "orders.>.bad", "label": "orders"}],
            }
        )


def test_subject_aware_policy_rejects_unsafe_labels() -> None:
    with pytest.raises(ValueError, match="label"):
        ObservabilityPolicy(
            subject_metrics={
                "rules": [{"subject": "orders.*", "label": "bad value"}],
            }
        )

    with pytest.raises(ValueError, match="secret or credential"):
        ObservabilityPolicy(
            subject_metrics={
                "rules": [{"subject": "orders.*", "label": "api_key"}],
            }
        )


def test_subject_aware_policy_cardinality_cap_is_enforced() -> None:
    with pytest.raises(ValueError, match="max_subject_families"):
        ObservabilityPolicy(
            subject_metrics={
                "enabled": True,
                "max_subject_families": 1,
                "rules": [
                    {"subject": "orders.*", "label": "orders"},
                    {"subject": "payments.*", "label": "payments"},
                ],
            }
        )


def test_subject_aware_policy_rejects_invalid_overflow_action() -> None:
    with pytest.raises(ValueError, match="overflow_action"):
        ObservabilityPolicy(subject_metrics={"overflow_action": "guess"})


def test_subject_aware_policy_supports_redacted_and_hash_modes() -> None:
    redacted = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders", "display_mode": "redacted"}],
        }
    )
    hashed = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders", "display_mode": "hash"}],
        }
    )

    assert (
        evaluate_subject_observability_policy(redacted, subject="orders.created").label
        == "redacted"
    )
    hash_label = evaluate_subject_observability_policy(hashed, subject="orders.created").label
    assert hash_label is not None
    assert hash_label.startswith("sha256_")


def test_subject_aware_policy_raw_mode_requires_explicit_review_flag() -> None:
    with pytest.raises(ValueError, match="allow_raw_subjects"):
        ObservabilityPolicy(
            subject_metrics={
                "enabled": True,
                "rules": [{"subject": "orders.*", "label": "orders", "display_mode": "raw"}],
            }
        )

    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "allow_raw_subjects": True,
            "rules": [{"subject": "orders.*", "label": "orders", "display_mode": "raw"}],
        }
    )

    decision = evaluate_subject_observability_policy(policy, subject="orders.created")

    assert decision.allowed is True
    assert decision.label == "orders.created"


def test_subject_aware_policy_invalid_runtime_subject_fails_closed() -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders"}],
        }
    )

    decision = evaluate_subject_observability_policy(policy, subject="bad subject")

    assert decision.allowed is False
    assert decision.reason == "invalid_subject"


def test_subject_aware_policy_does_not_change_aggregate_prometheus_output(
    tmp_path: Path,
) -> None:
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(
        json.dumps(
            {
                "schema": "nats_sinks.metrics.snapshot.v1",
                "generated_at_epoch_seconds": 1,
                "counters": {"messages_fetched_total": 7},
                "gauges": {},
                "observations": {},
            }
        ),
        encoding="utf-8",
    )
    snapshot = json.loads(metrics_file.read_text(encoding="utf-8"))
    policy = ObservabilityPolicy(
        enabled=True,
        allowed_metrics=["messages_fetched_total"],
        prometheus={"enabled": True},
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders"}],
        },
    )

    rendered = render_prometheus_textfile(snapshot, policy)

    assert 'subject="' not in rendered
    assert "orders" not in rendered
    assert "nats_sinks_messages_fetched_total 7" in rendered


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


def test_policy_rejects_unsafe_elastic_settings() -> None:
    with pytest.raises(ValueError, match="endpoint is required"):
        ObservabilityPolicy(enabled=True, elastic={"enabled": True})

    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(elastic={"endpoint": "https://user:secret@example.test/v1/metrics"})

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(elastic={"endpoint": "http://collector.example.test/v1/metrics"})

    with pytest.raises(ValueError, match="header names"):
        ObservabilityPolicy(elastic={"headers_env": {"Bad Header": "ELASTIC_TOKEN"}})

    with pytest.raises(ValueError, match="data_stream_namespace"):
        ObservabilityPolicy(elastic={"data_stream_namespace": "bad value"})


def test_policy_rejects_unsafe_grafana_alloy_settings() -> None:
    with pytest.raises(ValueError, match="endpoint is required"):
        ObservabilityPolicy(enabled=True, grafana_alloy={"enabled": True})

    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(
            grafana_alloy={"endpoint": "https://user:secret@example.test/v1/metrics"}
        )

    with pytest.raises(ValueError, match="/v1/metrics"):
        ObservabilityPolicy(grafana_alloy={"endpoint": "http://127.0.0.1:4318/other"})

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(grafana_alloy={"endpoint": "http://collector.example.test/v1/metrics"})

    with pytest.raises(ValueError, match="header names"):
        ObservabilityPolicy(grafana_alloy={"headers_env": {"Bad Header": "ALLOY_TOKEN"}})

    with pytest.raises(ValueError, match="component labels"):
        ObservabilityPolicy(grafana_alloy={"exporter_label": "bad-label"})

    with pytest.raises(ValueError, match="basic upstream auth"):
        ObservabilityPolicy(grafana_alloy={"upstream_auth_mode": "basic"})


def test_policy_rejects_unsafe_splunk_hec_settings() -> None:
    with pytest.raises(ValueError, match="endpoint is required"):
        ObservabilityPolicy(enabled=True, splunk_hec={"enabled": True, "token_env": "HEC_TOKEN"})

    with pytest.raises(ValueError, match="token_env is required"):
        ObservabilityPolicy(
            enabled=True,
            splunk_hec={
                "enabled": True,
                "endpoint": "https://splunk-hec.example.test/services/collector/event",
            },
        )

    with pytest.raises(ValueError, match="credentials"):
        ObservabilityPolicy(
            splunk_hec={
                "endpoint": "https://user:secret@example.test/services/collector/event",
                "token_env": "HEC_TOKEN",
            }
        )

    with pytest.raises(ValueError, match="plain http"):
        ObservabilityPolicy(
            splunk_hec={
                "endpoint": "http://splunk-hec.example.test/services/collector/event",
                "token_env": "HEC_TOKEN",
            }
        )

    with pytest.raises(ValueError, match="/services/collector/event"):
        ObservabilityPolicy(
            splunk_hec={
                "endpoint": "https://splunk-hec.example.test/services/collector/raw",
                "token_env": "HEC_TOKEN",
            }
        )

    with pytest.raises(ValueError, match="Authorization"):
        ObservabilityPolicy(splunk_hec={"headers_env": {"Authorization": "HEC_TOKEN"}})

    with pytest.raises(ValueError, match="metadata values"):
        ObservabilityPolicy(splunk_hec={"source": "bad value"})

    with pytest.raises(ValueError, match="index"):
        ObservabilityPolicy(splunk_hec={"index": "bad.index"})


def test_policy_rejects_unsafe_statsd_settings() -> None:
    with pytest.raises(ValueError, match="socket_path is required"):
        ObservabilityPolicy(enabled=True, statsd={"enabled": True, "transport": "unixgram"})

    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(statsd={"host": "127.0.0.1 /bad"})

    with pytest.raises(ValueError, match="socket_path"):
        ObservabilityPolicy(statsd={"socket_path": "bad\npath"})

    with pytest.raises(ValueError, match="metric_prefix"):
        ObservabilityPolicy(statsd={"metric_prefix": "bad prefix"})

    with pytest.raises(ValueError, match="max_datagram_bytes"):
        ObservabilityPolicy(statsd={"max_datagram_bytes": 70_000})


def test_policy_rejects_unsafe_syslog_settings() -> None:
    with pytest.raises(ValueError, match="socket_path is required"):
        ObservabilityPolicy(enabled=True, syslog={"enabled": True, "transport": "unixgram"})

    with pytest.raises(ValueError, match="host"):
        ObservabilityPolicy(syslog={"host": "127.0.0.1 /bad"})

    with pytest.raises(ValueError, match="socket_path"):
        ObservabilityPolicy(syslog={"socket_path": "bad\npath"})

    with pytest.raises(ValueError, match="app_name"):
        ObservabilityPolicy(syslog={"app_name": "bad value"})

    with pytest.raises(ValueError, match="structured_data_id"):
        ObservabilityPolicy(syslog={"structured_data_id": "bad value"})

    with pytest.raises(ValueError, match="max_message_bytes"):
        ObservabilityPolicy(syslog={"max_message_bytes": 70_000})
