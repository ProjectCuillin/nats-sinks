# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for small CLI behaviors that should not require network access."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import nats_sinks.cli.main as cli_main
from nats_sinks import InMemoryMetrics, MetricNames, SinkPluginConfig, __version__
from nats_sinks.cli.main import _attach_metrics_to_sink, _registry, app
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.mysql import MySqlSink
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


def test_cli_validates_http_sink_config(tmp_path: Path) -> None:
    config = tmp_path / "http-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "http-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "http",
                    "url": "https://events.example.invalid/nats-sink",
                    "endpoint_allowed_hosts": ["events.example.invalid"],
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Configuration is valid." in result.output
    assert "Active sink: http" in result.output


def test_cli_validates_s3_sink_config(tmp_path: Path) -> None:
    config = tmp_path / "s3-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "s3-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "s3",
                    "bucket": "nats-sinks-events",
                    "prefix": "orders/archive",
                    "key_strategy": "stream_sequence",
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Configuration is valid." in result.output
    assert "Active sink: s3" in result.output


def test_cli_validates_routing_match_policy_example() -> None:
    config = Path(__file__).resolve().parents[2] / "examples/routing-match-policy/config.json"

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


def test_cli_metrics_hook_attaches_mysql_sink_counters() -> None:
    metrics = InMemoryMetrics()
    sink = MySqlSink(
        host="127.0.0.1",
        database="nats_sinks_test",
        user="app_user",
        password="example",  # noqa: S106 - local test placeholder
        table="NATS_SINK_EVENTS",
        mode="insert_ignore",
    )

    _attach_metrics_to_sink(sink, metrics)
    sink._record_duplicate_ignored(1)

    assert metrics.counters[MetricNames.MYSQL_DUPLICATES_TOTAL] == 1


def test_cli_registry_always_exposes_first_party_connectors() -> None:
    registry = _registry()

    assert registry.names() == (
        "coherence",
        "file",
        "foundry",
        "gotham",
        "http",
        "mysql",
        "oracle",
        "oracle_nosql",
        "s3",
        "spool",
    )
    assert registry.connector("coherence").requires_extra == "coherence"
    assert registry.connector("coherence").production_ready is False
    assert registry.connector("file").built_in is True
    assert registry.connector("foundry").production_ready is False
    assert registry.connector("gotham").production_ready is False
    assert registry.connector("http").documentation == "docs/http-sink.md"
    assert registry.connector("http").production_ready is True
    assert registry.connector("mysql").requires_extra == "mysql"
    assert registry.connector("oracle_nosql").requires_extra == "oracle-nosql"
    assert registry.connector("oracle_nosql").production_ready is False
    assert registry.connector("oracle").production_ready is True
    assert registry.connector("s3").requires_extra == "s3"
    assert registry.connector("s3").documentation == "docs/s3-sink.md"
    assert registry.connector("spool").requires_extra == "crypto"


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


class FakeInspectionSequence:
    stream = 7
    consumer = 1


class FakeInspectionMetadata:
    stream = "ORDERS"
    consumer = "_ordered"
    num_delivered = 1
    num_pending = 0
    timestamp = None
    sequence = FakeInspectionSequence()


class FakeInspectionMessage:
    subject = "orders.created"
    data = b'{"order_id":"A-100"}'
    reply = None
    metadata = FakeInspectionMetadata()
    headers: dict[str, str]

    def __init__(self) -> None:
        self.headers = {"Authorization": "Bearer example", "X-Unit": "visible"}

    async def ack(self) -> None:
        raise AssertionError("inspection CLI must not ACK messages")


class FakeInspectionSubscription:
    def __init__(self) -> None:
        self.messages: list[FakeInspectionMessage] = [FakeInspectionMessage()]
        self.unsubscribed = False

    async def next_msg(self, **kwargs: float) -> FakeInspectionMessage:
        _ = kwargs["timeout"]
        if not self.messages:
            raise TimeoutError
        return self.messages.pop(0)

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeInspectionJetStream:
    def __init__(self) -> None:
        self.subscription = FakeInspectionSubscription()

    async def subscribe(
        self,
        subject: str,
        *,
        stream: str,
        ordered_consumer: bool,
        manual_ack: bool,
        idle_heartbeat: float | None,
        pending_msgs_limit: int,
        pending_bytes_limit: int,
    ) -> FakeInspectionSubscription:
        assert subject == "orders.created"
        assert stream == "ORDERS"
        assert ordered_consumer is True
        assert manual_ack is False
        assert idle_heartbeat is None
        assert pending_msgs_limit > 0
        assert pending_bytes_limit > 0
        return self.subscription


class FakeInspectionConnection:
    def __init__(self) -> None:
        self.js = FakeInspectionJetStream()
        self.closed = False

    def jetstream(self) -> FakeInspectionJetStream:
        return self.js

    async def close(self) -> None:
        self.closed = True


def _ordered_inspection_config(path: Path, event_dir: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.created",
                },
                "sink": {
                    "type": "file",
                    "directory": str(event_dir),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )


def test_cli_inspect_ordered_outputs_redacted_jsonl_without_building_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "file-config.json"
    _ordered_inspection_config(config, tmp_path / "events")
    connection = FakeInspectionConnection()

    async def fake_connect(options: dict[str, Any]) -> FakeInspectionConnection:
        assert options["servers"] == ["nats://localhost:4222"]
        return connection

    def fail_if_sink_is_built(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        raise AssertionError("inspection command must not build a sink")

    monkeypatch.setattr(cli_main, "_connect_nats_for_inspection", fake_connect)
    monkeypatch.setattr(cli_main, "_build_sink", fail_if_sink_is_built)
    monkeypatch.setattr(cli_main, "_build_sink_from_raw", fail_if_sink_is_built)

    result = CliRunner().invoke(app, ["inspect-ordered", str(config), "--format", "jsonl"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["inspection_only"] is True
    assert parsed["stream_sequence"] == 7
    assert parsed["headers"]["Authorization"] == "<redacted>"
    assert parsed["payload"]["redacted"] is True
    assert "data" not in parsed["payload"]
    assert connection.closed is True
    assert connection.js.subscription.unsubscribed is True


def test_cli_inspect_ordered_validates_limits_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "file-config.json"
    _ordered_inspection_config(config, tmp_path / "events")

    async def fail_if_connects(options: dict[str, Any]) -> None:
        _ = options
        raise AssertionError("invalid inspection limits should fail before connecting")

    monkeypatch.setattr(cli_main, "_connect_nats_for_inspection", fail_if_connects)

    result = CliRunner().invoke(app, ["inspect-ordered", str(config), "--max-messages", "0"])

    assert result.exit_code == 2
    assert "max_messages must be between 1 and 1000" in result.output
    assert "Traceback" not in result.output


def test_cli_inspect_ordered_writes_jsonl_under_output_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "file-config.json"
    output_root = tmp_path / "inspection"
    _ordered_inspection_config(config, tmp_path / "events")

    async def fake_connect(options: dict[str, Any]) -> FakeInspectionConnection:
        _ = options
        return FakeInspectionConnection()

    monkeypatch.setattr(cli_main, "_connect_nats_for_inspection", fake_connect)

    result = CliRunner().invoke(
        app,
        [
            "inspect-ordered",
            str(config),
            "--output-root",
            str(output_root),
            "--output",
            "orders.jsonl",
        ],
    )

    assert result.exit_code == 0
    output_path = output_root / "orders.jsonl"
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["subject"] == "orders.created"
    assert "JSONL inspection records written to" in result.output
