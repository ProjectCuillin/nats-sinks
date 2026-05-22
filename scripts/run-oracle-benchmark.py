#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run a live, sanitized NATS-to-Oracle phase benchmark.

This script is intentionally outside the default unit-test path.  It connects
to real non-production NATS and Oracle services only when
`NATS_SINKS_ORACLE_BENCHMARK=1` is present in the environment.  Connection
details must come from ignored `.local` files or local environment variables.

The benchmark preserves the project invariant: Oracle commit happens before
JetStream ACK.  It measures publish, fetch, map, Oracle execute, Oracle commit,
ACK, retry-delay observation, and shutdown phases without logging payloads,
credentials, table names, server addresses, wallet paths, or certificate
material.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import ssl
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
from nats.js.errors import NotFoundError

from nats_sinks.core.config import DeliveryConfig, EncryptionConfig
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames, observe_metric
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.oracle import OracleSink
from nats_sinks.oracle.routing import matches_subject
from nats_sinks.oracle.sql import validate_identifier
from nats_sinks.testing.oracle_benchmark import (
    OracleBenchmarkOptions,
    build_oracle_benchmark_report,
    render_oracle_benchmark_report,
    sanitize_public_text,
)

DEFAULT_STREAM = "NATS_SINKS_BENCHMARK"
DEFAULT_SUBJECT = "nats.sinks.benchmark.oracle"
DEFAULT_TABLE = "NATS_SINKS_BENCHMARK_EVENTS"


def _env(name: str, fallback: str | None = None) -> str | None:
    """Return a benchmark, e2e, or generic environment setting."""

    return (
        os.getenv(f"NATS_SINKS_BENCHMARK_{name}") or os.getenv(f"NATS_SINKS_E2E_{name}") or fallback
    )


def _oracle_env(name: str, fallback: str | None = None) -> str | None:
    """Return an Oracle environment setting shared with integration tests."""

    return os.getenv(f"NATS_SINKS_ORACLE_{name}", fallback)


def _required_env(value: str | None, name: str) -> str:
    """Fail closed when required local live-test configuration is missing."""

    if value:
        return value
    raise RuntimeError(f"{name} is required for the live Oracle benchmark")


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, fallback: int) -> int:
    value = _env(name)
    if value is None:
        return fallback
    return int(value)


def _float_oracle_env(name: str) -> float | None:
    value = _oracle_env(name)
    return float(value) if value else None


