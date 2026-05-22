# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for small CLI behaviors that should not require network access."""

import json
from pathlib import Path

from typer.testing import CliRunner

from nats_sinks import InMemoryMetrics, MetricNames, SinkPluginConfig, __version__
from nats_sinks.cli.main import _attach_metrics_to_sink, _registry, app
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle import OracleSink


def test_cli_version_option_exits_before_requiring_command() -> None:
    """The advertised global `--version` flag should work without a subcommand."""

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_cli_validates_file_sink_config(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Configuration is valid." in result.output
    assert "Active sink: file" in result.output


def test_cli_file_sink_test_succeeds_without_network(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    output_dir = tmp_path / "events"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(output_dir),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["test-sink", str(config)])

    assert result.exit_code == 0
    assert "Sink test succeeded." in result.output
    assert output_dir.is_dir()


def test_cli_run_reports_missing_encryption_key_without_network(tmp_path: Path) -> None:
    config = tmp_path / "encrypted-file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "encryption": {
                    "enabled": True,
                    "algorithm": "aes-256-gcm",
                    "key_id": "missing-key-test",
                    "key_b64_env": "NATS_SINKS_TEST_MISSING_KEY_B64",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["run", str(config)])

    assert result.exit_code == 1
    assert "environment variable NATS_SINKS_TEST_MISSING_KEY_B64 is not set" in result.output


def test_cli_run_rejects_invalid_log_level_without_traceback(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["run", str(config), "--log-level", "TRACE"])

    assert result.exit_code == 2
    assert "logging.level must be one of" in result.output
    assert "Traceback" not in result.output


def test_cli_metrics_hook_attaches_oracle_sink_counters() -> None:
    metrics = InMemoryMetrics()
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
    )

    _attach_metrics_to_sink(sink, metrics)
    sink._record_duplicate_ignored(1)

    assert metrics.counters[MetricNames.ORACLE_DUPLICATES_TOTAL] == 1


def test_cli_registry_always_exposes_first_party_connectors() -> None:
    registry = _registry()

    assert registry.names() == ("file", "oracle")
    assert registry.connector("file").built_in is True
    assert registry.connector("oracle").production_ready is True


def test_cli_registry_rejects_missing_allow_listed_plugin() -> None:
    plugins = SinkPluginConfig(enabled=True, allowed_sinks=("missing",))

    try:
        _registry(plugins)
    except ConfigurationError as exc:
        assert "allowed sink connector(s) not installed: missing" in str(exc)
    else:  # pragma: no cover - defensive guard for a fail-closed path
        raise AssertionError("missing plugin connector should fail closed")


def test_cli_stream_plan_outputs_json_without_network(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "stream-plan",
            str(config),
            "--retention",
            "limits",
            "--discard",
            "old",
            "--storage",
            "file",
            "--replicas",
            "3",
            "--duplicate-window-seconds",
            "300",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["stream"] == "ORDERS"
    assert payload["recommended_stream_settings"]["replicas"] == 3
    assert payload["recommended_stream_settings"]["duplicate_window_seconds"] == 300
    assert "$JS.API.STREAM.CREATE.ORDERS" in payload["administration_permissions"]
    assert "nats stream add ORDERS" in payload["nats_cli_example"]


def test_cli_stream_plan_rejects_invalid_options_without_traceback(tmp_path: Path) -> None:
    config = tmp_path / "file-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["stream-plan", str(config), "--retention", "forever"])

    assert result.exit_code == 2
    assert "retention must be one of" in result.output
    assert "Traceback" not in result.output
