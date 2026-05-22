#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run an optional local NATS WebSocket end-to-end certification check.

The script starts its own temporary `nats-server` process with WebSocket and
JetStream enabled on loopback-only ports.  It never stops or modifies an
already-running NATS process; when default ports are busy, it selects free
alternatives and records those selected ports in sanitized output.

This is intentionally an opt-in live test because it requires the external
`nats-server` binary.  Unit tests cover the harness helpers without starting
network services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.file import FileSink
from nats_sinks.testing import (
    choose_websocket_harness_ports,
    nats_server_command,
    sanitized_selected_ports,
    wait_for_tcp_port,
    write_nats_websocket_config,
)

STREAM = "NATS_SINKS_WEBSOCKET_E2E"
SUBJECT = "nats.sinks.websocket.e2e"
CONSUMER = "nats-sinks-websocket-e2e"


def _positive_int(value: str) -> int:
    rendered = int(value)
    if rendered < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return rendered


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local NATS WebSocket to FileSink end-to-end test.",
    )
    parser.add_argument("--message-count", type=_positive_int, default=16)
    parser.add_argument("--batch-size", type=_positive_int, default=8)
    parser.add_argument("--timeout-seconds", type=_positive_int, default=30)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".local") / "websocket-e2e",
        help="Ignored local directory used for temporary config, JetStream, and output files.",
    )
    parser.add_argument(
        "--preserve-work-dir",
        action="store_true",
        help="Keep generated local test files after the run for manual inspection.",
    )
    parser.add_argument(
        "--nats-server",
        default="nats-server",
        help="nats-server executable name or path.",
    )
    return parser.parse_args()


def _json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _resolve_nats_server(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved is None:
        msg = (
            f"nats-server executable {executable!r} was not found. "
            "Install NATS Server to run the optional WebSocket e2e test."
        )
        raise FileNotFoundError(msg)
    return resolved


async def _connect_with_retries(url: str, *, timeout_seconds: int) -> Any:
    import nats  # noqa: PLC0415 - optional live-test dependency path.

    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            return await nats.connect(
                servers=[url],
                name="nats-sinks-websocket-e2e",
                connect_timeout=2,
                reconnect_time_wait=1,
                max_reconnect_attempts=2,
            )
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    if last_error is not None:
        raise RuntimeError("failed to connect to local WebSocket NATS server") from last_error
    raise RuntimeError("failed to connect to local WebSocket NATS server")


async def _run_nats_flow(
    *,
    websocket_url: str,
    output_dir: Path,
    message_count: int,
    batch_size: int,
    timeout_seconds: int,
) -> dict[str, object]:
    from nats.js.api import StreamConfig  # noqa: PLC0415 - optional live-test path.

    nc = await _connect_with_retries(websocket_url, timeout_seconds=timeout_seconds)
    try:
        js = nc.jetstream()
        await js.add_stream(StreamConfig(name=STREAM, subjects=[SUBJECT]))
        for index in range(1, message_count + 1):
            payload = json.dumps(
                {
                    "transport": "websocket",
                    "message_index": index,
                    "run_id": "local-websocket-e2e",
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            await js.publish(
                SUBJECT,
                payload,
                headers={"Nats-Msg-Id": f"nats-sinks-websocket-e2e-{index}"},
            )

        subscription = await js.pull_subscribe(SUBJECT, durable=CONSUMER, stream=STREAM)
        sink = FileSink(directory=output_dir, fsync=False, partition_by_subject=False)
        runner = JetStreamSinkRunner(
            nats_url=websocket_url,
            stream=STREAM,
            consumer=CONSUMER,
            subject=SUBJECT,
            sink=sink,
            jetstream=js,
            nats_connection=nc,
        )
        await sink.start()
        processed = 0
        deadline = time.monotonic() + timeout_seconds
        while processed < message_count:
            remaining_timeout = max(deadline - time.monotonic(), 0.1)
            requested = min(batch_size, message_count - processed)
            raw_messages = await subscription.fetch(requested, timeout=remaining_timeout)
            if not raw_messages:
                continue
            await runner.process_raw_batch(raw_messages)
            processed += len(raw_messages)
        await nc.flush(timeout=2)
        await sink.stop()
        files = _json_files(output_dir)
        return {
            "messages_published": message_count,
            "messages_processed": processed,
            "files_written": len(files),
            "commit_then_ack": "verified by JetStreamSinkRunner.process_raw_batch",
        }
    finally:
        await nc.close()


async def _main() -> int:
    args = _parse_args()
    nats_server = _resolve_nats_server(args.nats_server)
    run_dir = args.work_dir.expanduser().resolve() / f"run-{uuid.uuid4().hex}"
    output_dir = run_dir / "files"
    ports = choose_websocket_harness_ports()
    harness = write_nats_websocket_config(run_dir, ports=ports)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "nats-server.log"
    command = nats_server_command(harness.config_path, executable=nats_server)
    _write_line(json.dumps(sanitized_selected_ports(harness), sort_keys=True))

    process: asyncio.subprocess.Process | None = None
    try:
        with log_path.open("wb") as server_log:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=server_log,
                stderr=asyncio.subprocess.STDOUT,
                cwd=run_dir,
            )
            await wait_for_tcp_port(
                "127.0.0.1",
                harness.ports.websocket,
                timeout_seconds=args.timeout_seconds,
            )
            result = await _run_nats_flow(
                websocket_url=harness.websocket_url,
                output_dir=output_dir,
                message_count=args.message_count,
                batch_size=args.batch_size,
                timeout_seconds=args.timeout_seconds,
            )
        _write_line(json.dumps(result, sort_keys=True))
        if result["files_written"] != args.message_count:
            _write_line(
                json.dumps(
                    {
                        "status": "failed",
                        "reason": "written file count did not match message count",
                    },
                    sort_keys=True,
                )
            )
            return 1
        _write_line(json.dumps({"status": "passed"}, sort_keys=True))
        return 0
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
        if not args.preserve_work_dir:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from exc
