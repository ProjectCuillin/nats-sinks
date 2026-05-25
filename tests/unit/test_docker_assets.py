# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local Docker image and Compose smoke-test assets.

These tests deliberately inspect files instead of starting Docker.  The actual
container build and runtime flow are covered by `scripts/run-docker-local-smoke.py`
when Docker is available on a developer machine or release workstation.
"""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType

import pytest

from nats_sinks.core.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-docker-local-smoke.py"


def _load_smoke_script() -> ModuleType:
    """Load the smoke-test script as a module for focused unit tests."""

    spec = importlib.util.spec_from_file_location("run_docker_local_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load Docker smoke-test script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dockerfile_uses_oracle_linux_slim_base_image() -> None:
    """The local image should stay aligned with the Oracle Linux base-image policy."""

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [
        line.strip() for line in dockerfile.splitlines() if line.strip().startswith("FROM ")
    ]

    assert "SPDX-License-Identifier: Apache-2.0" in dockerfile
    assert from_lines == ["FROM container-registry.oracle.com/os/oraclelinux:9-slim"]
    assert "python:3.12-slim" not in dockerfile
    assert "debian:" not in dockerfile
    assert "ubuntu:" not in dockerfile
    assert "alpine:" not in dockerfile


def test_dockerfile_uses_project_entrypoint_and_non_root_user() -> None:
    """The local image should run `nats-sink` without root privileges."""

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python3.11 -m pip install" in dockerfile
    assert "microdnf install -y --setopt=install_weak_deps=0" in dockerfile
    assert "useradd --uid 10001 --gid 10001" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["nats-sink"]' in dockerfile


def test_dockerfile_declares_hardening_runtime_invariants() -> None:
    """The production image should advertise stable hardening boundaries."""

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "org.opencontainers.image.documentation" in dockerfile
    assert "org.opencontainers.image.version" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "org.opencontainers.image.created" in dockerfile
    assert (
        'org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim"'
        in dockerfile
    )
    assert "PIP_NO_CACHE_DIR=1" in dockerfile
    assert "XDG_CACHE_HOME=/tmp/nats-sinks-cache" in dockerfile
    assert "chmod -R go-w /opt/nats-sinks" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert "HEALTHCHECK NONE" in dockerfile


def test_dockerignore_excludes_private_and_generated_paths() -> None:
    """Build context must not include local secrets, wallets, caches, or docs output."""

    ignored = {
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert ".local" in ignored
    assert ".env" in ignored
    assert ".git" in ignored
    assert "dist" in ignored
    assert "site" in ignored
    assert "*.log" in ignored
    assert "*.key" in ignored
    assert "*.p12" in ignored


def test_container_image_artifacts_are_never_version_controlled() -> None:
    """Generated image archives and OCI layouts must stay outside Git.

    Dockerfiles, Compose files, and scripts are source artifacts. Built
    containers are deployment artifacts and can contain operating-system layers,
    dependency caches, generated state, or sensitive runtime files. The project
    therefore ignores common image export formats both for Git and Docker build
    contexts.
    """

    git_ignored = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    docker_ignored = {
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    required_git_patterns = {
        "*.docker.tar",
        "*.image.tar",
        "*.oci.tar",
        "*.container.tar",
        "container-exports/",
        "container-images/",
        "docker-exports/",
        "docker-images/",
        "image-archives/",
        "oci-layout/",
        "oci-layouts/",
        "oci-images/",
    }
    required_docker_patterns = {pattern.rstrip("/") for pattern in required_git_patterns}

    assert required_git_patterns <= git_ignored
    assert required_docker_patterns <= docker_ignored


def test_docker_compose_stack_declares_nats_and_sink_services() -> None:
    """The local Compose example should connect the image to a NATS service."""

    compose = json.loads(
        (REPO_ROOT / "examples" / "docker-local" / "compose.json").read_text(encoding="utf-8")
    )

    services = compose["services"]
    assert services["nats"]["image"].startswith("nats:")
    assert services["nats"]["command"] == ["-js", "-m", "8222"]
    assert services["nats-sink"]["image"] == "${NATS_SINKS_IMAGE:-nats-sinks:local}"
    assert services["nats-sink"]["depends_on"] == ["nats"]
    assert services["nats-sink"]["command"] == ["run", "/etc/nats-sinks/config.json"]
    assert services["nats-sink"]["read_only"] is True
    assert services["nats-sink"]["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev"]  # noqa: S108
    assert services["nats-sink"]["cap_drop"] == ["ALL"]
    assert services["nats-sink"]["security_opt"] == ["no-new-privileges:true"]
    assert any(
        mount.endswith(":/etc/nats-sinks/config.json:ro")
        for mount in services["nats-sink"]["volumes"]
    )
    assert any(
        "NATS_SINKS_DOCKER_OUTPUT_DIR" in mount for mount in services["nats-sink"]["volumes"]
    )


def test_docker_local_config_is_valid_file_sink_config() -> None:
    """The Docker config should validate through the production JSON loader."""

    config = load_config(
        REPO_ROOT / "examples" / "docker-local" / "config.json",
        env_overrides=False,
    )

    assert config.nats.url == "nats://nats:4222"
    assert config.nats.stream == "ORDERS"
    assert config.nats.subject == "orders.*"
    assert config.delivery.batch_size == 8
    assert config.delivery.batch_timeout_ms == 500
    assert config.message_metadata.priority.default == "normal"
    assert config.message_metadata.classification.default == "NATO UNCLASSIFIED"
    assert config.message_metadata.labels.default == ("docker-local", "smoke-test")
    assert config.sink.type == "file"


def test_docker_smoke_script_uses_safe_subprocess_style() -> None:
    """The Docker smoke runner should avoid shell command construction."""

    script = (REPO_ROOT / "scripts" / "run-docker-local-smoke.py").read_text(encoding="utf-8")

    assert "shell=False" in script
    assert "docker" in script
    assert "compose" in script
    assert "--message-count" in script
    assert "shell=True" not in script


def test_docker_smoke_script_reports_port_allocation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The smoke script should not expose a traceback when local sockets are blocked."""

    module = _load_smoke_script()

    class DeniedSocket:
        def __enter__(self) -> DeniedSocket:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def bind(self, address: tuple[str, int]) -> None:
            raise PermissionError("denied")

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: DeniedSocket())

    with pytest.raises(module.SmokeTestError, match="Unable to allocate a local loopback port"):
        module.find_free_port()


