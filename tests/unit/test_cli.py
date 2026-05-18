# SPDX-License-Identifier: Apache-2.0
"""Unit tests for small CLI behaviors that should not require network access."""

import json
from pathlib import Path

from typer.testing import CliRunner

from nats_sinks import __version__
from nats_sinks.cli.main import app


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