def _int_oracle_env(name: str) -> int | None:
    value = _oracle_env(name)
    return int(value) if value else None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a sanitized live NATS-to-Oracle phase benchmark."
    )
    parser.add_argument("--message-count", type=int, default=_int_env("MESSAGE_COUNT", 256))
    parser.add_argument("--batch-size", type=int, default=_int_env("BATCH_SIZE", 64))
    parser.add_argument(
        "--payload-shape",
        choices=("json", "text", "mixed", "empty", "binary"),
        default=_env("PAYLOAD_SHAPE", "mixed"),
    )
    parser.add_argument(
        "--sink-mode",
        choices=("merge", "insert_ignore", "insert", "append"),
        default=_env("SINK_MODE", "merge"),
    )
    parser.add_argument("--table", default=_env("ORACLE_TABLE", DEFAULT_TABLE))
    parser.add_argument(
        "--stream",
        default=_env("STREAM", "auto"),
        help="JetStream stream name, or 'auto' to discover the stream that owns the subject.",
    )
    parser.add_argument("--subject", default=_env("SUBJECT", DEFAULT_SUBJECT))
    parser.add_argument("--publish-subject", default=_env("PUBLISH_SUBJECT"))
    parser.add_argument("--drop-table-before", action="store_true")
    parser.add_argument("--drop-table-after", action="store_true")
    parser.add_argument("--with-encryption", action="store_true")
    parser.add_argument(
        "--encryption-algorithm",
        choices=("aes-256-gcm", "aes-256-ccm"),
        default=_env("ENCRYPTION_ALGORITHM", "aes-256-gcm"),
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--report-file", type=Path)
    return parser


def _payload_for_index(*, shape: str, run_id: str, index: int) -> bytes:
    """Build synthetic benchmark payload bytes without operational content."""

    selected = shape
    if shape == "mixed":
        selected = ("json", "text", "empty", "binary")[index % 4]
    if selected == "json":
        return json.dumps(
            {
                "benchmark": True,
                "run": run_id,
                "index": index,
                "kind": "json",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    if selected == "text":
        return f"synthetic-benchmark-text:{run_id}:{index:06d}".encode()
    if selected == "empty":
        return b""
    if selected == "binary":
        return bytes((index + offset) % 256 for offset in range(32))
    raise ValueError("unsupported payload shape")


def _nats_options() -> dict[str, Any]:
    """Build NATS client options without printing resolved secrets."""

    password_env = _env("NATS_PASSWORD_ENV", "NATS_PASSWORD")
    token_env = _env("NATS_TOKEN_ENV")
    tls_ca_file = _env("NATS_TLS_CA_FILE")
    options: dict[str, Any] = {
        "name": "nats-sinks-oracle-benchmark",
        "connect_timeout": 5,
        "allow_reconnect": False,
    }
    user = _env("NATS_USER")
    if user:
        options["user"] = user
        options["password"] = _required_env(
            os.getenv(password_env or ""),
            password_env or "NATS_PASSWORD",
        )
    elif token_env:
        options["token"] = _required_env(os.getenv(token_env), token_env)
    url = _required_env(_env("NATS_URL"), "NATS_SINKS_BENCHMARK_NATS_URL")
    if url.startswith("tls://") or tls_ca_file:
        options["tls"] = ssl.create_default_context(cafile=tls_ca_file)
    return options


def _oracle_sink(*, table: str, sink_mode: str, metrics: InMemoryMetrics) -> OracleSink:
    """Build the Oracle sink from local ignored environment configuration."""

    dsn = _required_env(_oracle_env("DSN"), "NATS_SINKS_ORACLE_DSN")
    user = _required_env(_oracle_env("USER"), "NATS_SINKS_ORACLE_USER")
    password_env = _oracle_env("PASSWORD_ENV", "ORACLE_PASSWORD")
    _required_env(os.getenv(password_env or ""), password_env or "ORACLE_PASSWORD")
    return OracleSink(
        dsn=dsn,
        user=user,
        password_env=password_env,
        config_dir=_oracle_env("CONFIG_DIR"),
        wallet_location=_oracle_env("WALLET_LOCATION"),
        wallet_password_env=_oracle_env("WALLET_PASSWORD_ENV"),
        ssl_server_dn_match=_bool_oracle_env("SSL_SERVER_DN_MATCH"),
        ssl_server_cert_dn=_oracle_env("SSL_SERVER_CERT_DN"),
        tcp_connect_timeout=_float_oracle_env("TCP_CONNECT_TIMEOUT"),
        retry_count=_int_oracle_env("RETRY_COUNT"),
        retry_delay=_int_oracle_env("RETRY_DELAY"),
        https_proxy=_oracle_env("HTTPS_PROXY"),
        https_proxy_port=_int_oracle_env("HTTPS_PROXY_PORT"),
        table=table,
        mode=sink_mode,  # type: ignore[arg-type]
        auto_create=True,
        metrics=metrics,
    )


def _bool_oracle_env(name: str) -> bool | None:
    value = _oracle_env(name)
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def _drop_table(pool: Any, *, table: str) -> None:
    """Drop a benchmark table when the operator explicitly asks for cleanup."""

    safe_table = validate_identifier(table)
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            with suppress(Exception):
                cursor.execute(f"drop table {safe_table} purge")  # nosec B608
        connection.commit()


async def _ensure_stream(js: Any, *, stream: str, subject: str, message_count: int) -> None:
    """Create or validate a benchmark stream."""

    try:
        info = await js.stream_info(stream)
    except NotFoundError:
        await js.add_stream(
            config=StreamConfig(
                name=stream,
                subjects=[subject],
                max_msgs=max(message_count * 2, 1000),
                max_age=24 * 60 * 60,
            )
        )
        return
    subjects = info.config.subjects or []
    if not any(matches_subject(pattern, subject) for pattern in subjects):
        raise RuntimeError("configured benchmark stream does not include the benchmark subject")


async def _resolve_stream(
    js: Any,
    *,
    configured_stream: str,
    subject: str,
    message_count: int,
) -> str:
    """Resolve the benchmark stream without creating overlapping subjects."""

    if configured_stream != "auto":
        stream = validate_identifier(configured_stream)
        await _ensure_stream(js, stream=stream, subject=subject, message_count=message_count)
        return stream

    find_stream = getattr(js, "find_stream_name_by_subject", None)
    if find_stream is not None:
        with suppress(Exception):
            detected = await find_stream(subject)
            return validate_identifier(str(detected))

    stream = DEFAULT_STREAM
    await _ensure_stream(js, stream=stream, subject=subject, message_count=message_count)
    return stream


async def _prepare_consumer(
    js: Any,
    *,
    stream: str,
    subject: str,
    consumer: str,
    max_ack_pending: int,
) -> None:
    """Create a disposable pull consumer for the benchmark run."""

    with suppress(Exception):
        await js.delete_consumer(stream, consumer)
    await js.add_consumer(
        stream,
        config=ConsumerConfig(
            durable_name=consumer,
            filter_subject=subject,
            deliver_policy=DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
            max_ack_pending=max_ack_pending,
        ),
    )


async def _publish_messages(
    js: Any,
    *,
    subject: str,
    stream: str,
    run_id: str,
    options: OracleBenchmarkOptions,
) -> None:
    """Publish benchmark messages to JetStream."""

    for index in range(options.message_count):
        message_id = f"{run_id}-{index:06d}"
        headers = {
            "Nats-Msg-Id": message_id,
            "Nats-Expected-Stream": stream,
            "Nats-Sinks-Priority": "benchmark",
            "Nats-Sinks-Classification": "UNCLASSIFIED",
            "Nats-Sinks-Labels": "benchmark;oracle",
        }
        await js.publish(
            subject,
            _payload_for_index(shape=options.payload_shape, run_id=run_id, index=index),
            headers=headers,
        )


async def _drain_messages(
    *,
    runner: JetStreamSinkRunner,
    subscription: Any,
    options: OracleBenchmarkOptions,
    metrics: InMemoryMetrics,
) -> None:
    """Fetch and process all benchmark messages with ACK-last behavior."""

    processed = 0
    while processed < options.message_count:
        fetch_size = min(options.batch_size, options.message_count - processed)
        fetch_started = time.perf_counter()
        raw_messages = await subscription.fetch(fetch_size, timeout=30)
        observe_metric(
            metrics,
            MetricNames.NATS_FETCH_SECONDS,
            time.perf_counter() - fetch_started,
        )
        await runner.process_raw_batch(raw_messages)
        processed += len(raw_messages)


def _encryption_config(enabled: bool, algorithm: str) -> EncryptionConfig | None:
    """Create benchmark encryption config with ephemeral key material if needed."""

    if not enabled:
        return None
    key_env = "NATS_SINKS_BENCHMARK_ENCRYPTION_KEY_B64"
    if key_env not in os.environ:
        os.environ[key_env] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return EncryptionConfig(
        enabled=True,
        algorithm=algorithm,
        key_id="nats-sinks-benchmark-generated",
        key_b64_env=key_env,
    )


async def _run_live_benchmark(args: argparse.Namespace) -> int:  # noqa: PLR0915
    """Run the benchmark and render a sanitized report."""

    if os.getenv("NATS_SINKS_ORACLE_BENCHMARK") != "1":
        sys.stderr.write(
            "Set NATS_SINKS_ORACLE_BENCHMARK=1 or use scripts/run-oracle-benchmark.sh "
            "to run a live non-production benchmark.\n"
        )
        return 2

    options = OracleBenchmarkOptions(
        message_count=args.message_count,
        batch_size=args.batch_size,
        payload_shape=args.payload_shape,
        sink_mode=args.sink_mode,
        encryption_enabled=args.with_encryption,
        encryption_algorithm=args.encryption_algorithm if args.with_encryption else "none",
        drop_table_before=args.drop_table_before,
        drop_table_after=args.drop_table_after,
    )
    table = validate_identifier(args.table)
    subject = sanitize_public_text(args.subject)
    publish_subject = args.publish_subject or args.subject
    if subject != args.subject:
        raise RuntimeError("benchmark subject contains unsafe control characters")

    metrics = InMemoryMetrics()
    explicit_phase_seconds: dict[str, list[float]] = {"publish": [], "shutdown": []}
    run_id = f"nats-sinks-benchmark-{uuid.uuid4().hex}"
    consumer = f"nats_sinks_benchmark_{uuid.uuid4().hex[:12]}"
    nats_url = _required_env(_env("NATS_URL"), "NATS_SINKS_BENCHMARK_NATS_URL")

    sink = _oracle_sink(table=table, sink_mode=args.sink_mode, metrics=metrics)
    nc: Any | None = None
    runner: JetStreamSinkRunner | None = None
    try:
        await sink.start()
        if sink._pool is not None and args.drop_table_before:
            await asyncio.to_thread(_drop_table, sink._pool, table=table)
            await sink.ensure_schema()

        nc = await nats.connect(nats_url, **_nats_options())
        js = nc.jetstream()
        stream = await _resolve_stream(
            js,
            configured_stream=args.stream,
            subject=args.subject,
            message_count=options.message_count,
        )
        await _prepare_consumer(
            js,
            stream=stream,
            subject=args.subject,
            consumer=consumer,
            max_ack_pending=max(options.batch_size * 2, 64),
        )
        publish_started = time.perf_counter()
        await _publish_messages(
            js,
            subject=publish_subject,
            stream=stream,
            run_id=run_id,
            options=options,
        )
        explicit_phase_seconds["publish"].append(time.perf_counter() - publish_started)

        runner = JetStreamSinkRunner(
            nats_url=nats_url,
            stream=stream,
            consumer=consumer,
            subject=args.subject,
            sink=sink,
            delivery=DeliveryConfig(batch_size=options.batch_size),
            encryption=_encryption_config(args.with_encryption, args.encryption_algorithm),
            metrics=metrics,
            jetstream=js,
            nats_connection=nc,
        )
        await runner.start()
        subscription = await js.pull_subscribe(args.subject, durable=consumer, stream=stream)
        await _drain_messages(
            runner=runner,
            subscription=subscription,
            options=options,
            metrics=metrics,
        )
    finally:
        shutdown_started = time.perf_counter()
        if sink._pool is not None and args.drop_table_after:
            await asyncio.to_thread(_drop_table, sink._pool, table=table)
        if runner is not None:
            await runner.stop()
        else:
            await sink.stop()
            if nc is not None:
                await nc.close()
        explicit_phase_seconds["shutdown"].append(time.perf_counter() - shutdown_started)

    report = build_oracle_benchmark_report(
        options=options,
        metrics=metrics,
        explicit_phase_seconds=explicit_phase_seconds,
        notes=(
            "Timing observations are environment-specific and should not be "
            "treated as portable throughput guarantees.",
            "Benchmark processing uses OracleSink commit before JetStream ACK.",
        ),
    )
    rendered = render_oracle_benchmark_report(report, output_format=args.format)
    if args.report_file is not None:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


def main() -> int:
    """CLI entry point for the live benchmark script."""

    args = _build_parser().parse_args()
    try:
        return asyncio.run(_run_live_benchmark(args))
    except Exception as exc:
        sys.stderr.write(f"Oracle benchmark failed: {sanitize_public_text(exc)}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
