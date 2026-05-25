#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the Oracle MySQL sink e2e test against a short-lived test container."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-mysql-container-smoke.py"
DEFAULT_SECRET_DIR = REPO_ROOT / ".local" / "oracle-mysql-sink-e2e"
DEFAULT_TABLE = "NATS_SINKS_MYSQL_E2E_EVENTS"


class OracleMySqlSinkE2eError(RuntimeError):
    """Raised when the Oracle MySQL sink e2e harness cannot complete."""


def _load_smoke_module() -> ModuleType:
    """Load the existing Oracle MySQL test-container helpers."""

    spec = importlib.util.spec_from_file_location("oracle_mysql_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise OracleMySqlSinkE2eError("Unable to load Oracle MySQL smoke helper.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    """Parse bounded e2e-test arguments."""

    smoke = _load_smoke_module()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-tag", default=smoke.DEFAULT_IMAGE_TAG)
    parser.add_argument("--dockerfile", type=Path, default=smoke.DEFAULT_DOCKERFILE)
    parser.add_argument("--secret-dir", type=Path, default=DEFAULT_SECRET_DIR)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container, volume, and generated secret files for debugging.",
    )
    return parser.parse_args()


def _pytest_env(*, host_port: int, app_password: str, table: str) -> dict[str, str]:
    """Build a sanitized subprocess environment for pytest."""

    env = os.environ.copy()
    env.update(
        {
            "NATS_SINKS_MYSQL_INTEGRATION": "1",
            "NATS_SINKS_MYSQL_HOST": "127.0.0.1",
            "NATS_SINKS_MYSQL_PORT": str(host_port),
            "NATS_SINKS_MYSQL_DATABASE": "nats_sinks_test",
            "NATS_SINKS_MYSQL_USER": "nats_sinks_test",
            "NATS_SINKS_MYSQL_PASSWORD_ENV": "NATS_SINKS_MYSQL_PASSWORD",
            "NATS_SINKS_MYSQL_PASSWORD": app_password,
            "NATS_SINKS_MYSQL_TABLE": table,
            "NATS_SINKS_MYSQL_DROP_TABLE_BEFORE": "true",
        }
    )
    return env


def _run_pytest(smoke: ModuleType, *, host_port: int, app_password: str, table: str) -> None:
    """Run the focused Oracle MySQL sink integration test."""

    smoke.run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_mysql_sink.py",
            "-q",
        ],
        env=_pytest_env(host_port=host_port, app_password=app_password, table=table),
        timeout=240,
        secrets_to_redact=(app_password,),
    )


def main() -> int:
    """Build the container, run Oracle MySQL sink e2e, and clean up by default."""

    smoke = _load_smoke_module()
    args = parse_args()
    suffix = smoke.random_suffix()
    container_name = f"nats-sinks-oracle-mysql-sink-e2e-{suffix}"
    volume_name = f"nats-sinks-oracle-mysql-sink-e2e-{suffix}"
    secret_dir = args.secret_dir.resolve() / suffix
    root_password = smoke.generate_password()
    app_password = smoke.generate_password()
    secrets_to_redact = (root_password, app_password)

    try:
        smoke.validate_args(args)
        smoke.write_secret(secret_dir / "root-password", root_password)
        smoke.write_secret(secret_dir / "app-password", app_password)
        host_port = smoke.find_free_port()
        smoke.run_command(["docker", "version"], timeout=30, secrets_to_redact=secrets_to_redact)
        smoke.run_command(
            [
                "docker",
                "build",
                "-t",
                args.image_tag,
                "-f",
                str(args.dockerfile.resolve()),
                ".",
            ],
            timeout=900,
            secrets_to_redact=secrets_to_redact,
        )
        smoke.run_command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev",  # noqa: S108 - Docker tmpfs mount.
                "--tmpfs",
                "/run/mysqld:rw,nosuid,nodev",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "DAC_OVERRIDE",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "SETGID",
                "--cap-add",
                "SETUID",
                "--security-opt",
                "no-new-privileges:true",
                "-p",
                f"127.0.0.1:{host_port}:3306",
                "--mount",
                f"type=volume,source={volume_name},target=/var/lib/mysql",
                "--mount",
                (
                    f"type=bind,source={secret_dir / 'root-password'},"
                    "target=/run/secrets/oracle-mysql-root-password,readonly"
                ),
                "--mount",
                (
                    f"type=bind,source={secret_dir / 'app-password'},"
                    "target=/run/secrets/oracle-mysql-app-password,readonly"
                ),
                args.image_tag,
            ],
            timeout=120,
            secrets_to_redact=secrets_to_redact,
        )
        smoke.wait_for_oracle_mysql(
            container_name=container_name,
            app_password=app_password,
            timeout_seconds=args.timeout_seconds,
            secrets_to_redact=secrets_to_redact,
        )
        _run_pytest(
            smoke,
            host_port=host_port,
            app_password=app_password,
            table=args.table,
        )
        sys.stdout.write("Oracle MySQL sink container e2e test passed.\n")
        return 0
    except (OracleMySqlSinkE2eError, smoke.OracleMySqlSmokeError, subprocess.TimeoutExpired) as exc:
        safe_error = smoke.redact(str(exc), secrets_to_redact)
        sys.stderr.write(f"Oracle MySQL sink e2e test failed: {safe_error}\n")
        return 1
    finally:
        smoke.cleanup(container_name, volume_name, secret_dir, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
