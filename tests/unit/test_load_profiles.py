# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nats_sinks.testing import (
    LoadProfileOptions,
    render_load_profile_report,
    run_load_profile,
)


def test_load_profile_options_validate_bounds() -> None:
    with pytest.raises(ValueError, match="message_count"):
        LoadProfileOptions(message_count=0)
    with pytest.raises(ValueError, match="batch_size"):
        LoadProfileOptions(batch_size=0)
    with pytest.raises(ValueError, match="seed"):
        LoadProfileOptions(seed=-1)


def test_normal_load_profile_reports_all_core_phases(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.json"
    report = run_load_profile(
        LoadProfileOptions(
            profile="normal",
            message_count=16,
            batch_size=4,
            encrypt_payloads=True,
            metrics_snapshot_file=metrics_path,
            preserve_metrics_snapshot=True,
        )
    )
    data = report.to_dict()
    phases = {phase["phase"]: phase for phase in data["phases"]}

    assert data["counters"]["messages_generated"] == 16
    assert data["counters"]["messages_written"] == 16
    assert data["counters"]["messages_acked"] == 16
    assert data["counters"]["messages_encrypted"] == 16
    assert metrics_path.is_file()
    assert json.loads(metrics_path.read_text(encoding="utf-8"))["schema"] == (
        "nats_sinks.metrics.snapshot.v1"
    )
    for phase in (
        "fetch",
        "payload_normalization",
        "metadata_resolution",
        "encryption",
        "backend_write",
        "commit",
        "ack",
        "metrics_snapshot",
        "shutdown",
    ):
        assert phase in phases


def test_retry_dlq_and_shutdown_profiles_report_expected_pressure() -> None:
    retry_report = run_load_profile(
        LoadProfileOptions(profile="retry", message_count=16, batch_size=4)
    )
    dlq_report = run_load_profile(LoadProfileOptions(profile="dlq", message_count=18, batch_size=6))
    shutdown_report = run_load_profile(
        LoadProfileOptions(profile="shutdown", message_count=10, batch_size=4)
    )

    assert retry_report.counters["retry_events"] == 2
    assert retry_report.counters["messages_nacked"] == 8
    assert retry_report.counters["messages_acked"] == 16

    assert dlq_report.counters["messages_dlq"] > 0
    assert dlq_report.counters["messages_acked"] == 18
    assert dlq_report.counters["messages_written"] < 18

    assert shutdown_report.counters["shutdown_unfetched_messages"] == 2
    assert shutdown_report.counters["messages_acked"] == 8


def test_load_profile_markdown_is_sanitized() -> None:
    report = run_load_profile(LoadProfileOptions(profile="normal", message_count=4, batch_size=2))
    rendered = render_load_profile_report(report, output_format="markdown")

    assert "# Load Profile Report" in rendered
    assert "Phase Timings" in rendered
    assert "secret-value" not in rendered.lower()
    assert "nats://" not in rendered
    assert "192.168." not in rendered


def test_load_profile_script_argument_handling(tmp_path: Path) -> None:
    report_path = tmp_path / "report.md"
    metrics_path = tmp_path / "metrics.json"
    result = subprocess.run(  # noqa: S603 - fixed Python executable with static script path.
        [
            sys.executable,
            "scripts/run-load-profile.py",
            "--profile",
            "dlq",
            "--message-count",
            "18",
            "--batch-size",
            "6",
            "--metrics-snapshot-file",
            str(metrics_path),
            "--format",
            "markdown",
            "--report-file",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""
    assert report_path.read_text(encoding="utf-8").startswith("# Load Profile Report")
    assert not metrics_path.exists()


def test_load_profile_shell_wrapper_has_valid_syntax() -> None:
    subprocess.run(
        ["/bin/sh", "-n", "scripts/run-load-profile.sh"],
        check=True,
    )
