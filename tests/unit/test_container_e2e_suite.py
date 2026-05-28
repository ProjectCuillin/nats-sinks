# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the opt-in full container-backed sink e2e suite."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE_SCRIPT = REPO_ROOT / "scripts" / "run-container-e2e-suite.py"
CHECK_SINKS_SCRIPT = REPO_ROOT / "scripts" / "check-sinks.sh"


def _load_suite_script() -> ModuleType:
    """Load the suite runner as a module for deterministic unit tests."""

    spec = importlib.util.spec_from_file_location("run_container_e2e_suite", SUITE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load container e2e suite script.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_container_e2e_suite"] = module
    spec.loader.exec_module(module)
    return module


def test_container_e2e_suite_declares_expected_backends() -> None:
    """The full suite should include both Oracle key/value container runners."""

    module = _load_suite_script()

    assert [runner.name for runner in module.BACKEND_RUNNERS] == [
        "Oracle NoSQL Database",
        "Oracle Coherence Community Edition",
    ]
    assert [runner.script.name for runner in module.BACKEND_RUNNERS] == [
        "run-oracle-nosql-sink-e2e.py",
        "run-coherence-sink-e2e.py",
    ]


def test_container_e2e_suite_builds_fixed_commands() -> None:
    """Backend commands should be fixed argv lists with no shell construction."""

    module = _load_suite_script()
    command = module.backend_command(
        module.BACKEND_RUNNERS[0],
        timeout_seconds=300.0,
        preserve_artifacts=True,
    )

    assert command == [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run-oracle-nosql-sink-e2e.py"),
        "--timeout-seconds",
        "300",
        "--preserve-artifacts",
    ]


@pytest.mark.parametrize("timeout_seconds", [29.0, 901.0])
def test_container_e2e_suite_bounds_readiness_timeout(timeout_seconds: float) -> None:
    """The orchestration helper should reject unbounded readiness settings."""

    module = _load_suite_script()
    args = argparse.Namespace(timeout_seconds=timeout_seconds)

    with pytest.raises(module.ContainerE2eSuiteError, match="between 30 and 900"):
        module.validate_args(args)


def test_container_e2e_suite_runs_backend_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend e2e helper should be invoked through subprocess without shell."""

    module = _load_suite_script()
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.run_backend(
        module.BACKEND_RUNNERS[1],
        timeout_seconds=300.0,
        preserve_artifacts=False,
    )

    assert calls == [
        {
            "command": [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run-coherence-sink-e2e.py"),
                "--timeout-seconds",
                "300",
            ],
            "check": False,
            "cwd": REPO_ROOT,
            "shell": False,
        }
    ]


def test_container_e2e_suite_fails_closed_on_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed backend helper should make the whole suite fail."""

    module = _load_suite_script()

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command, returncode=7)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--timeout-seconds", "300"]) == 1


def test_check_sinks_exposes_single_full_container_e2e_gate() -> None:
    """The sink checks should include one explicit all-container e2e gate."""

    script = CHECK_SINKS_SCRIPT.read_text(encoding="utf-8")

    assert 'NATS_SINKS_RUN_CONTAINER_E2E:-0' in script
    assert "python scripts/run-container-e2e-suite.py" in script
    assert "NATS_SINKS_RUN_CONTAINER_E2E" not in script.split("pytest \\\n", maxsplit=1)[0]
    assert "NATS_SINKS_RUN_COHERENCE_E2E" in script
    assert "NATS_SINKS_RUN_ORACLE_NOSQL_SINK_E2E" in script
