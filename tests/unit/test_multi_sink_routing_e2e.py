# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic multi-sink routing e2e certification flow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from typer.testing import CliRunner

from nats_sinks.cli.main import app
from nats_sinks.testing.multi_sink_routing import (
    MULTI_SINK_EXAMPLE_CONFIG,
    run_reduced_multi_sink_routing_flow_sync,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run-multi-sink-routing-e2e.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_multi_sink_routing_e2e", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load multi-sink routing script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_validate_accepts_multi_sink_routing_e2e_example() -> None:
    """The documented route matrix must pass the production config validator."""

    config = REPO_ROOT / MULTI_SINK_EXAMPLE_CONFIG
    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Active sink: fanout" in result.output
    assert "oracle_primary (oracle)" in result.output
    assert "mysql_audit (mysql)" in result.output
    assert "file_audit (file)" in result.output
    assert "coherence_read_model (coherence)" in result.output
    assert "secret_sensor_multi_sink" in result.output
    assert "tasking_coherence_read_model" in result.output


def test_reduced_flow_routes_expected_messages_and_exercises_ack_policies(
    tmp_path: Path,
) -> None:
    """Reduced mode should certify route selection and ACK-gate behavior."""

    report = run_reduced_multi_sink_routing_flow_sync(
        work_dir=tmp_path,
        config_path=REPO_ROOT / MULTI_SINK_EXAMPLE_CONFIG,
    )

    assert report.config_validated is True
    assert report.actual_by_sink == {
        "coherence_read_model": ["MSG-SECRET-1", "MSG-TASKING-1"],
        "file_audit": ["MSG-SECRET-1"],
        "mysql_audit": ["MSG-SECRET-1"],
        "oracle_primary": ["MSG-SECRET-1"],
        "oracle_unclass": ["MSG-UNCLASS-1"],
    }
    assert report.expected_by_sink == report.actual_by_sink
    assert report.no_route_message_ids == ["MSG-NO-ROUTE-1", "MSG-TRAINING-1"]
    assert report.optional_timeout_observed is True
    assert report.required_failure_blocked_ack is True
    assert report.reject_no_route_observed is True
    assert report.duplicate_attempts_by_sink == {
        "coherence_read_model": 2,
        "file_audit": 1,
        "mysql_audit": 1,
        "oracle_primary": 1,
        "oracle_unclass": 1,
    }
    assert report.evidence_file_counts_by_sink == {
        "coherence_read_model": 2,
        "file_audit": 1,
        "mysql_audit": 1,
        "oracle_primary": 1,
        "oracle_unclass": 1,
    }


def test_reduced_flow_report_and_evidence_are_sanitized(tmp_path: Path) -> None:
    """The reduced report must avoid payloads, local paths, and credentials."""

    report = run_reduced_multi_sink_routing_flow_sync(
        work_dir=tmp_path,
        config_path=REPO_ROOT / MULTI_SINK_EXAMPLE_CONFIG,
    )
    rendered = report.to_json()

    assert str(tmp_path) not in rendered
    assert "synthetic" not in rendered
    assert "password" not in rendered.casefold()
    assert "secret_sensor_multi_sink" in rendered
    assert "Nats-Sinks-Flow=multi-sink-routing-e2e" in rendered

    evidence_files = sorted((tmp_path / "success").glob("*/*.json"))
    assert len(evidence_files) == 6
    for path in evidence_files:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert sorted(record) == [
            "classification",
            "labels",
            "message_id",
            "priority",
            "route_header",
            "sink",
            "sink_type",
            "stream_sequence",
            "subject_family",
        ]
        assert "synthetic" not in json.dumps(record)


def test_multi_sink_routing_script_writes_pipe_friendly_report(
    tmp_path: Path,
    capsys,
) -> None:
    """The local runner should be usable from shell scripts without secrets."""

    module = _load_script()
    report_path = tmp_path / "report.json"
    work_dir = tmp_path / "work"

    exit_code = module.main(
        [
            "--work-dir",
            str(work_dir),
            "--preserve-work-dir",
            "--config",
            str(REPO_ROOT / MULTI_SINK_EXAMPLE_CONFIG),
            "--output",
            str(report_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"mode": "reduced"' in captured.out
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["actual_by_sink"]["oracle_primary"] == ["MSG-SECRET-1"]
    assert "required_failure_blocked_ack" in report
    assert str(work_dir) not in report_path.read_text(encoding="utf-8")


def test_script_does_not_use_subprocess_or_shell() -> None:
    """Reduced mode must stay local and cannot spawn shell commands."""

    script = SCRIPT.read_text(encoding="utf-8")

    assert "subprocess" not in script
    assert "shell=True" not in script
