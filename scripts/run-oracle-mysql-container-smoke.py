#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Build and smoke-test the local Oracle MySQL test database container.

The script is intentionally test-only. It starts a fresh short-lived Oracle
MySQL container with random credentials for each run, verifies basic database
read/write behavior, and removes the container, volume, and generated secret
files by default. It does not implement or exercise the future Oracle MySQL
sink.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = REPO_ROOT / "examples" / "oracle-mysql-test" / "Dockerfile"
DEFAULT_SECRET_DIR = REPO_ROOT / ".local" / "oracle-mysql-test"
DEFAULT_IMAGE_TAG = "nats-sinks-oracle-mysql-test:local"
DEFAULT_DATABASE = "nats_sinks_test"
DEFAULT_USER = "nats_sinks_test"
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 900
FAILED_OUTPUT_TAIL_CHARS = 4000
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "_-"
PASSWORD_LENGTH = 40


class OracleMySqlSmokeError(RuntimeError):
    """Raised when the Oracle MySQL container smoke test cannot complete."""


def parse_args() -> argparse.Namespace:
    """Parse bounded smoke-test arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-tag",
        default=DEFAULT_IMAGE_TAG,
        help="Local image tag used for the Oracle MySQL test database image.",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=DEFAULT_DOCKERFILE,
        help="Dockerfile used to build the Oracle MySQL test database image.",
    )
    parser.add_argument(
        "--secret-dir",
        type=Path,
        default=DEFAULT_SECRET_DIR,
        help="Local ignored directory used for generated short-lived secret files.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for Oracle MySQL readiness.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container, volume, and generated secret files for debugging.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Reject unbounded or unsafe smoke-test options before Docker is called."""

    dockerfile = args.dockerfile.resolve()
    if not dockerfile.is_file():
        raise OracleMySqlSmokeError(f"Oracle MySQL Dockerfile does not exist: {dockerfile}")
    try:
        dockerfile.relative_to(REPO_ROOT)
    except ValueError as exc:
        message = "Oracle MySQL Dockerfile must stay inside the repository."
        raise OracleMySqlSmokeError(message) from exc

    secret_dir = args.secret_dir.resolve()
    try:
        secret_dir.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise OracleMySqlSmokeError("Secret directory must stay inside the repository.") from exc

    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise OracleMySqlSmokeError("--timeout-seconds must be between 30 and 900.")


def generate_password() -> str:
    """Generate a shell-safe random Oracle MySQL test password."""

    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def random_suffix() -> str:
    """Return a compact random suffix for short-lived Docker object names."""

    return secrets.token_hex(8)


