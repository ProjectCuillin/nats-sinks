# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for running and inspecting nats-sinks.

The CLI is deliberately thin: it loads JSON configuration, constructs a sink
from the explicit registry, and hands delivery semantics to `JetStreamSinkRunner`.
All commands share the same validation path so `validate`, `show-effective-config`,
`test-sink`, and `run` fail on the same configuration errors.

Security-sensitive behavior is kept here as well.  Effective configuration is
rendered as redacted JSON, and the CLI does not print resolved passwords or
full NATS authentication material.  TLS options are converted into an
`ssl.SSLContext` only when the user requests TLS by URL or by certificate
configuration.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError as PydanticValidationError

from nats_sinks import __version__
from nats_sinks.core.config import AppConfig, load_config, redacted_config
from nats_sinks.core.errors import ConfigurationError, NatsSinksError
from nats_sinks.core.logging import configure_logging
from nats_sinks.core.metrics import JsonFileMetrics, MetricsRecorder
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.file import FileSink
from nats_sinks.oracle import OracleSink
from nats_sinks.sinks.base import HealthCheckableSink, Sink
from nats_sinks.sinks.registry import SinkRegistry

app = typer.Typer(help="Run NATS JetStream sink connectors.")


def _version_callback(value: bool) -> None:
    """Print the package version before command validation when requested."""

    if value:
        typer.echo(__version__)
        raise typer.Exit()


def _registry() -> SinkRegistry:
    registry = SinkRegistry()
    registry.register("file", FileSink.from_mapping)
    registry.register("oracle", OracleSink.from_mapping)
    return registry


def _raw_sink_config(config: AppConfig) -> dict[str, Any]:
    return config.sink.model_dump(mode="python")


def _load_or_exit(config_path: Path) -> AppConfig:
    try:
        return load_config(config_path)
    except NatsSinksError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _build_sink(config: AppConfig) -> Sink:
    raw_sink = _raw_sink_config(config)
    sink_type = str(raw_sink.get("type", ""))
    return _registry().create(sink_type, raw_sink)


def _attach_metrics_to_sink(sink: Sink, metrics: MetricsRecorder | None) -> None:
    """Attach optional metrics to sinks that expose sink-specific counters.

    Core delivery metrics are always recorded by `JetStreamSinkRunner`. Some
    destination sinks can also expose safe, low-cardinality counters.  Oracle
    uses this for duplicate/conflict observations.  The hook is deliberately
    optional so future sinks are not forced to care about metrics.
    """

    set_metrics = getattr(sink, "set_metrics", None)
    if callable(set_metrics):
        set_metrics(metrics)


def _print_redacted(config: AppConfig) -> None:
    typer.echo(json.dumps(redacted_config(config), indent=2, sort_keys=False))


def _nats_options(config: AppConfig) -> dict[str, Any]:
    """Convert validated JSON config into `nats-py` connection options.

    Secret values are resolved at this final boundary rather than during config
    loading so validation and redacted config rendering can run without reading
    secret environment variables.  The returned dictionary is passed directly to
    `nats.connect`, so option names intentionally match `nats-py` keywords.
    """

    password = config.nats.resolve_password()
    token = config.nats.resolve_token()
    servers = config.nats.urls or [config.nats.url]
    options: dict[str, Any] = {
        key: value
        for key, value in {
            "servers": servers,
            "user": config.nats.user,
            "password": password,
            "token": token,
            "name": config.nats.name,
            "user_credentials": config.nats.creds_file,
            "nkeys_seed": config.nats.nkey_seed_file,
            # `no_echo` asks the NATS server not to echo messages published by
            # this same connection back to subscriptions on the same
            # connection. nats-sinks primarily uses pull consumers and only
            # publishes DLQ messages, so the default remains explicit and off.
            "no_echo": config.nats.no_echo,
            "allow_reconnect": config.nats.allow_reconnect,
            "connect_timeout": config.nats.connect_timeout_seconds,
            "reconnect_time_wait": config.nats.reconnect_time_wait_seconds,
            "max_reconnect_attempts": config.nats.max_reconnect_attempts,
            "ping_interval": config.nats.ping_interval_seconds,
            "max_outstanding_pings": config.nats.max_outstanding_pings,
            "pending_size": config.nats.pending_size_bytes,
            "drain_timeout": config.nats.drain_timeout_seconds,
        }.items()
        if value is not None
    }

    if any(
        (
            config.nats.tls_ca_file,
            config.nats.tls_cert_file,
            config.nats.tls_key_file,
            any(server.startswith("tls://") for server in servers),
        )
    ):
        # A local CA file lets operators trust a private or self-signed NATS
        # server CA without disabling certificate verification globally.
        context = ssl.create_default_context(cafile=config.nats.tls_ca_file)
        context.check_hostname = config.nats.tls_verify
        if not config.nats.tls_verify:
            context.verify_mode = ssl.CERT_NONE
        if config.nats.tls_cert_file:
            # Client certificates are passed through for deployments that use
            # mutual TLS transport. Full certificate-identity authorization is
            # tracked as a roadmap item because it needs more acceptance tests.
            context.load_cert_chain(
                certfile=config.nats.tls_cert_file,
                keyfile=config.nats.tls_key_file,
            )
        options["tls"] = context
    return options


