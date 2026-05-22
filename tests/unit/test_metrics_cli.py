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
    observe_metric,
    set_metric_value,
)

runner = CliRunner()


def _snapshot(path: Path) -> Path:
    metrics = JsonFileMetrics(path, namespace="mission_ops")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 5)
    increment_metric(metrics, MetricNames.MESSAGES_PREPARED_TOTAL, 4)
    increment_metric(metrics, MetricNames.MESSAGES_ACKED_TOTAL, 4)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, 2)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL, 2)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.25)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.75)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 4.0)
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
    assert "MESSAGES_FETCHED_TOTAL" not in result.stdout


def test_metrics_cli_show_prometheus(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["show", str(path), "--format", "prometheus"])

    assert result.exit_code == 0
    assert "# TYPE mission_ops_messages_fetched_total counter" in result.stdout
    assert "mission_ops_sink_batch_write_seconds_count 2" in result.stdout


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
    assert "sink_batch_write_seconds" in result.stdout


def test_metrics_cli_rejects_stale_snapshot(tmp_path: Path) -> None:
    path = _snapshot(tmp_path / "metrics.json")

    result = runner.invoke(app, ["show", str(path), "--stale-after-seconds", "0"])

    assert result.exit_code == 3
    assert "stale" in result.stderr