@pytest.mark.asyncio
async def test_docker_smoke_script_uses_quiet_nats_readiness_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expected NATS startup retries should not use the noisy default error callback."""

    module = _load_smoke_script()
    callbacks: list[object] = []
    monotonic_values = iter([0.0, 0.0, 1.0])

    async def fake_connect(
        *,
        servers: list[str],
        connect_timeout: int,
        error_cb: object | None = None,
    ) -> object:
        callbacks.append(error_cb)
        raise ConnectionRefusedError("not ready")

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(module.nats, "connect", fake_connect)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values, 1.0))

    with pytest.raises(module.SmokeTestError, match="NATS did not become ready"):
        await module.wait_for_nats("nats://127.0.0.1:1", 0.5)

    assert callbacks
    assert all(callable(callback) for callback in callbacks)


@pytest.mark.asyncio
async def test_docker_smoke_script_reports_seed_failures_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NATS seeding failures should be converted into a concise smoke-test error."""

    module = _load_smoke_script()

    async def fake_connect(*args: object, **kwargs: object) -> object:
        raise ConnectionRefusedError("not ready")

    monkeypatch.setattr(module.nats, "connect", fake_connect)

    with pytest.raises(module.SmokeTestError, match="Unable to seed NATS stream"):
        await module.seed_stream("nats://127.0.0.1:1", 1)
