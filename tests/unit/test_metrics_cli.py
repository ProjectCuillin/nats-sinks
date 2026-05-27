# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standalone metrics inspection CLI."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nats_sinks.cli.metrics import app
from nats_sinks.core.metrics import (
    JsonFileMetrics,
    MetricNames,
    increment_metric,
    metrics_snapshot,
    observe_metric,
    set_metric_value,
    write_metrics_snapshot,
)
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.subject_family import (
    aggregate_subject_family_counter,
    attach_labeled_metric_rows,
)
from nats_sinks.testing import certification_envelope

runner = CliRunner()


def _snapshot(path: Path) -> Path:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 5)
    increment_metric(metrics, MetricNames.MESSAGES_PREPARED_TOTAL, 4)
    increment_metric(metrics, MetricNames.MESSAGES_ACKED_TOTAL, 4)
    increment_metric(metrics, MetricNames.MESSAGES_TERMINATED_TOTAL, 1)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, 2)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL, 2)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL, 1)
    increment_metric(metrics, MetricNames.ORACLE_MERGE_ROWS_TOTAL, 3)
    increment_metric(metrics, MetricNames.IN_PROGRESS_ATTEMPTS_TOTAL, 3)
    increment_metric(metrics, MetricNames.IN_PROGRESS_SUCCESSES_TOTAL, 2)
    increment_metric(metrics, MetricNames.IN_PROGRESS_FAILURES_TOTAL, 1)
    increment_metric(metrics, MetricNames.IN_PROGRESS_MAX_HEARTBEATS_REACHED_TOTAL, 1)
    increment_metric(metrics, MetricNames.EVENTS_STALE_AT_RECEIVE_TOTAL, 1)
    increment_metric(metrics, MetricNames.FANOUT_MESSAGES_ROUTED_TOTAL, 1)
    increment_metric(metrics, MetricNames.FANOUT_CHILD_SINKS_SELECTED_TOTAL, 2)
    increment_metric(metrics, MetricNames.FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL, 1)
    increment_metric(metrics, MetricNames.FANOUT_MESSAGES_ACKED_TOTAL, 1)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.75)
    observe_metric(metrics, MetricNames.IN_PROGRESS_HEARTBEAT_SECONDS, 0.05)
    observe_metric(metrics, MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS, 42.0)
    observe_metric(metrics, MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS, 0.125)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 4.0)
    set_metric_value(metrics, MetricNames.CURRENT_IN_PROGRESS_BATCHES_ACTIVE, 1.0)
    set_metric_value(metrics, MetricNames.CURRENT_FANOUT_CHILD_SINKS_SELECTED, 2.0)
    return path


def test_metrics_cli_show_table(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["show", str(path)])

    assert result.exit_code == 0
    assert "messages_fetched_total" in result.stdout
    assert "sink_batch_write_seconds.count" in result.stdout
    assert "messages_received_total" not in result.stdout


def test_metrics_cli_show_shell_and_include_legacy(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        [
            "show",
            str(path),
            "--format",
            "shell",
            "--metric",
            "messages_*",
            "--include-legacy",
        ],
    )

    assert result.exit_code == 0
    assert "MESSAGES_FETCHED_TOTAL=5" in result.stdout
    assert "MESSAGES_TERMINATED_TOTAL=1" in result.stdout
    assert "MESSAGES_RECEIVED_TOTAL=4" in result.stdout


def test_metrics_cli_show_jsonl_filter(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        ["show", str(path), "--format", "jsonl", "--kind", "counter", "--metric", "*acked*"],
    )

    assert result.exit_code == 0
    assert '"name": "messages_acked_total"' in result.stdout
    assert "sink_batch_write_seconds" not in result.stdout


def test_metrics_cli_filters_oracle_duplicate_metrics(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        ["show", str(path), "--format", "shell", "--metric", "oracle_*"],
    )

    assert result.exit_code == 0
    assert "ORACLE_DUPLICATES_TOTAL=2" in result.stdout
    assert "ORACLE_DUPLICATE_IGNORED_TOTAL=2" in result.stdout
    assert "ORACLE_DUPLICATE_NOOP_TOTAL=1" in result.stdout
    assert "ORACLE_MERGE_ROWS_TOTAL=3" in result.stdout
    assert "MESSAGES_FETCHED_TOTAL" not in result.stdout


def test_metrics_cli_filters_fanout_metrics(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        ["show", str(path), "--format", "shell", "--metric", "fanout_*"],
    )

    assert result.exit_code == 0
    assert "FANOUT_MESSAGES_ROUTED_TOTAL=1" in result.stdout
    assert "FANOUT_CHILD_SINKS_SELECTED_TOTAL=2" in result.stdout
    assert "FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL=1" in result.stdout
    assert "FANOUT_ACK_GATE_WAIT_SECONDS_COUNT=1" in result.stdout
    assert "MESSAGES_FETCHED_TOTAL" not in result.stdout