def write_secret(path: Path, value: str) -> None:
    """Write a generated secret file with restrictive permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)


def redact(value: str, secrets_to_redact: tuple[str, ...]) -> str:
    """Redact generated secrets from operator-facing error text."""

    redacted = value
    for secret in secrets_to_redact:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def run_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    secrets_to_redact: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a fixed-argument subprocess without a shell."""

    completed = subprocess.run(  # noqa: S603 - fixed Docker argv lists are assembled by this script.
        args,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        stdout = redact(completed.stdout[-FAILED_OUTPUT_TAIL_CHARS:], secrets_to_redact)
        stderr = redact(completed.stderr[-FAILED_OUTPUT_TAIL_CHARS:], secrets_to_redact)
        safe_args = " ".join(redact(part, secrets_to_redact) for part in args)
        raise OracleMySqlSmokeError(
            f"Command failed with exit code {completed.returncode}: {safe_args}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    return completed


def find_free_port() -> int:
    """Ask the operating system for a free localhost port."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    except OSError as exc:
        raise OracleMySqlSmokeError("Unable to allocate a local loopback port.") from exc


def docker_env(app_password: str) -> dict[str, str]:
    """Build the minimal environment used by Docker CLI subprocesses."""

    env = os.environ.copy()
    env["MYSQL_PWD"] = app_password
    return env


def wait_for_oracle_mysql(
    *,
    container_name: str,
    app_password: str,
    timeout_seconds: float,
    secrets_to_redact: tuple[str, ...],
) -> None:
    """Wait until the Oracle MySQL test account can connect over TCP."""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    command = [
        "docker",
        "exec",
        "--env",
        "MYSQL_PWD",
        container_name,
        "mysqladmin",
        "ping",
        "-h",
        "127.0.0.1",
        "-P",
        "3306",
        "-u",
        DEFAULT_USER,
    ]
    while time.monotonic() < deadline:
        completed = subprocess.run(  # noqa: S603 - fixed Docker argv list.
            command,
            check=False,
            cwd=REPO_ROOT,
            env=docker_env(app_password),
            text=True,
            capture_output=True,
            timeout=20,
            shell=False,
        )
        if completed.returncode == 0:
            return
        last_error = redact(completed.stderr[-500:] or completed.stdout[-500:], secrets_to_redact)
        time.sleep(2)
    raise OracleMySqlSmokeError(f"Oracle MySQL did not become ready: {last_error}")


def verify_oracle_mysql_record(
    *,
    container_name: str,
    app_password: str,
    secrets_to_redact: tuple[str, ...],
) -> None:
    """Create a table, insert one record, and read it back."""

    sql = (
        "CREATE TABLE IF NOT EXISTS smoke_test "
        "(id INT PRIMARY KEY, payload VARCHAR(64) NOT NULL); "
        "REPLACE INTO smoke_test (id, payload) VALUES (1, 'oracle-mysql-smoke-ok'); "
        "SELECT payload FROM smoke_test WHERE id = 1;"
    )
    completed = run_command(
        [
            "docker",
            "exec",
            "--env",
            "MYSQL_PWD",
            container_name,
            "mysql",
            "-h",
            "127.0.0.1",
            "-P",
            "3306",
            "-u",
            DEFAULT_USER,
            "--database",
            DEFAULT_DATABASE,
            "--batch",
            "--raw",
            "--skip-column-names",
            "-e",
            sql,
        ],
        env=docker_env(app_password),
        timeout=60,
        secrets_to_redact=secrets_to_redact,
    )
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if values[-1:] != ["oracle-mysql-smoke-ok"]:
        raise OracleMySqlSmokeError("Oracle MySQL smoke query did not return the expected value.")


def cleanup(container_name: str, volume_name: str, secret_dir: Path, *, preserve: bool) -> None:
    """Remove short-lived Docker and secret artifacts unless preservation is requested."""

    if preserve:
        return
    for command in (
        ["docker", "rm", "-f", container_name],
        ["docker", "volume", "rm", "-f", volume_name],
    ):
        try:
            run_command(command, timeout=60)
        except OracleMySqlSmokeError:
            pass
    if secret_dir.exists():
        shutil.rmtree(secret_dir)


def main() -> int:
    """Execute the Oracle MySQL container smoke test."""

    args = parse_args()
    suffix = random_suffix()
    container_name = f"nats-sinks-oracle-mysql-test-{suffix}"
    volume_name = f"nats-sinks-oracle-mysql-test-{suffix}"
    secret_dir = args.secret_dir.resolve() / suffix
    root_password = generate_password()
    app_password = generate_password()
    secrets_to_redact = (root_password, app_password)

    try:
        validate_args(args)
        write_secret(secret_dir / "root-password", root_password)
        write_secret(secret_dir / "app-password", app_password)
        host_port = find_free_port()

        run_command(["docker", "version"], timeout=30, secrets_to_redact=secrets_to_redact)
        run_command(
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
        run_command(
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
        wait_for_oracle_mysql(
            container_name=container_name,
            app_password=app_password,
            timeout_seconds=args.timeout_seconds,
            secrets_to_redact=secrets_to_redact,
        )
        verify_oracle_mysql_record(
            container_name=container_name,
            app_password=app_password,
            secrets_to_redact=secrets_to_redact,
        )
        sys.stdout.write(
            "Oracle MySQL container smoke test passed with one verified test record.\n"
        )
        return 0
    except (OracleMySqlSmokeError, subprocess.TimeoutExpired) as exc:
        safe_error = redact(str(exc), secrets_to_redact)
        sys.stderr.write(f"Oracle MySQL container smoke test failed: {safe_error}\n")
        return 1
    finally:
        cleanup(container_name, volume_name, secret_dir, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
