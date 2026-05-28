#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Smoke-test the local Oracle NoSQL Database KVLite test backend.

The script is intentionally test-only. It starts a fresh short-lived Oracle
NoSQL Database Community Edition KVLite container from Oracle's GitHub
Container Registry image, exposes only the HTTP proxy on a loopback-bound
random local port, writes one complete fake event JSON object to a key/value
style table, reads it back, and removes the container by default.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_REF = "ghcr.io/oracle/nosql:latest-ce"
DEFAULT_TABLE = "nats_sinks_nosql_smoke_events"
DEFAULT_KEY_FIELD = "sink_key"
DEFAULT_VALUE_FIELD = "event_json"
DEFAULT_STORED_AT_FIELD = "stored_at_epoch_ns"
DEFAULT_CONTAINER_PROXY_PORT = 8080
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 900
FAILED_OUTPUT_TAIL_CHARS = 4000
SDK_READINESS_RETRY_SECONDS = 2.0
TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
ORACLE_NOSQL_IMAGE_SOURCE = "GitHub Container Registry"
ORACLE_NOSQL_MODE = "non-secure KVLite with HTTP proxy"


class OracleNoSqlContainerSmokeError(RuntimeError):
    """Raised when the Oracle NoSQL Database container smoke test fails."""


def parse_args() -> argparse.Namespace:
    """Parse bounded smoke-test arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-ref",
        default=DEFAULT_IMAGE_REF,
        help="Oracle NoSQL Database container image reference to run.",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help="Oracle NoSQL Database table used for smoke-test JSON verification.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=240.0,
        help="Maximum time to wait for KVLite proxy readiness.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container for debugging.",
    )
    return parser.parse_args()


def validate_table_name(value: str) -> None:
    """Reject table names that are unsafe for generated test DDL."""

    if not TABLE_NAME_RE.fullmatch(value):
        raise OracleNoSqlContainerSmokeError(
            "Oracle NoSQL Database smoke-test table must be one identifier that "
            "starts with a letter and contains only letters, numbers, or underscores."
        )


def validate_args(args: argparse.Namespace) -> None:
    """Reject unbounded or unsafe smoke-test options before Docker is called."""

    if args.image_ref != args.image_ref.strip() or not args.image_ref:
        raise OracleNoSqlContainerSmokeError("--image-ref must not be empty or padded.")
    if any(character.isspace() for character in args.image_ref):
        raise OracleNoSqlContainerSmokeError("--image-ref must not contain whitespace.")
    validate_table_name(args.table)
    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise OracleNoSqlContainerSmokeError("--timeout-seconds must be between 30 and 900.")


def random_suffix() -> str:
    """Return a compact random suffix for short-lived Docker object names."""

    return secrets.token_hex(8)


def redact(value: str, sensitive_values: tuple[str, ...]) -> str:
    """Redact local generated identifiers from operator-facing error text."""

    redacted = value
    for sensitive in sensitive_values:
        if sensitive:
            redacted = redacted.replace(sensitive, "<redacted>")
    return redacted


def run_command(
    args: list[str],
    *,
    timeout: float = 120.0,
    sensitive_values: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a fixed-argument subprocess without a shell."""

    completed = subprocess.run(  # noqa: S603 - fixed Docker argv lists are assembled here.
        args,
        check=False,
        cwd=REPO_ROOT,
        env=None,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        stdout = redact(completed.stdout[-FAILED_OUTPUT_TAIL_CHARS:], sensitive_values)
        stderr = redact(completed.stderr[-FAILED_OUTPUT_TAIL_CHARS:], sensitive_values)
        safe_args = " ".join(redact(part, sensitive_values) for part in args)
        raise OracleNoSqlContainerSmokeError(
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
        raise OracleNoSqlContainerSmokeError("Unable to allocate a local loopback port.") from exc


def wait_for_tcp_port(*, port: int, timeout_seconds: float) -> None:
    """Wait until the local Oracle NoSQL proxy accepts TCP connections."""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return
        except OSError as exc:
            last_error = str(exc)
            time.sleep(1)
    raise OracleNoSqlContainerSmokeError(
        f"Oracle NoSQL Database proxy did not open its local port: {last_error}"
    )


def docker_run_args(*, container_name: str, host_port: int, image_ref: str) -> list[str]:
    """Build the Docker run argv for the Oracle NoSQL Database KVLite backend."""

    return [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--hostname",
        container_name,
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev",  # noqa: S108 - Docker tmpfs mount.
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--env",
        f"KV_PROXY_PORT={DEFAULT_CONTAINER_PROXY_PORT}",
        "-p",
        f"127.0.0.1:{host_port}:{DEFAULT_CONTAINER_PROXY_PORT}",
        image_ref,
    ]


def smoke_event_value() -> dict[str, Any]:
    """Return the complete fake event JSON object used for smoke verification."""

    return {
        "schema": "nats_sinks.oracle_nosql.container_smoke.v1",
        "schema_version": 1,
        "source": "nats-sinks-oracle-nosql-container-smoke",
        "subject": "example.oracle_nosql.smoke",
        "payload": {
            "kind": "fake-event",
            "sequence": 1,
            "body": {"status": "ok", "storage": "json-value"},
        },
        "metadata": {
            "priority": "normal",
            "classification": "NATO UNCLASSIFIED",
            "labels": ["oracle-nosql-smoke", "local-test"],
        },
    }


def require_borneo() -> Any:
    """Import the optional Oracle NoSQL Python SDK with a clear local error."""

    try:
        return importlib.import_module("borneo")
    except ModuleNotFoundError as exc:
        raise OracleNoSqlContainerSmokeError(
            "The optional borneo package is required for this smoke test. "
            "Install nats-sinks[oracle-nosql] in an isolated local test "
            "environment before running the Docker smoke test."
        ) from exc


def _nosql_handle(*, endpoint: str) -> Any:
    """Create an Oracle NoSQL handle for a non-secure local KVLite proxy."""

    borneo = require_borneo()
    kv_module = importlib.import_module("borneo.kv")
    provider = kv_module.StoreAccessTokenProvider()
    try:
        handle_config = borneo.NoSQLHandleConfig(endpoint, provider)
    except TypeError:
        handle_config = borneo.NoSQLHandleConfig(endpoint)
        handle_config.set_authorization_provider(provider)
    return borneo.NoSQLHandle(handle_config)


def _put_result_succeeded(result: Any) -> bool:
    """Interpret SDK put results without treating ambiguity as success."""

    if isinstance(result, bool):
        return result
    for method_name in ("get_success", "is_success"):
        method = getattr(result, method_name, None)
        if callable(method):
            return bool(method())
    get_version = getattr(result, "get_version", None)
    if callable(get_version):
        return get_version() is not None
    version = getattr(result, "version", None)
    return version is not None


def _wait_for_table(handle: Any, table_request_result: Any, borneo: Any) -> None:
    """Wait for table creation across compatible SDK API shapes."""

    wait = getattr(table_request_result, "wait_for_completion", None)
    if callable(wait):
        wait(handle, 50_000, 3_000)
        return
    table_request = getattr(handle, "do_table_request", None)
    if callable(table_request):
        table_request(table_request_result, 50_000, 3_000)
        return
    _ = borneo


def verify_json_value(*, endpoint: str, table_name: str, suffix: str) -> None:
    """Create a table, write one fake JSON value, and read it back."""

    borneo = require_borneo()
    handle = _nosql_handle(endpoint=endpoint)
    key = f"nats-sinks-nosql-smoke-{suffix}"
    stored_at = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
    row = {
        DEFAULT_KEY_FIELD: key,
        DEFAULT_VALUE_FIELD: smoke_event_value(),
        DEFAULT_STORED_AT_FIELD: stored_at,
    }
    try:
        statement = (
            f"CREATE TABLE IF NOT EXISTS {table_name} "
            f"({DEFAULT_KEY_FIELD} STRING, "
            f"{DEFAULT_VALUE_FIELD} JSON, "
            f"{DEFAULT_STORED_AT_FIELD} LONG, "
            f"PRIMARY KEY({DEFAULT_KEY_FIELD}))"
        )
        table_request = borneo.TableRequest().set_statement(statement)
        table_call = getattr(handle, "table_request", None)
        if callable(table_call):
            result = table_call(table_request)
            _wait_for_table(handle, result, borneo)
        else:
            handle.do_table_request(table_request, 50_000, 3_000)

        put_request = borneo.PutRequest().set_table_name(table_name).set_value(row)
        put_result = handle.put(put_request)
        if not _put_result_succeeded(put_result):
            raise OracleNoSqlContainerSmokeError(
                "Oracle NoSQL Database smoke put returned no success indicator."
            )

        get_request = (
            borneo.GetRequest().set_table_name(table_name).set_key({DEFAULT_KEY_FIELD: key})
        )
        get_result = handle.get(get_request)
        actual = get_result.get_value()
        if json.loads(json.dumps(actual, sort_keys=True)) != json.loads(
            json.dumps(row, sort_keys=True)
        ):
            raise OracleNoSqlContainerSmokeError(
                "Oracle NoSQL Database JSON key/value verification returned unexpected data."
            )
    finally:
        close = getattr(handle, "close", None)
        if callable(close):
            close()


def wait_for_oracle_nosql_ready(
    *,
    endpoint: str,
    table_name: str,
    suffix: str,
    timeout_seconds: float,
) -> None:
    """Wait until the SDK can create, write, and read the smoke table."""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            verify_json_value(endpoint=endpoint, table_name=table_name, suffix=suffix)
            return
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            time.sleep(SDK_READINESS_RETRY_SECONDS)
    raise OracleNoSqlContainerSmokeError(
        f"Oracle NoSQL Database SDK readiness did not complete before timeout: {last_error}"
    )


def cleanup(container_name: str, *, preserve: bool) -> None:
    """Remove the short-lived Docker container unless preservation is requested."""

    if preserve:
        return
    try:
        run_command(
            ["docker", "rm", "-f", container_name],
            timeout=60,
            sensitive_values=(container_name,),
        )
    except OracleNoSqlContainerSmokeError:
        pass


def main() -> int:
    """Execute the Oracle NoSQL Database container smoke test."""

    args = parse_args()
    suffix = random_suffix()
    container_name = f"nats-sinks-oracle-nosql-test-{suffix}"
    sensitive_values = (container_name,)

    try:
        validate_args(args)
        require_borneo()
        host_port = find_free_port()
        endpoint = f"http://127.0.0.1:{host_port}"

        run_command(["docker", "version"], timeout=30, sensitive_values=sensitive_values)
        run_command(
            ["docker", "pull", args.image_ref],
            timeout=900,
            sensitive_values=sensitive_values,
        )
        run_command(
            docker_run_args(
                container_name=container_name,
                host_port=host_port,
                image_ref=args.image_ref,
            ),
            timeout=120,
            sensitive_values=sensitive_values,
        )
        wait_for_tcp_port(port=host_port, timeout_seconds=args.timeout_seconds)
        wait_for_oracle_nosql_ready(
            endpoint=endpoint,
            table_name=args.table,
            suffix=suffix,
            timeout_seconds=args.timeout_seconds,
        )
        sys.stdout.write(
            "Oracle NoSQL Database container smoke test passed with one "
            "verified JSON key/value entry.\n"
        )
        return 0
    except (OracleNoSqlContainerSmokeError, subprocess.TimeoutExpired) as exc:
        safe_error = redact(str(exc), sensitive_values)
        sys.stderr.write(f"Oracle NoSQL Database container smoke test failed: {safe_error}\n")
        return 1
    finally:
        cleanup(container_name, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
