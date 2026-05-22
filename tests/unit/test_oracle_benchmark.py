# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nats_sinks.core.metrics import InMemoryMetrics, MetricNames, observe_metric
from nats_sinks.testing import (
    OracleBenchmarkOptions,
    build_oracle_benchmark_report,
    render_oracle_benchmark_report,
    sanitize_public_text,
)


def test_oracle_benchmark_options_validate_bounds_and_modes() -> None:
    options = OracleBenchmarkOptions(
        message_count=256,
        batch_size=64,
        payload_shape="mixed",
        sink_mode="merge",
    )

    assert options.to_public_dict()["message_count"] == 256
    assert options.to_public_dict()["encryption_algorithm"] == "none"

    with pytest.raises(ValueError, match="message_count"):
        OracleBenchmarkOptions(message_count=0)
    with pytest.raises(ValueError, match="batch_size"):
        OracleBenchmarkOptions(batch_size=0)
    with pytest.raises(ValueError, match="encryption_algorithm"):
        OracleBenchmarkOptions(encryption_enabled=True, encryption_algorithm="not-aes")


def test_oracle_benchmark_report_summarizes_all_required_phases() -> None:
    metrics = InMemoryMetrics()
    observe_metric(metrics, MetricNames.NATS_FETCH_SECONDS, 0.20)
    observe_metric(metrics, MetricNames.MESSAGE_MAPPING_SECONDS, 0.05)
    observe_metric(metrics, MetricNames.ORACLE_EXECUTE_SECONDS, 0.70)
    observe_metric(metrics, MetricNames.ORACLE_COMMIT_SECONDS, 0.10)
    observe_metric(metrics, MetricNames.MESSAGE_ACK_SECONDS, 0.03)
    observe_metric(metrics, MetricNames.RETRY_BACKOFF_DELAY_SECONDS, 1.00)
    options = OracleBenchmarkOptions(message_count=100, batch_size=25)

    report = build_oracle_benchmark_report(
        options=options,
        metrics=metrics,
        explicit_phase_seconds={"publish": [0.40], "shutdown": [0.02]},
        notes=("user=operator password=secret nats://192.0.2.1:4222",),
    )
    data = report.to_dict()
    phases = {phase["phase"]: phase for phase in data["phases"]}

    assert set(phases) == {
        "publish",
        "fetch",
        "map",
        "write",
        "commit",
        "ack",
        "retry",
        "shutdown",
    }
    assert phases["publish"]["messages_per_second"] == 250.0
    assert phases["write"]["total_seconds"] == 0.7
    assert phases["commit"]["total_seconds"] == 0.1
    assert "<redacted-url>" in data["notes"][0]
    assert "<redacted-ip>" not in data["notes"][0]
    assert "secret" not in data["notes"][0]


def test_oracle_benchmark_markdown_is_public_safe() -> None:
    report = build_oracle_benchmark_report(
        options=OracleBenchmarkOptions(
            message_count=4,
            batch_size=2,
            payload_shape="text",
            sink_mode="insert_ignore",
            encryption_enabled=True,
            encryption_algorithm="aes-256-gcm",
        ),
        metrics=InMemoryMetrics(),
        explicit_phase_seconds={"publish": [0.2]},
        notes=("dsn=(description=(host=private.example.invalid)) token=abc123",),
    )

    rendered = render_oracle_benchmark_report(report, output_format="markdown")

    assert "# Oracle Benchmark Report" in rendered
    assert "| `payload_shape` | `text` |" in rendered
    assert "| `sink_mode` | `insert_ignore` |" in rendered
    assert "private.example" not in rendered
    assert "abc123" not in rendered
    assert "dsn=<redacted>" in rendered


def test_oracle_benchmark_redactor_removes_common_private_values() -> None:
    rendered = sanitize_public_text("user=admin password=secret token=abc tls://192.0.2.12:4222")

    assert "admin" not in rendered
    assert "secret" not in rendered
    assert "abc" not in rendered
    assert "192.0.2.12" not in rendered
    assert "<redacted-url>" in rendered


def _load_benchmark_script() -> ModuleType:
    script = Path("scripts/run-oracle-benchmark.py")
    spec = importlib.util.spec_from_file_location("run_oracle_benchmark", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_oracle_benchmark"] = module
    spec.loader.exec_module(module)
    return module


def test_oracle_benchmark_script_requires_explicit_live_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_benchmark_script()
    monkeypatch.delenv("NATS_SINKS_ORACLE_BENCHMARK", raising=False)
    monkeypatch.setattr(sys, "argv", ["run-oracle-benchmark.py", "--message-count", "1"])

    assert module.main() == 2
    captured = capsys.readouterr()
    assert "NATS_SINKS_ORACLE_BENCHMARK=1" in captured.err


def test_oracle_benchmark_shell_wrapper_has_valid_syntax() -> None:
    result = subprocess.run(
        ["/bin/sh", "-n", "scripts/run-oracle-benchmark.sh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
