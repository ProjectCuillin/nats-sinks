# SPDX-License-Identifier: Apache-2.0
"""Unit tests for small CLI behaviors that should not require network access."""

from typer.testing import CliRunner

from nats_sinks import __version__
from nats_sinks.cli.main import app


def test_cli_version_option_exits_before_requiring_command() -> None:
    """The advertised global `--version` flag should work without a subcommand."""

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__