def _metrics_recorder(config: AppConfig) -> MetricsRecorder | None:
    """Build the optional CLI-owned metrics recorder.

    The CLI intentionally supports only local JSON snapshots for now. Full
    Prometheus or OpenTelemetry exporters are deployment-owned future work, but
    a snapshot file gives shell scripts and developers a safe, dependency-free
    way to inspect current counters.
    """

    if not config.metrics.enabled or config.metrics.snapshot_file is None:
        return None
    return JsonFileMetrics(
        config.metrics.snapshot_file,
        namespace=config.metrics.namespace,
    )


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """nats-sinks provides commit-then-acknowledge JetStream sinks."""
    _ = version


@app.command()
def validate(config: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Validate a configuration file."""

    loaded = _load_or_exit(config)
    try:
        _build_sink(loaded)
    except (ConfigurationError, PydanticValidationError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo("Configuration is valid.")
    typer.echo(f"Active sink: {loaded.sink.type}")
    typer.echo("ACK policy: commit-then-acknowledge")


@app.command("show-effective-config")
def show_effective_config(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Show the effective configuration with secrets redacted."""

    loaded = _load_or_exit(config)
    _print_redacted(loaded)


@app.command()
def run(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    log_level: Annotated[str | None, typer.Option("--log-level")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run a sink process."""

    loaded = _load_or_exit(config)
    if log_level:
        loaded.logging.level = log_level
    try:
        configure_logging(loaded.logging.level)
        sink = _build_sink(loaded)
    except (ConfigurationError, PydanticValidationError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Active sink: {loaded.sink.type}")
    typer.echo("ACK policy: commit-then-acknowledge")
    if dry_run:
        typer.echo("Dry run complete; no NATS or sink connections opened.")
        return

    try:
        metrics = _metrics_recorder(loaded)
        _attach_metrics_to_sink(sink, metrics)
        runner = JetStreamSinkRunner(
            nats_url=loaded.nats.url,
            stream=loaded.nats.stream,
            consumer=loaded.nats.consumer,
            subject=loaded.nats.subject,
            durable=loaded.nats.durable,
            sink=sink,
            delivery=loaded.delivery,
            consumer_management=loaded.consumer_management,
            dead_letter=loaded.dead_letter,
            message_metadata=loaded.message_metadata,
            mission_metadata=loaded.mission_metadata,
            encryption=loaded.encryption,
            custody=loaded.custody,
            advisories=loaded.advisories,
            pre_sink_policy=loaded.pre_sink_policy,
            metrics=metrics,
            nats_options=_nats_options(loaded),
        )
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        typer.echo("Shutdown requested.")
    except NatsSinksError as exc:
        typer.echo(f"Runtime error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected runtime error: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc


@app.command("test-sink")
def test_sink(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Start and health-check the configured sink."""

    loaded = _load_or_exit(config)
    sink = _build_sink(loaded)
    typer.echo(f"Active sink: {loaded.sink.type}")
    typer.echo("ACK policy: commit-then-acknowledge")
    if dry_run:
        typer.echo("Dry run complete; sink was not opened.")
        return

    async def _test() -> None:
        await sink.start()
        try:
            if isinstance(sink, HealthCheckableSink):
                await sink.healthcheck()
        finally:
            await sink.stop()

    try:
        asyncio.run(_test())
    except NatsSinksError as exc:
        typer.echo(f"Sink test failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected sink test failure: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Sink test succeeded.")
