# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Oracle Coherence Community Edition test backend assets."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "examples" / "oracle-coherence-ce-test" / "Dockerfile"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-coherence-container-smoke.py"


def _load_smoke_script() -> ModuleType:
    """Load the smoke runner as a module for focused behavior checks."""

    spec = importlib.util.spec_from_file_location(
        "run_oracle_coherence_container_smoke",
        SMOKE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load Oracle Coherence smoke-test script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_coherence_dockerfile_wraps_explicit_official_image() -> None:
    """The test backend should use an explicit Oracle Coherence CE image."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in dockerfile
    assert 'ARG ORACLE_COHERENCE_CE_IMAGE="ghcr.io/oracle/coherence-ce:25.03.1"' in dockerfile
    assert "FROM ${ORACLE_COHERENCE_CE_IMAGE}" in dockerfile
    assert 'org.opencontainers.image.base.name="ghcr.io/oracle/coherence-ce:25.03.1"' in dockerfile
    assert "HEALTHCHECK NONE" in dockerfile
    assert "python:" not in dockerfile
    assert "debian:" not in dockerfile
    assert "ubuntu:" not in dockerfile
    assert "alpine:" not in dockerfile


def test_oracle_coherence_smoke_script_declares_runtime_contract() -> None:
    """The smoke runner should keep its image and port choices explicit."""

    module = _load_smoke_script()

    assert module.OFFICIAL_COHERENCE_CE_IMAGE == "ghcr.io/oracle/coherence-ce:25.03.1"
    assert module.DEFAULT_CONTAINER_GRPC_PORT == 1408
    assert module.DEFAULT_IMAGE_TAG == "nats-sinks-oracle-coherence-ce-test:local"
    assert module.DEFAULT_CACHE_NAME == "nats_sinks_smoke_events"


def test_oracle_coherence_smoke_script_rejects_unsafe_paths(tmp_path: Path) -> None:
    """The smoke runner should only use repository-local asset paths."""

    module = _load_smoke_script()
    args = argparse.Namespace(
        dockerfile=tmp_path / "Dockerfile",
        cache_name=module.DEFAULT_CACHE_NAME,
        timeout_seconds=180.0,
    )
    args.dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    with pytest.raises(module.OracleCoherenceSmokeError, match="must stay inside the repository"):
        module.validate_args(args)


def test_oracle_coherence_smoke_script_bounds_timeouts() -> None:
    """Unbounded readiness waits should be rejected before Docker is called."""

    module = _load_smoke_script()
    args = argparse.Namespace(
        dockerfile=DOCKERFILE,
        cache_name=module.DEFAULT_CACHE_NAME,
        timeout_seconds=1.0,
    )

    with pytest.raises(module.OracleCoherenceSmokeError, match="between 30 and 900"):
        module.validate_args(args)


@pytest.mark.parametrize("cache_name", ["", ".bad", "bad-", "bad name", "bad/name"])
def test_oracle_coherence_smoke_script_validates_cache_names(cache_name: str) -> None:
    """Cache names are user input and should be allow-list validated."""

    module = _load_smoke_script()

    with pytest.raises(module.OracleCoherenceSmokeError):
        module.validate_cache_name(cache_name)


def test_oracle_coherence_smoke_script_accepts_safe_cache_names() -> None:
    """Review-friendly cache names should remain usable for local testing."""

    module = _load_smoke_script()

    module.validate_cache_name("nats_sinks.smoke-events_1")


def test_oracle_coherence_smoke_script_uses_safe_docker_invocation() -> None:
    """The Docker runner should avoid shell command construction."""

    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "shell=False" in script
    assert "shell=True" not in script
    assert "--preserve-artifacts" in script
    assert "--read-only" in script
    assert "--tmpfs" in script
    assert "--cap-drop" in script
    assert "ALL" in script
    assert "no-new-privileges:true" in script
    assert "127.0.0.1" in script
    assert "docker_run_args" in script
    assert "logging.disable(logging.INFO)" in script


def test_oracle_coherence_smoke_script_redacts_failed_command_output(
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

    with pytest.raises(module.OracleCoherenceSmokeError) as exc_info:
        module.run_command(["docker", "bad"], sensitive_values=("local-container-name",))

    message = str(exc_info.value)
    assert "local-container-name" not in message
    assert message.count("<redacted>") == 2


def test_oracle_coherence_smoke_script_requires_optional_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing optional client support should produce a clear local error."""

    module = _load_smoke_script()

    def fake_import(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(module.importlib, "import_module", fake_import)

    with pytest.raises(module.OracleCoherenceSmokeError, match="coherence-client"):
        module.require_coherence_client()


def test_oracle_coherence_smoke_event_is_complete_fake_json() -> None:
    """The smoke record should look like a complete event JSON value."""

    module = _load_smoke_script()
    value = module.smoke_event_value()

    assert value["schema_version"] == 1
    assert value["source"] == "nats-sinks-oracle-coherence-ce-smoke"
    assert value["subject"] == "example.coherence.smoke"
    assert value["payload"]["body"]["storage"] == "json-value"
    assert value["metadata"]["priority"] == "normal"
    assert value["metadata"]["classification"] == "NATO UNCLASSIFIED"
    assert value["metadata"]["labels"] == ["coherence-smoke", "local-test"]


def test_oracle_coherence_smoke_script_cleanup_is_default_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup by default keeps short-lived Coherence test runs repeatable."""

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

    module.cleanup("coherence-test-container", preserve=False)

    assert calls == [["docker", "rm", "-f", "coherence-test-container"]]


def test_oracle_coherence_smoke_script_preserve_skips_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preserve flag should keep local artifacts for explicit debugging."""

    module = _load_smoke_script()
    calls: list[list[str]] = []

    monkeypatch.setattr(module, "run_command", lambda args, **kwargs: calls.append(args))

    module.cleanup("coherence-test-container", preserve=True)

    assert calls == []
