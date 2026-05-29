# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Oracle NoSQL Database KVLite test backend assets.

The Oracle NoSQL Database test container is intentionally a development and
e2e certification aid for the Oracle NoSQL sink. These unit tests do not
require Docker. They inspect the helper scripts so CI can protect the local
test-backend security contract even on runners that cannot pull or start
containers.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-nosql-container-smoke.py"
SINK_E2E_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-nosql-sink-e2e.py"


def _load_smoke_script() -> ModuleType:
    """Load the smoke runner as a module for focused behavior checks."""

    spec = importlib.util.spec_from_file_location(
        "run_oracle_nosql_container_smoke",
        SMOKE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load Oracle NoSQL smoke-test script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_nosql_smoke_script_declares_official_image_strategy() -> None:
    """The test backend should use Oracle's documented KVLite image wrapper."""

    module = _load_smoke_script()
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert module.DEFAULT_IMAGE_REF == "ghcr.io/oracle/nosql:latest-ce"
    assert module.ORACLE_NOSQL_IMAGE_SOURCE == "GitHub Container Registry"
    assert module.ORACLE_NOSQL_MODE == "non-secure KVLite with HTTP proxy"
    assert module.DEFAULT_CONTAINER_PROXY_PORT == 8080
    assert "dockerfile" not in script.lower()
    assert "Oracle Linux 9 slim" not in script


def test_oracle_nosql_smoke_script_rejects_unsafe_options() -> None:
    """Operator input should be allow-list validated before Docker is called."""

    module = _load_smoke_script()

    with pytest.raises(module.OracleNoSqlContainerSmokeError, match="image-ref"):
        module.validate_args(
            argparse.Namespace(
                image_ref=" ghcr.io/oracle/nosql:latest-ce",
                table=module.DEFAULT_TABLE,
                timeout_seconds=240.0,
            )
        )

    with pytest.raises(module.OracleNoSqlContainerSmokeError, match="table"):
        module.validate_args(
            argparse.Namespace(
                image_ref=module.DEFAULT_IMAGE_REF,
                table="../bad",
                timeout_seconds=240.0,
            )
        )

    with pytest.raises(module.OracleNoSqlContainerSmokeError, match="between 30 and 900"):
        module.validate_args(
            argparse.Namespace(
                image_ref=module.DEFAULT_IMAGE_REF,
                table=module.DEFAULT_TABLE,
                timeout_seconds=1.0,
            )
        )


def test_oracle_nosql_smoke_script_builds_safe_docker_run_args() -> None:
    """Docker should be invoked through fixed argv lists and loopback binding."""

    module = _load_smoke_script()
    args = module.docker_run_args(
        container_name="nats-sinks-oracle-nosql-test-abc123",
        host_port=18080,
        image_ref=module.DEFAULT_IMAGE_REF,
    )

    assert args[:3] == ["docker", "run", "-d"]
    assert "--privileged" not in args
    assert "--network=host" not in args
    assert "--network" not in args
    assert "/var/run/docker.sock" not in " ".join(args)
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--security-opt" in args
    assert "no-new-privileges:true" in args
    assert "--env" in args
    assert "KV_PROXY_PORT=8080" in args
    assert "-p" in args
    assert "127.0.0.1:18080:8080" in args
    assert args[-1] == module.DEFAULT_IMAGE_REF


def test_oracle_nosql_smoke_script_uses_safe_subprocesses() -> None:
    """The Docker and pytest runners should avoid shell command construction."""

    smoke_script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    e2e_script = SINK_E2E_SCRIPT.read_text(encoding="utf-8")

    assert "shell=False" in smoke_script
    assert "shell=True" not in smoke_script
    assert "shell=False" in e2e_script
    assert "shell=True" not in e2e_script
    assert "--preserve-artifacts" in smoke_script
    assert "--preserve-artifacts" in e2e_script
    assert 'docker", "pull"' in smoke_script
    assert 'docker", "pull"' in e2e_script


def test_oracle_nosql_smoke_script_redacts_failed_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed subprocess output should not echo local generated identifiers."""

    module = _load_smoke_script()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="stdout has local-container-name",
            stderr="stderr has local-container-name",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.OracleNoSqlContainerSmokeError) as exc_info:
        module.run_command(["docker", "bad"], sensitive_values=("local-container-name",))

    message = str(exc_info.value)
    assert "local-container-name" not in message
    assert message.count("<redacted>") == 2


def test_oracle_nosql_smoke_script_requires_optional_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing optional SDK support should produce a clear local error."""

    module = _load_smoke_script()

    def fake_import(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(module.importlib, "import_module", fake_import)

    with pytest.raises(module.OracleNoSqlContainerSmokeError, match="borneo"):
        module.require_borneo()


def test_oracle_nosql_smoke_event_is_complete_fake_json() -> None:
    """The smoke record should look like a complete event JSON value."""

    module = _load_smoke_script()
    value = module.smoke_event_value()

    assert value["schema"] == "nats_sinks.oracle_nosql.container_smoke.v1"
    assert value["schema_version"] == 1
    assert value["source"] == "nats-sinks-oracle-nosql-container-smoke"
    assert value["subject"] == "example.oracle_nosql.smoke"
    assert value["payload"]["body"]["storage"] == "json-value"
    assert value["metadata"]["priority"] == "normal"
    assert value["metadata"]["classification"] == "NATO UNCLASSIFIED"
    assert value["metadata"]["labels"] == ["oracle-nosql-smoke", "local-test"]


def test_oracle_nosql_smoke_script_cleanup_is_default_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup by default keeps short-lived Oracle NoSQL test runs repeatable."""

    module = _load_smoke_script()
    calls: list[list[str]] = []

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        sensitive_values: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    module.cleanup("nats-sinks-oracle-nosql-test-abc123", preserve=False)

    assert calls == [["docker", "rm", "-f", "nats-sinks-oracle-nosql-test-abc123"]]


def test_oracle_nosql_smoke_script_retries_sdk_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient SDK readiness failures should be retried before writes proceed."""

    module = _load_smoke_script()
    calls = 0

    def fake_verify_json_value(*, endpoint: str, table_name: str, suffix: str) -> None:
        nonlocal calls
        calls += 1
        assert endpoint == "http://127.0.0.1:18080"
        assert table_name == module.DEFAULT_TABLE
        assert suffix == "abc123"
        if calls < 3:
            raise module.OracleNoSqlContainerSmokeError("proxy not ready")

    monkeypatch.setattr(module, "verify_json_value", fake_verify_json_value)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    current_time = {"value": 0.0}

    def fake_monotonic() -> float:
        current_time["value"] += 1.0
        return current_time["value"]

    monkeypatch.setattr(module.time, "monotonic", fake_monotonic)

    module.wait_for_oracle_nosql_ready(
        endpoint="http://127.0.0.1:18080",
        table_name=module.DEFAULT_TABLE,
        suffix="abc123",
        timeout_seconds=30.0,
    )

    assert calls == 3


def test_oracle_nosql_smoke_script_fails_closed_on_persistent_sdk_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent SDK readiness failures should stay bounded and explicit."""

    module = _load_smoke_script()

    def fake_verify_json_value(*, endpoint: str, table_name: str, suffix: str) -> None:
        _ = endpoint, table_name, suffix
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(module, "verify_json_value", fake_verify_json_value)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    current_time = {"value": 0.0}

    def fake_monotonic() -> float:
        current_time["value"] += 1.0
        return current_time["value"]

    monkeypatch.setattr(module.time, "monotonic", fake_monotonic)

    with pytest.raises(module.OracleNoSqlContainerSmokeError, match="SDK readiness"):
        module.wait_for_oracle_nosql_ready(
            endpoint="http://127.0.0.1:18080",
            table_name=module.DEFAULT_TABLE,
            suffix="abc123",
            timeout_seconds=2.0,
        )


def test_oracle_nosql_sink_e2e_script_reuses_container_assets_safely() -> None:
    """The sink e2e runner should preserve the same short-lived-container rules."""

    script = SINK_E2E_SCRIPT.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in script
    assert "run-oracle-nosql-container-smoke.py" in script
    assert "NATS_SINKS_ORACLE_NOSQL_INTEGRATION" in script
    assert "NATS_SINKS_ORACLE_NOSQL_ENDPOINT" in script
    assert "NATS_SINKS_ORACLE_NOSQL_AUTO_CREATE" in script
    assert "--preserve-artifacts" in script
    assert "smoke.docker_run_args" in script
    assert "smoke.cleanup(container_name" in script
