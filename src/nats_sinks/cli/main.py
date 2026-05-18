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
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.oracle import OracleSink
from nats_sinks.sinks.base import HealthCheckableSink, Sink
from nats_sinks.sinks.registry import SinkRegistry

app = typer.Typer(help="Run NATS JetStream sink connectors.")


def _registry() -> SinkRegistry:
    registry = SinkRegistry()
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
    options: dict[str, Any] = {
        key: value
        for key, value in {
            "user": config.nats.user,
            "password": password,
            "token": token,
            "name": config.nats.name,
            "user_credentials": config.nats.creds_file,
            "nkeys_seed": config.nats.nkey_seed_file,
        }.items()
        if value is not None
    }

    if any(
        (
            config.nats.tls_ca_file,
            config.nats.tls_cert_file,
            config.nats.tls_key_file,
            config.nats.url.startswith("tls://"),
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


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    """nats-sinks provides commit-then-acknowledge JetStream sinks."""

    if version:
        typer.echo(__version__)
        raise typer.Exit()


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
    configure_logging(loaded.logging.level)
    sink = _build_sink(loaded)
    typer.echo(f"Active sink: {loaded.sink.type}")
    typer.echo("ACK policy: commit-then-acknowledge")
    if dry_run:
        typer.echo("Dry run complete; no NATS or sink connections opened.")
        return

    runner = JetStreamSinkRunner(
        nats_url=loaded.nats.url,
        stream=loaded.nats.stream,
        consumer=loaded.nats.consumer,
        subject=loaded.nats.subject,
        durable=loaded.nats.durable,
        sink=sink,
        delivery=loaded.delivery,
        dead_letter=loaded.dead_letter,
        nats_options=_nats_options(loaded),
    )
    try:
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
