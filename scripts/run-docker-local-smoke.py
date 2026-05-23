#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the local Docker/NATS/file-sink smoke test.

The script intentionally keeps orchestration in Python instead of shell glue so
inputs can be bounded, subprocesses can be called without a shell, and command
failures can be reported with clear operator-facing messages. It is a developer
smoke test, not a production deployment tool.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import nats

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "examples" / "docker-local" / "compose.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".local" / "docker-file-sink"
MIN_MESSAGE_COUNT = 1
MAX_MESSAGE_COUNT = 10_000
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 600
FAILED_OUTPUT_TAIL_CHARS = 4000


class SmokeTestError(RuntimeError):
    """Raised when the local Docker smoke test cannot complete safely."""


def parse_args() -> argparse.Namespace:
    """Parse bounded command-line arguments for the smoke test."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message-count",
        type=int,
        default=8,
        help="Number of test messages to publish before starting the sink.",
    )
    parser.add_argument(
        "--image-tag",
        default="nats-sinks:local",
        help="Local Docker image tag used by the Compose stack.",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help="Compose JSON file to use for the local stack.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Host directory where the file sink writes test output.",
    )
    parser.add_argument(
        "--project-name",
        default="nats-sinks-local-smoke",
        help="Docker Compose project name used to isolate this test run.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=45.0,
        help="Maximum time to wait for NATS and sink output.",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Leave the Compose stack running for manual inspection.",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="Preserve generated file-sink output after a successful run.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsafe or unbounded smoke-test inputs before doing work."""

    if args.message_count < MIN_MESSAGE_COUNT or args.message_count > MAX_MESSAGE_COUNT:
        raise SmokeTestError("--message-count must be between 1 and 10000.")
    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise SmokeTestError("--timeout-seconds must be between 5 and 600.")

    compose_file = args.compose_file.resolve()
    if not compose_file.is_file():
        raise SmokeTestError(f"Compose file does not exist: {compose_file}")

    output_dir = args.output_dir.resolve()
    try:
        output_dir.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise SmokeTestError("Output directory must stay inside the repository.") from exc


def run_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with shell disabled and bounded output capture."""

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
        stderr = completed.stderr[-FAILED_OUTPUT_TAIL_CHARS:]
        stdout = completed.stdout[-FAILED_OUTPUT_TAIL_CHARS:]
        raise SmokeTestError(
            f"Command failed with exit code {completed.returncode}: {' '.join(args)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    return completed


def find_free_port() -> int:
    """Ask the OS for a free localhost port to avoid developer collisions."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    except OSError as exc:
        raise SmokeTestError(
            "Unable to allocate a local loopback port for the Docker smoke test. "
            "Check local sandbox, firewall, or operating-system socket restrictions."
        ) from exc


async def wait_for_nats(url: str, timeout_seconds: float) -> None:
    """Wait until the temporary NATS server accepts client connections."""

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            nc = await nats.connect(servers=[url], connect_timeout=1)
            await nc.close()
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    raise SmokeTestError(f"NATS did not become ready at {url}: {last_error}") from last_error


async def seed_stream(url: str, message_count: int) -> None:
    """Create a clean ORDERS stream and publish deterministic test messages."""

    nc = await nats.connect(servers=[url], connect_timeout=3)
    try:
        js = nc.jetstream()
        with contextlib.suppress(Exception):
            await js.delete_stream("ORDERS")
        await js.add_stream(name="ORDERS", subjects=["orders.*"])
        for sequence in range(1, message_count + 1):
            payload: dict[str, Any] = {
                "order_id": f"DOCKER-{sequence:05d}",
                "amount": sequence,
                "source": "docker-local-smoke",
            }
            headers = {
                "Nats-Msg-Id": f"docker-local-smoke-{sequence:05d}",
                "Nats-Sinks-Priority": "normal",
                "Nats-Sinks-Classification": "NATO UNCLASSIFIED",
                "Nats-Sinks-Labels": "docker-local;smoke-test",
            }
            await js.publish(
                "orders.created",
                json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
    finally:
        await nc.drain()


def count_output_files(output_dir: Path) -> int:
    """Count JSON files emitted by the file sink in the mounted output tree."""

    if not output_dir.exists():
        return 0
    return sum(1 for path in output_dir.rglob("*.json") if path.is_file())


def wait_for_output(output_dir: Path, expected_count: int, timeout_seconds: float) -> None:
    """Wait until the file sink has persisted the expected number of files."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if count_output_files(output_dir) >= expected_count:
            return
        time.sleep(0.5)
    raise SmokeTestError(
        f"Timed out waiting for {expected_count} file-sink output file(s); "
        f"found {count_output_files(output_dir)}."
    )


def compose_env(args: argparse.Namespace, nats_port: int, monitor_port: int) -> dict[str, str]:
    """Build the sanitized environment passed to Docker Compose."""

    env = os.environ.copy()
    env["NATS_SINKS_IMAGE"] = args.image_tag
    env["NATS_SINKS_NATS_PORT"] = str(nats_port)
    env["NATS_SINKS_NATS_MONITOR_PORT"] = str(monitor_port)
    env["NATS_SINKS_DOCKER_OUTPUT_DIR"] = str(args.output_dir.resolve())
    return env


def main() -> int:
    """Execute the local Docker smoke test and print a concise result."""

    args = parse_args()
    try:
        validate_args(args)
        output_dir = args.output_dir.resolve()
        if output_dir.exists() and not args.keep_output:
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        nats_port = find_free_port()
        monitor_port = find_free_port()
        nats_url = f"nats://127.0.0.1:{nats_port}"
        env = compose_env(args, nats_port, monitor_port)
        compose = [
            "docker",
            "compose",
            "-f",
            str(args.compose_file.resolve()),
            "-p",
            args.project_name,
        ]

        run_command(["docker", "version"], timeout=30)
        run_command(["docker", "build", "-t", args.image_tag, "."], timeout=300)
        try:
            run_command([*compose, "up", "-d", "nats"], env=env, timeout=120)
            asyncio.run(wait_for_nats(nats_url, args.timeout_seconds))
            asyncio.run(seed_stream(nats_url, args.message_count))
            run_command([*compose, "up", "-d", "nats-sink"], env=env, timeout=120)
            wait_for_output(output_dir, args.message_count, args.timeout_seconds)
        finally:
            if not args.keep_running:
                with contextlib.suppress(SmokeTestError):
                    run_command([*compose, "down", "--volumes"], env=env, timeout=120)

        sys.stdout.write(
            f"Local Docker smoke test passed: persisted {args.message_count} "
            f"message(s) under {output_dir}.\n"
        )
        if output_dir.exists() and not args.keep_output:
            shutil.rmtree(output_dir)
        return 0
    except SmokeTestError as exc:
        sys.stderr.write(f"Local Docker smoke test failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
