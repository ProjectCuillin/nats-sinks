#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the HTTP sink e2e test against a short-lived NGINX test endpoint.

The runner builds the local Oracle Linux 9 slim FIPS based NGINX endpoint,
starts it with loopback-only port binding, sends real ``HttpSink`` writes, and
copies the local capture evidence back for validation. It is intentionally
test-only and remains behind the container e2e gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nats_sinks.core.errors import NatsSinksError
from nats_sinks.http import HttpSink, HttpSinkConfig
from nats_sinks.testing import certification_envelope

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = REPO_ROOT / "examples" / "http-nginx-fips-test" / "Dockerfile"
DEFAULT_IMAGE_TAG = "nats-sinks-http-nginx-fips-test:local"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".local" / "http-nginx-fips-e2e"
DEFAULT_CONTAINER_HTTP_PORT = 8080
HTTP_OK = 200
CAPTURE_COPY_RETRY_SECONDS = 10.0
CAPTURE_COPY_RETRY_INTERVAL_SECONDS = 0.5
MIN_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 900.0
MIN_MESSAGE_COUNT = 1
MAX_MESSAGE_COUNT = 100
FAILED_OUTPUT_TAIL_CHARS = 4000
CAPTURE_FILE_IN_CONTAINER = "/var/lib/nats-sinks-http/requests.jsonl"


class HttpNginxE2eError(RuntimeError):
    """Raised when the local HTTP sink NGINX e2e test cannot complete."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse bounded local e2e arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-tag",
        default=DEFAULT_IMAGE_TAG,
        help="Local image tag used for the HTTP sink NGINX test endpoint image.",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=DEFAULT_DOCKERFILE,
        help="Dockerfile used to build the HTTP sink NGINX test endpoint image.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Repository-local directory used for sanitized request evidence.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum time to wait for the local NGINX endpoint.",
    )
    parser.add_argument(
        "--message-count",
        type=int,
        default=3,
        help="Number of fake HTTP sink messages to send through NGINX.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container and copied request evidence for debugging.",
    )
    return parser.parse_args(argv)


def _require_repo_local_path(path: Path, *, label: str) -> Path:
    """Resolve a path and require it to stay inside the repository."""

    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise HttpNginxE2eError(f"{label} must stay inside the repository.") from exc
    return resolved


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsafe local e2e settings before Docker is called."""

    dockerfile = _require_repo_local_path(args.dockerfile, label="Dockerfile")
    if not dockerfile.is_file():
        raise HttpNginxE2eError(f"HTTP sink NGINX Dockerfile does not exist: {dockerfile}")
    output_dir = _require_repo_local_path(args.output_dir, label="Output directory")
    if not str(output_dir).startswith(str((REPO_ROOT / ".local").resolve())):
        raise HttpNginxE2eError("Output directory must be under .local for this local e2e test.")
    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise HttpNginxE2eError("--timeout-seconds must be between 30 and 900.")
    if args.message_count < MIN_MESSAGE_COUNT or args.message_count > MAX_MESSAGE_COUNT:
        raise HttpNginxE2eError("--message-count must be between 1 and 100.")
    if args.image_tag != args.image_tag.strip() or not args.image_tag:
        raise HttpNginxE2eError("--image-tag must not be empty or padded.")
    if any(character.isspace() for character in args.image_tag):
        raise HttpNginxE2eError("--image-tag must not contain whitespace.")


def random_suffix() -> str:
    """Return a compact random suffix for short-lived Docker object names."""

    return secrets.token_hex(8)


