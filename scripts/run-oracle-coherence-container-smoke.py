#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Build and smoke-test the Oracle Coherence Community Edition test backend.

The script is intentionally test-only. It starts a fresh short-lived Oracle
Coherence Community Edition container, waits for the local gRPC endpoint, writes
one complete fake event JSON object as the value of a key/value entry, reads it
back, and removes the container by default.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = REPO_ROOT / "examples" / "oracle-coherence-ce-test" / "Dockerfile"
DEFAULT_IMAGE_TAG = "nats-sinks-oracle-coherence-ce-test:local"
DEFAULT_CACHE_NAME = "nats_sinks_smoke_events"
DEFAULT_CONTAINER_GRPC_PORT = 1408
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 900
FAILED_OUTPUT_TAIL_CHARS = 4000
CACHE_NAME_MAX_LENGTH = 64
CACHE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
OFFICIAL_COHERENCE_CE_IMAGE = "ghcr.io/oracle/coherence-ce:25.03.1"

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
logging.getLogger("coherence").setLevel(logging.WARNING)


class OracleCoherenceSmokeError(RuntimeError):
    """Raised when the Oracle Coherence CE smoke test cannot complete."""


def parse_args() -> argparse.Namespace:
    """Parse bounded smoke-test arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-tag",
        default=DEFAULT_IMAGE_TAG,
        help="Local image tag used for the Oracle Coherence CE test backend image.",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=DEFAULT_DOCKERFILE,
        help="Dockerfile used to build the Oracle Coherence CE test backend image.",
    )
    parser.add_argument(
        "--cache-name",
        default=DEFAULT_CACHE_NAME,
        help="Named cache used for the smoke-test JSON key/value record.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for Oracle Coherence CE readiness.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container for debugging.",
    )
    return parser.parse_args()


def validate_cache_name(value: str) -> None:
    """Reject cache names that are unsafe for a local smoke-test backend."""

    if not value:
        raise OracleCoherenceSmokeError("Cache name must not be empty.")
    if len(value) > CACHE_NAME_MAX_LENGTH:
        raise OracleCoherenceSmokeError("Cache name is longer than the supported limit.")
    if value[0] in ".-_" or value[-1] in ".-_":
        raise OracleCoherenceSmokeError("Cache name must start and end with an alphanumeric.")
    if not set(value) <= CACHE_NAME_CHARS:
        raise OracleCoherenceSmokeError("Cache name contains unsupported characters.")


def validate_args(args: argparse.Namespace) -> None:
    """Reject unbounded or unsafe smoke-test options before Docker is called."""

    dockerfile = args.dockerfile.resolve()
    if not dockerfile.is_file():
        message = f"Oracle Coherence CE Dockerfile does not exist: {dockerfile}"
        raise OracleCoherenceSmokeError(message)
    try:
        dockerfile.relative_to(REPO_ROOT)
    except ValueError as exc:
        message = "Oracle Coherence CE Dockerfile must stay inside the repository."
        raise OracleCoherenceSmokeError(message) from exc

    validate_cache_name(args.cache_name)

    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise OracleCoherenceSmokeError("--timeout-seconds must be between 30 and 900.")


def random_suffix() -> str:
    """Return a compact random suffix for short-lived Docker object names."""

    return secrets.token_hex(8)


def redact(value: str, sensitive_values: tuple[str, ...]) -> str:
    """Redact generated local identifiers from operator-facing error text."""

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
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        stdout = redact(completed.stdout[-FAILED_OUTPUT_TAIL_CHARS:], sensitive_values)
        stderr = redact(completed.stderr[-FAILED_OUTPUT_TAIL_CHARS:], sensitive_values)
        safe_args = " ".join(redact(part, sensitive_values) for part in args)
        raise OracleCoherenceSmokeError(
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
        raise OracleCoherenceSmokeError("Unable to allocate a local loopback port.") from exc


def wait_for_tcp_port(*, port: int, timeout_seconds: float) -> None:
    """Wait until the local Coherence gRPC port accepts TCP connections."""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return
        except OSError as exc:
            last_error = str(exc)
            time.sleep(1)
    message = f"Oracle Coherence CE did not open its local port: {last_error}"
    raise OracleCoherenceSmokeError(message)


def smoke_event_value() -> dict[str, Any]:
    """Return the complete fake event JSON object used for smoke verification."""

    return {
        "schema_version": 1,
        "source": "nats-sinks-oracle-coherence-ce-smoke",
        "subject": "example.coherence.smoke",
        "payload": {
            "kind": "fake-event",
            "sequence": 1,
            "body": {"status": "ok", "storage": "json-value"},
        },
        "metadata": {
            "priority": "normal",
            "classification": "NATO UNCLASSIFIED",
            "labels": ["coherence-smoke", "local-test"],
        },
    }


def require_coherence_client() -> tuple[Any, Any]:
    """Import the optional Coherence Python client with a clear local error."""

    try:
        coherence = importlib.import_module("coherence")
    except ModuleNotFoundError as exc:
        raise OracleCoherenceSmokeError(
            "The optional coherence-client package is required for this smoke test. "
            "Install it in an isolated local test environment before running the "
            "Docker smoke test."
        ) from exc

    logging.getLogger("coherence").setLevel(logging.WARNING)
    try:
        return coherence.Options, coherence.Session
    except AttributeError as exc:
        raise OracleCoherenceSmokeError(
            "The installed coherence-client package does not expose the expected API."
        ) from exc


async def _create_session(session_class: Any, options: Any) -> Any:
    """Create a Coherence session across compatible client API shapes."""

    create = getattr(session_class, "create", None)
    if create is not None:
        try:
            return await create(options)
        except TypeError:
            return await create(session_options=options)
    return session_class(options)


async def _verify_json_value_async(*, address: str, cache_name: str, key: str) -> None:
    """Write and read the fake event JSON object through the Coherence client."""

    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        options_class, session_class = require_coherence_client()
        options = options_class(
            address=address,
            request_timeout_seconds=10.0,
            ready_timeout_seconds=10.0,
        )
        session = await _create_session(session_class, options)
        try:
            cache = await session.get_cache(cache_name)
            expected = smoke_event_value()
            await cache.put(key, expected)
            actual = await cache.get(key)
            if actual != expected:
                raise OracleCoherenceSmokeError(
                    "Oracle Coherence CE JSON key/value verification returned unexpected data."
                )
            await cache.remove(key)
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
    finally:
        logging.disable(previous_logging_disable)


def verify_json_value(*, host_port: int, cache_name: str, suffix: str) -> None:
    """Verify one complete JSON object through the local Coherence backend."""

    key = f"nats-sinks-smoke-{suffix}"
    asyncio.run(
        _verify_json_value_async(
            address=f"127.0.0.1:{host_port}",
            cache_name=cache_name,
            key=key,
        )
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
    except OracleCoherenceSmokeError:
        pass


def docker_run_args(*, container_name: str, host_port: int, image_tag: str) -> list[str]:
    """Build the hardened Docker run argv for the Coherence CE backend."""

    return [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev",  # noqa: S108 - Docker tmpfs mount.
        "--tmpfs",
        "/logs:rw,nosuid,nodev",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-p",
        f"127.0.0.1:{host_port}:{DEFAULT_CONTAINER_GRPC_PORT}",
        image_tag,
    ]


def main() -> int:
    """Execute the Oracle Coherence Community Edition container smoke test."""

    args = parse_args()
    suffix = random_suffix()
    container_name = f"nats-sinks-oracle-coherence-ce-test-{suffix}"
    sensitive_values = (container_name,)

    try:
        validate_args(args)
        require_coherence_client()
        host_port = find_free_port()

        run_command(["docker", "version"], timeout=30, sensitive_values=sensitive_values)
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
            sensitive_values=sensitive_values,
        )
        run_command(
            docker_run_args(
                container_name=container_name,
                host_port=host_port,
                image_tag=args.image_tag,
            ),
            timeout=120,
            sensitive_values=sensitive_values,
        )
        wait_for_tcp_port(port=host_port, timeout_seconds=args.timeout_seconds)
        verify_json_value(host_port=host_port, cache_name=args.cache_name, suffix=suffix)
        message = (
            "Oracle Coherence CE container smoke test passed with one "
            "verified JSON key/value entry.\n"
        )
        sys.stdout.write(message)
        return 0
    except (OracleCoherenceSmokeError, subprocess.TimeoutExpired) as exc:
        safe_error = redact(str(exc), sensitive_values)
        sys.stderr.write(f"Oracle Coherence CE container smoke test failed: {safe_error}\n")
        return 1
    finally:
        cleanup(container_name, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
