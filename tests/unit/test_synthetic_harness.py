# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY
from nats_sinks.testing import (
    SyntheticScenarioProfile,
    generate_synthetic_scenario,
    render_synthetic_report_markdown,
    run_file_sink_synthetic_scenario,
    synthetic_report,
)


def test_synthetic_scenario_generation_covers_required_cases() -> None:
    profile = SyntheticScenarioProfile(message_count=18, seed=7)

    messages = generate_synthetic_scenario(profile)
    cases = {message.case for message in messages}

    assert len(messages) == 18
    assert {
        "valid_json",
        "malformed_json_text",
        "duplicate",
        "stale",
        "encrypted_marker",
        "classified",
        "priority",
        "labeled",
        "empty",
    } <= cases
    assert any(message.malformed_json_text for message in messages)
    assert any(message.stale for message in messages)
    assert any(message.encrypted_marker for message in messages)
    assert any(message.envelope.priority == "urgent" for message in messages)
    assert any(message.envelope.classification == "NATO SECRET" for message in messages)
    assert any("f2t2ea-example" in message.envelope.labels for message in messages)


def test_synthetic_scenario_generation_is_deterministic() -> None:
    profile = SyntheticScenarioProfile(message_count=12, seed=99)

    first = generate_synthetic_scenario(profile)
    second = generate_synthetic_scenario(profile)

    assert [message.envelope.data for message in first] == [
        message.envelope.data for message in second
    ]
    assert [message.envelope.idempotency_key() for message in first] == [
        message.envelope.idempotency_key() for message in second
    ]


def test_duplicate_case_reuses_idempotency_key_and_file_identity() -> None:
    profile = SyntheticScenarioProfile(message_count=4)

    messages = generate_synthetic_scenario(profile)
    duplicate = messages[2]
    original = messages[1]

    assert duplicate.case == "duplicate"
    assert duplicate.duplicate_of_sequence == original.envelope.stream_sequence
    assert duplicate.envelope.idempotency_key() == original.envelope.idempotency_key()
    assert duplicate.envelope.subject == original.envelope.subject
    assert duplicate.envelope.data == original.envelope.data


def test_synthetic_report_is_sanitized_and_counts_edge_cases() -> None:
    profile = SyntheticScenarioProfile(message_count=18)
    messages = generate_synthetic_scenario(profile)

    report = synthetic_report(messages, profile_name=profile.name, sink="core")
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.generated_messages == 18
    assert report.duplicate_messages == 2
    assert report.malformed_json_text_messages == 2
    assert report.encrypted_marker_messages == 2
    assert report.stale_messages == 2
    assert report.priority_values["urgent"] == 2
    assert report.classification_values["NATO SECRET"] == 2
    assert report.labels["f2t2ea-example"] == 2
    assert "payload" not in rendered.casefold()
    assert "password" not in rendered.casefold()
    assert "://" not in rendered


def test_markdown_report_contains_public_summary_tables() -> None:
    profile = SyntheticScenarioProfile(message_count=9)
    report = synthetic_report(
        generate_synthetic_scenario(profile),
        profile_name=profile.name,
        sink="core",
    )

    rendered = render_synthetic_report_markdown(report)

    assert "# Synthetic Scenario Report" in rendered
    assert "| `valid_json` | 1 |" in rendered
    assert "| `NATO SECRET` | 1 |" in rendered
    assert "| `f2t2ea-example` | 1 |" in rendered


def test_file_sink_synthetic_harness_writes_durable_files(tmp_path: Path) -> None:
    profile = SyntheticScenarioProfile(message_count=18)

    result = run_file_sink_synthetic_scenario(
        profile=profile,
        output_dir=tmp_path / "synthetic-files",
        preserve_files=True,
    )
    files = sorted((tmp_path / "synthetic-files").rglob("*.json"))

    assert result.retained_output_directory == tmp_path / "synthetic-files"
    assert result.report.sink == "file"
    assert result.report.file_count == 16
    assert len(files) == 16


def test_file_sink_synthetic_harness_supports_gzip_and_cleanup(tmp_path: Path) -> None:
    profile = SyntheticScenarioProfile(message_count=9)
    output_dir = tmp_path / "delete-me"

    result = run_file_sink_synthetic_scenario(
        profile=profile,
        output_dir=output_dir,
        compression="gzip",
        preserve_files=False,
    )

    assert result.report.compression == "gzip"
    assert result.report.file_count == 8
    assert result.retained_output_directory is None
    assert not output_dir.exists()


def test_synthetic_file_records_preserve_metadata_and_encrypted_marker(tmp_path: Path) -> None:
    profile = SyntheticScenarioProfile(message_count=9)

    run_file_sink_synthetic_scenario(
        profile=profile,
        output_dir=tmp_path,
        preserve_files=True,
    )
    records = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(tmp_path.rglob("*.json"))
    ]

    assert any(record["priority"] == "urgent" for record in records)
    assert any(record["classification"] == "NATO SECRET" for record in records)
    expected_labels = "synthetic;mission-test;sensor-fusion;f2t2ea-example"
    assert any(record["labels"] == expected_labels for record in records)
    assert any(ENCRYPTED_PAYLOAD_KEY in record["payload"] for record in records)
    assert any(
        record["payload_info"]["original_format"] == "text"
        and record["payload"]["_nats_sinks"]["payload_format"] == "text"
        for record in records
    )


def _load_synthetic_script() -> ModuleType:
    script = Path("scripts/run-synthetic-harness.py")
    spec = importlib.util.spec_from_file_location("run_synthetic_harness", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_synthetic_harness"] = module
    spec.loader.exec_module(module)
    return module


def test_synthetic_harness_script_writes_markdown_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_synthetic_script()
    report_file = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-synthetic-harness.py",
            "--sink",
            "file",
            "--message-count",
            "9",
            "--output-dir",
            str(tmp_path / "files"),
            "--format",
            "markdown",
            "--report-file",
            str(report_file),
        ],
    )

    assert module.main() == 0
    rendered = report_file.read_text(encoding="utf-8")

    assert "Synthetic Scenario Report" in rendered
    assert "Durable files: `8`" in rendered
    assert not (tmp_path / "files").exists()