def redact(value: str, sensitive_values: tuple[str, ...]) -> str:
    """Redact local generated identifiers from operator-facing output."""

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
        raise HttpNginxE2eError(
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
        raise HttpNginxE2eError("Unable to allocate a local loopback port.") from exc


def wait_for_http_health(*, port: int, timeout_seconds: float) -> None:
    """Wait until the loopback NGINX endpoint serves its health route."""

    deadline = time.monotonic() + timeout_seconds
    health_url = f"http://127.0.0.1:{port}/health"
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:  # noqa: S310 - loopback only.
                if response.status == HTTP_OK:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise HttpNginxE2eError(f"HTTP sink NGINX endpoint was not healthy: {last_error}")


def docker_run_args(*, container_name: str, host_port: int, image_tag: str) -> list[str]:
    """Build the hardened Docker run argv for the HTTP endpoint container."""

    return [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev",  # noqa: S108 - Docker tmpfs mount.
        "--tmpfs",
        "/var/lib/nats-sinks-http:rw,nosuid,nodev,uid=10001,gid=10001,mode=0750",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-p",
        f"127.0.0.1:{host_port}:{DEFAULT_CONTAINER_HTTP_PORT}",
        image_tag,
    ]


def fake_event_payload(sequence: int) -> bytes:
    """Return one fake, non-sensitive JSON payload for HTTP sink verification."""

    return json.dumps(
        {
            "event_id": f"HTTP-NGINX-E2E-{sequence:04d}",
            "kind": "fake-http-sink-event",
            "status": "accepted",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


async def _send_messages_async(*, endpoint_url: str, message_count: int) -> None:
    """Send fake messages through the production HTTP sink."""

    config = HttpSinkConfig.model_validate(
        {
            "type": "http",
            "url": endpoint_url,
            "allow_http_for_local_testing": True,
            "endpoint_allowed_hosts": ["127.0.0.1"],
            "headers": {"X-Nats-Sinks-Route": "http-nginx-e2e"},
            "idempotency": {"strategy": "stream_sequence"},
            "retry": {"max_retries": 0},
            "request_timeout_seconds": 10,
            "max_request_bytes": 1_048_576,
        }
    )
    sink = HttpSink(url=config.url, config=config)
    messages = tuple(
        certification_envelope(
            subject="integration.http.nginx",
            stream="HTTP_NGINX_E2E",
            stream_sequence=sequence,
            message_id=f"http-nginx-e2e-{sequence:04d}",
            data=fake_event_payload(sequence),
            priority="normal",
            classification="unclassified",
            labels=("http", "nginx", "e2e"),
        )
        for sequence in range(1, message_count + 1)
    )
    await sink.start()
    try:
        await sink.write_batch(messages)
    finally:
        await sink.stop()


def send_messages(*, endpoint_url: str, message_count: int) -> None:
    """Send fake messages through the production HTTP sink."""

    asyncio.run(_send_messages_async(endpoint_url=endpoint_url, message_count=message_count))


def copy_capture_file(*, container_name: str, output_file: Path) -> None:
    """Copy the request capture file from the short-lived container."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + CAPTURE_COPY_RETRY_SECONDS
    last_error: HttpNginxE2eError | None = None
    command = ["docker", "exec", container_name, "cat", CAPTURE_FILE_IN_CONTAINER]
    while time.monotonic() <= deadline:
        try:
            completed = run_command(
                command,
                timeout=60,
                sensitive_values=(container_name,),
            )
            if not completed.stdout:
                raise HttpNginxE2eError("Request evidence file was empty.")
            output_file.write_text(completed.stdout, encoding="utf-8")
            return
        except HttpNginxE2eError as exc:
            last_error = exc
            time.sleep(CAPTURE_COPY_RETRY_INTERVAL_SECONDS)
    if last_error is not None:
        raise last_error
    raise HttpNginxE2eError("Request evidence was not ready before the copy deadline.")


def read_capture_records(capture_file: Path) -> list[dict[str, Any]]:
    """Read and parse JSONL request evidence copied from the container."""

    records: list[dict[str, Any]] = []
    with capture_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            records.append(json.loads(line))
    return records


def verify_capture_records(records: list[dict[str, Any]], *, message_count: int) -> None:
    """Verify that the NGINX endpoint received each HTTP sink message."""

    if len(records) != message_count:
        raise HttpNginxE2eError(
            f"Expected {message_count} captured request(s), got {len(records)}."
        )
    for index, record in enumerate(records, start=1):
        headers = record.get("headers")
        if not isinstance(headers, dict):
            raise HttpNginxE2eError("Captured request headers were not an object.")
        body = json.loads(record["body"])
        payload = body["payload"]
        expected_key = f"stream-sequence:HTTP_NGINX_E2E:{index}"
        if record["method"] != "POST":
            raise HttpNginxE2eError("Captured request used an unexpected HTTP method.")
        if record["path"] != "/nats-sink":
            raise HttpNginxE2eError("Captured request used an unexpected path.")
        if headers.get("idempotency-key") != expected_key:
            raise HttpNginxE2eError(
                "Captured request did not include the expected idempotency key."
            )
        if headers.get("x-nats-sinks-route") != "http-nginx-e2e":
            raise HttpNginxE2eError("Captured request did not include the expected route header.")
        if body["schema"] != "nats_sinks.http.message.v1":
            raise HttpNginxE2eError("Captured request used an unexpected HTTP envelope schema.")
        if body["subject"] != "integration.http.nginx":
            raise HttpNginxE2eError("Captured request used an unexpected subject.")
        if payload["event_id"] != f"HTTP-NGINX-E2E-{index:04d}":
            raise HttpNginxE2eError("Captured request payload event_id did not match.")


def cleanup(container_name: str, output_dir: Path, *, preserve: bool) -> None:
    """Remove the short-lived container and copied evidence unless requested."""

    if preserve:
        return
    try:
        run_command(
            ["docker", "rm", "-f", container_name], timeout=60, sensitive_values=(container_name,)
        )
    except HttpNginxE2eError:
        pass
    shutil.rmtree(output_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Build the HTTP endpoint image, run e2e verification, and clean up."""

    args = parse_args(argv)
    suffix = random_suffix()
    container_name = f"nats-sinks-http-nginx-fips-test-{suffix}"
    output_dir = args.output_dir.resolve() / suffix
    capture_file = output_dir / "requests.jsonl"
    sensitive_values = (container_name,)

    try:
        validate_args(args)
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
        wait_for_http_health(port=host_port, timeout_seconds=args.timeout_seconds)
        send_messages(
            endpoint_url=f"http://127.0.0.1:{host_port}/nats-sink",
            message_count=args.message_count,
        )
        copy_capture_file(container_name=container_name, output_file=capture_file)
        verify_capture_records(
            read_capture_records(capture_file),
            message_count=args.message_count,
        )
        sys.stdout.write("HTTP sink NGINX container e2e test passed.\n")
        return 0
    except (
        HttpNginxE2eError,
        NatsSinksError,
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        ValueError,
    ) as exc:
        safe_error = redact(str(exc), sensitive_values)
        sys.stderr.write(f"HTTP sink NGINX container e2e test failed: {safe_error}\n")
        return 1
    finally:
        cleanup(container_name, output_dir, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