def test_metrics_cli_filters_in_progress_metrics(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        [
            "show",
            str(path),
            "--format",
            "shell",
            "--metric",
            "in_progress_*",
            "--metric",
            "current_in_progress_*",
        ],
    )

    assert result.exit_code == 0
    assert "IN_PROGRESS_ATTEMPTS_TOTAL=3" in result.stdout
    assert "IN_PROGRESS_SUCCESSES_TOTAL=2" in result.stdout
    assert "IN_PROGRESS_FAILURES_TOTAL=1" in result.stdout
    assert "IN_PROGRESS_MAX_HEARTBEATS_REACHED_TOTAL=1" in result.stdout
    assert "IN_PROGRESS_HEARTBEAT_SECONDS_COUNT=1" in result.stdout
    assert "CURRENT_IN_PROGRESS_BATCHES_ACTIVE=1" in result.stdout
    assert "MESSAGES_ACKED_TOTAL" not in result.stdout


def test_metrics_cli_filters_terminal_ack_metrics(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        ["show", str(path), "--format", "shell", "--metric", "*term*"],
    )

    assert result.exit_code == 0
    assert "MESSAGES_TERMINATED_TOTAL=1" in result.stdout


def test_metrics_cli_show_prometheus(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["show", str(path), "--format", "prometheus"])

    assert result.exit_code == 0
    assert "# TYPE mission_ops_messages_fetched_total counter" in result.stdout
    assert "mission_ops_sink_batch_write_seconds_count 2" in result.stdout
    assert "mission_ops_in_progress_heartbeat_seconds_count 1" in result.stdout
    assert "mission_ops_event_age_at_receive_seconds_count 1" in result.stdout
    assert "mission_ops_fanout_ack_gate_wait_seconds_count 1" in result.stdout


def test_metrics_cli_shows_prepared_subject_family_rows(tmp_path: Path) -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "rules": [{"subject": "orders.*", "label": "orders"}],
        }
    )
    result_rows = aggregate_subject_family_counter(
        (
            certification_envelope(subject="orders.created"),
            certification_envelope(subject="orders.updated"),
        ),
        policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    snapshot = attach_labeled_metric_rows(
        metrics_snapshot(
            counters={MetricNames.MESSAGES_WRITTEN_TOTAL: 2},
            gauges={},
            observations={},
            namespace="mission_ops",
        ),
        result_rows.rows,
    )
    path = tmp_path / "metrics.json"
    write_metrics_snapshot(snapshot, path)

    table = runner.invoke(app, ["show", str(path), "--metric", "messages_written_total"])
    assert table.exit_code == 0
    assert "subject_family=orders" in table.stdout

    prometheus = runner.invoke(
        app,
        ["show", str(path), "--format", "prometheus", "--metric", "messages_written_total"],
    )
    assert prometheus.exit_code == 0
    assert 'mission_ops_messages_written_total{subject_family="orders"} 2' in prometheus.stdout
    assert "orders.created" not in prometheus.stdout


def test_metrics_cli_filters_freshness_metrics(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(
        app,
        [
            "show",
            str(path),
            "--format",
            "shell",
            "--metric",
            "event_*",
            "--metric",
            "events_*",
        ],
    )

    assert result.exit_code == 0
    assert "EVENTS_STALE_AT_RECEIVE_TOTAL=1" in result.stdout
    assert "EVENT_AGE_AT_RECEIVE_SECONDS_COUNT=1" in result.stdout
    assert "MESSAGES_FETCHED_TOTAL" not in result.stdout


def test_metrics_cli_get_value(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["get", str(path), "messages_fetched_total"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "5"


def test_metrics_cli_get_missing_default(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["get", str(path), "does_not_exist", "--default", "0"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0"


def test_metrics_cli_describe_names() -> None:
    result = runner.invoke(app, ["describe", "--format", "names"])

    assert result.exit_code == 0
    assert "messages_fetched_total" in result.stdout
    assert "oracle_duplicates_total" in result.stdout
    assert "event_age_at_receive_seconds" in result.stdout
    assert "in_progress_attempts_total" in result.stdout
    assert "current_in_progress_batches_active" in result.stdout
    assert "fanout_messages_routed_total" in result.stdout
    assert "fanout_ack_gate_wait_seconds" in result.stdout
    assert "sink_batch_write_seconds" in result.stdout


def test_metrics_cli_rejects_stale_snapshot(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["show", str(path), "--stale-after-seconds", "0"])

    assert result.exit_code == 3
    assert "stale" in result.stderr
