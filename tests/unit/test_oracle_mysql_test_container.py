# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Oracle MySQL test database container assets.

The Oracle MySQL test container is intentionally a development and e2e
certification aid for the Oracle MySQL sink.  These unit tests do not require
Docker.  They inspect the Dockerfile, entrypoint, and smoke runner so CI can
protect the security contract even on runners that cannot build or start local
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
DOCKERFILE = REPO_ROOT / "examples" / "oracle-mysql-test" / "Dockerfile"
ENTRYPOINT = REPO_ROOT / "examples" / "oracle-mysql-test" / "entrypoint.sh"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-mysql-container-smoke.py"
SINK_E2E_SCRIPT = REPO_ROOT / "scripts" / "run-mysql-sink-e2e.py"


def _load_smoke_script() -> ModuleType:
    """Load the smoke runner as a module for focused behavior checks."""

    spec = importlib.util.spec_from_file_location("run_oracle_mysql_container_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load Oracle MySQL smoke-test script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_mysql_dockerfile_uses_only_oracle_linux_9_slim() -> None:
    """The test database image must stay anchored on the approved Oracle base."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [
        line.strip() for line in dockerfile.splitlines() if line.strip().startswith("FROM ")
    ]

    assert "SPDX-License-Identifier: Apache-2.0" in dockerfile
    assert from_lines == ["FROM container-registry.oracle.com/os/oraclelinux:9-slim"]
    assert "python:" not in dockerfile
    assert "debian:" not in dockerfile
    assert "ubuntu:" not in dockerfile
    assert "alpine:" not in dockerfile


def test_oracle_mysql_dockerfile_declares_version_and_oracle_repo() -> None:
    """The selected Oracle MySQL version should be explicit and auditable."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert 'ARG ORACLE_MYSQL_VERSION="9.7.0"' in dockerfile
    assert "OracleLinux/OL9/MySQL97/community" in dockerfile
    assert "mysql-community-server" in dockerfile
    assert "mysql-community-client" in dockerfile
    assert "gpgcheck=1" in dockerfile
    assert "HEALTHCHECK NONE" in dockerfile
    assert (
        'org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim"'
        in dockerfile
    )


def test_oracle_mysql_entrypoint_requires_secret_files_and_safe_bootstrap() -> None:
    """Startup should fail closed when generated test credentials are absent."""

    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "required secret file is missing" in entrypoint
    assert "secret file contains an unsupported value" in entrypoint
    assert "secret file value length is outside the supported range" in entrypoint
    assert "mysqld --initialize-insecure" in entrypoint
    assert "--log-error=/tmp/oracle-mysql-initialize.err" in entrypoint
    assert "--skip-networking" in entrypoint
    assert "--skip-name-resolve" in entrypoint
    assert "--local-infile=0" in entrypoint
    assert "--symbolic-links=0" in entrypoint
    assert "--secure-file-priv=NULL" in entrypoint
    assert 'MYSQL_PWD="$ROOT_PASSWORD" mysqladmin' in entrypoint


def test_oracle_mysql_smoke_script_generates_strong_local_passwords() -> None:
    """Generated test passwords should be long, shell-safe, and non-deterministic."""

    module = _load_smoke_script()

    first = module.generate_password()
    second = module.generate_password()

    assert len(first) == module.PASSWORD_LENGTH
    assert len(second) == module.PASSWORD_LENGTH
    assert first != second
    assert set(first) <= set(module.PASSWORD_ALPHABET)
    assert set(second) <= set(module.PASSWORD_ALPHABET)


def test_oracle_mysql_smoke_script_redacts_generated_secrets() -> None:
    """Operator-facing command failures must redact generated credentials."""

    module = _load_smoke_script()

    text = module.redact(
        "root-password=abc123 app-password=def456 still visible",
        ("abc123", "def456"),
    )

    assert "abc123" not in text
    assert "def456" not in text
    assert text.count("<redacted>") == 2
    assert "still visible" in text


def test_oracle_mysql_smoke_script_rejects_unsafe_paths(tmp_path: Path) -> None:
    """The smoke runner should only use repository-local asset paths."""

    module = _load_smoke_script()
    args = argparse.Namespace(
        dockerfile=tmp_path / "Dockerfile",
        secret_dir=REPO_ROOT / ".local" / "oracle-mysql-test",
        timeout_seconds=180.0,
    )
    args.dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    with pytest.raises(module.OracleMySqlSmokeError, match="must stay inside the repository"):
        module.validate_args(args)


def test_oracle_mysql_smoke_script_bounds_timeouts() -> None:
    """Unbounded readiness waits should be rejected before Docker is called."""

    module = _load_smoke_script()
    args = argparse.Namespace(
        dockerfile=DOCKERFILE,
        secret_dir=REPO_ROOT / ".local" / "oracle-mysql-test",
        timeout_seconds=1.0,
    )

    with pytest.raises(module.OracleMySqlSmokeError, match="between 30 and 900"):
        module.validate_args(args)


def test_oracle_mysql_smoke_script_uses_safe_docker_invocation() -> None:
    """The Docker runner should avoid shell command construction and password argv."""

    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "shell=False" in script
    assert "shell=True" not in script
    assert "secrets.choice" in script
    assert "--preserve-artifacts" in script
    assert "--read-only" in script
    assert "--tmpfs" in script
    assert "--cap-drop" in script
    assert "CHOWN" in script
    assert "DAC_OVERRIDE" in script
    assert "FOWNER" in script
    assert "SETGID" in script
    assert "SETUID" in script
    assert "no-new-privileges:true" in script
    assert "--env" in script
    assert "MYSQL_PWD" in script
    assert "--password" not in script


def test_oracle_mysql_smoke_script_redacts_failed_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed subprocess output should not echo generated passwords."""

    module = _load_smoke_script()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="stdout has secret-one",
            stderr="stderr has secret-two",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.OracleMySqlSmokeError) as exc_info:
        module.run_command(["docker", "bad"], secrets_to_redact=("secret-one", "secret-two"))

    message = str(exc_info.value)
    assert "secret-one" not in message
    assert "secret-two" not in message
    assert message.count("<redacted>") == 2


def test_oracle_mysql_smoke_script_cleanup_is_default_behavior() -> None:
    """Cleanup by default keeps short-lived Oracle MySQL test runs repeatable."""

    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "preserve=args.preserve_artifacts" in script
    assert '["docker", "rm", "-f", container_name]' in script
    assert '["docker", "volume", "rm", "-f", volume_name]' in script
    assert "shutil.rmtree(secret_dir)" in script


def test_oracle_mysql_sink_e2e_script_reuses_container_assets_safely() -> None:
    """The sink e2e runner should preserve the same short-lived-container rules."""

    script = SINK_E2E_SCRIPT.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in script
    assert "run-oracle-mysql-container-smoke.py" in script
    assert "NATS_SINKS_MYSQL_PASSWORD_ENV" in script
    assert "NATS_SINKS_MYSQL_PASSWORD" in script
    assert "--preserve-artifacts" in script
    assert "--read-only" in script
    assert "--cap-drop" in script
    assert "no-new-privileges:true" in script
    assert "secrets_to_redact=(app_password,)" in script
    assert "smoke.cleanup(container_name, volume_name, secret_dir" in script
