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
import inspect
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError as PydanticValidationError

from nats_sinks import __version__
from nats_sinks.coherence import CoherenceSink
from nats_sinks.core.config import AppConfig, SinkPluginConfig, load_config, redacted_config
from nats_sinks.core.errors import ConfigurationError, NatsSinksError
from nats_sinks.core.fanout_sink import FanoutSink
from nats_sinks.core.logging import configure_logging
from nats_sinks.core.metrics import JsonFileMetrics, MetricsRecorder
from nats_sinks.core.nats_options import build_nats_connect_options
from nats_sinks.core.ordered_inspection import (
    DEFAULT_MAX_HEADER_VALUE_BYTES,
    DEFAULT_MAX_HEADERS,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PENDING_BYTES,
    DEFAULT_PENDING_MESSAGES,
    DEFAULT_TIMEOUT_SECONDS,
    OrderedInspectionOptions,
    collect_ordered_inspection_records,
    render_ordered_inspection_jsonl,
    render_ordered_inspection_text,
    resolve_inspection_output_path,
    validate_ordered_inspection_options,
    write_ordered_inspection_jsonl,
)
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.core.stream_management import (
    StreamManagementOptions,
    StreamManagementPlan,
    build_stream_management_plan,
)
from nats_sinks.file import FileSink
from nats_sinks.foundry import FoundrySink
from nats_sinks.gotham import GothamSink
from nats_sinks.http import HttpSink
from nats_sinks.mysql import MySqlSink
from nats_sinks.oracle import (
    OracleLineageReader,
    OracleSink,
    OracleSinkConfig,
    build_oracle_lineage_query,
    render_lineage_result_text,
    resolve_lineage_table,
)
from nats_sinks.oracle_nosql import OracleNoSqlSink
from nats_sinks.s3 import S3Sink
from nats_sinks.sinks.base import HealthCheckableSink, Sink
from nats_sinks.sinks.connectors import SinkConnector, load_entry_point_connectors
from nats_sinks.sinks.registry import SinkRegistry
from nats_sinks.spool import SpoolSink, replay_spool_to_sink

app = typer.Typer(help="Run NATS JetStream sink connectors.")


def _version_callback(value: bool) -> None:
    """Print the package version before command validation when requested."""

    if value:
        typer.echo(__version__)
        raise typer.Exit()


def _registry(plugins: SinkPluginConfig | None = None) -> SinkRegistry:
    """Build the explicit sink connector registry.

    Oracle Database, Oracle MySQL, Oracle NoSQL Database, Oracle Coherence CE,
    HTTP, S3-compatible object storage, and FileSink are first-party built-ins
    and are always registered.
    External connectors are loaded only when the JSON config explicitly enables
    plugin discovery and allow-lists the connector name.
    """

    registry = SinkRegistry()
    registry.register_connector(
        SinkConnector(
            name="file",
            factory=FileSink.from_mapping,
            summary="Built-in local JSON file sink.",
            built_in=True,
            production_ready=True,
            documentation="docs/file-sink.md",
            certification=("commit-then-ack", "unit", "integration"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="oracle",
            factory=OracleSink.from_mapping,
            summary="Built-in Oracle Database sink.",
            built_in=True,
            production_ready=True,
            requires_extra="oracle",
            documentation="docs/oracle-sink.md",
            certification=("commit-then-ack", "unit", "integration", "live-e2e"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="mysql",
            factory=MySqlSink.from_mapping,
            summary="Built-in Oracle MySQL sink.",
            built_in=True,
            production_ready=True,
            requires_extra="mysql",
            documentation="docs/mysql-sink.md",
            certification=("commit-then-ack", "unit", "integration", "container-e2e"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="oracle_nosql",
            factory=OracleNoSqlSink.from_mapping,
            summary="Built-in Oracle NoSQL Database sink.",
            status="experimental",
            built_in=True,
            production_ready=False,
            requires_extra="oracle-nosql",
            documentation="docs/oracle-nosql-sink.md",
            certification=("commit-then-ack", "unit", "container-e2e"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="coherence",
            factory=CoherenceSink.from_mapping,
            summary="Built-in Oracle Coherence Community Edition sink.",
            status="experimental",
            built_in=True,
            production_ready=False,
            requires_extra="coherence",
            documentation="docs/coherence-sink.md",
            certification=("commit-then-ack", "unit", "container-e2e"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="spool",
            factory=SpoolSink.from_mapping,
            summary="Built-in encrypted local edge spool sink.",
            built_in=True,
            production_ready=True,
            requires_extra="crypto",
            documentation="docs/spool-sink.md",
            certification=("commit-then-ack", "unit", "replay"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="http",
            factory=HttpSink.from_mapping,
            summary="Built-in HTTP endpoint sink.",
            built_in=True,
            production_ready=True,
            documentation="docs/http-sink.md",
            certification=("commit-then-ack", "unit", "mock-contract"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="s3",
            factory=S3Sink.from_mapping,
            summary="Built-in S3-compatible object sink.",
            built_in=True,
            production_ready=True,
            requires_extra="s3",
            documentation="docs/s3-sink.md",
            certification=("commit-then-ack", "unit", "mock-contract"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="foundry",
            factory=FoundrySink.from_mapping,
            summary="Experimental Palantir Foundry Streams sink.",
            status="experimental",
            built_in=True,
            production_ready=False,
            documentation="docs/foundry-sink.md",
            certification=("commit-then-ack", "unit", "mock-contract"),
        )
    )
    registry.register_connector(
        SinkConnector(
            name="gotham",
            factory=GothamSink.from_mapping,
            summary="Experimental Palantir Gotham RevDB object sink.",
            status="experimental",
            built_in=True,
            production_ready=False,
            documentation="docs/gotham-sink.md",
            certification=("commit-then-ack", "unit", "mock-contract"),
        )
    )
    if plugins is not None and plugins.enabled:
        for connector in load_entry_point_connectors(
            allowed_names=plugins.allowed_sinks,
            require_production_ready=plugins.require_production_ready,
        ):
            registry.register_connector(connector)
    return registry


def _raw_sink_config(config: AppConfig) -> dict[str, Any]:
    return config.sink.model_dump(mode="python")


def _raw_named_sink_config(config: AppConfig, sink_name: str) -> dict[str, Any]:
    try:
        sink_config = config.sinks[sink_name]
    except KeyError as exc:
        available = ", ".join(sorted(config.sinks)) or "none configured"
        raise ConfigurationError(
            f"unknown named sink {sink_name!r}; available named sinks: {available}"
        ) from exc
    return sink_config.model_dump(mode="python")


def _load_or_exit(config_path: Path) -> AppConfig:
    try:
        return load_config(config_path)
    except NatsSinksError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _build_sink(config: AppConfig) -> Sink:
    raw_sink = _raw_sink_config(config)
    if raw_sink.get("type") == "fanout":
        return _build_fanout_sink(config, raw_sink)
    return _build_sink_from_raw(config, raw_sink)


def _build_sink_from_raw(config: AppConfig, raw_sink: dict[str, Any]) -> Sink:
    sink_type = str(raw_sink.get("type", ""))
    if sink_type == "fanout":
        raise ConfigurationError("fanout can only be used as the active top-level sink")
    return _registry(config.plugins).create(sink_type, raw_sink)


def _validate_fanout_config(config: AppConfig, raw_sink: dict[str, Any]) -> None:
    """Validate the active fan-out sink without opening child destinations."""

    extra_fields = sorted(set(raw_sink) - {"type"})
    if extra_fields:
        joined = ", ".join(extra_fields)
        raise ConfigurationError(
            "sink.type 'fanout' accepts only the 'type' field after config normalization; "
            f"unexpected field(s): {joined}"
        )
    if not config.sinks:
        raise ConfigurationError("sink.type 'fanout' requires named child sinks")
    if not config.routing.enabled:
        raise ConfigurationError("sink.type 'fanout' requires routing.enabled true")
    if not config.routing.target_names():
        raise ConfigurationError("sink.type 'fanout' routing must select at least one target")


def _build_fanout_sink(config: AppConfig, raw_sink: dict[str, Any]) -> FanoutSink:
    """Create the active fan-out sink and only the child sinks referenced by routes."""

    _validate_fanout_config(config, raw_sink)
    children: dict[str, Sink] = {}
    for target_name in config.routing.target_names():
        child_raw = _raw_named_sink_config(config, target_name)
        children[target_name] = _build_sink_from_raw(config, child_raw)
    return FanoutSink(children=children, routing=config.routing)


def _validate_sink_config(
    config: AppConfig,
    raw_sink: dict[str, Any],
    *,
    label: str,
) -> None:
    """Validate one sink instance through the registry without opening it."""

    try:
        if raw_sink.get("type") == "fanout":
            if label != "sink":
                raise ConfigurationError(f"{label}: fanout may only be the active sink")
            _validate_fanout_config(config, raw_sink)
            return
        _build_sink_from_raw(config, raw_sink)
    except (ConfigurationError, PydanticValidationError) as exc:
        raise ConfigurationError(f"{label}: {exc}") from exc


def _validate_all_sink_configs(config: AppConfig) -> None:
    """Validate the active sink and every named sink instance."""

    _validate_sink_config(config, _raw_sink_config(config), label="sink")
    for name, sink_config in config.sinks.items():
        _validate_sink_config(
            config,
            sink_config.model_dump(mode="python"),
            label=f"sinks.{name}",
        )


def _target_description(target: Any) -> str:
    """Render one route target without exposing destination credentials."""

    parts = [target.sink]
    parts.append("required" if target.required else "optional")
    if target.minimum_wait_ms is not None:
        parts.append(f"minimum_wait_ms={target.minimum_wait_ms}")
    if target.timeout_ms is not None:
        parts.append(f"timeout_ms={target.timeout_ms}")
    return " (".join((parts[0], ", ".join(parts[1:]))) + ")"


def _print_named_sink_report(config: AppConfig) -> None:
    """Print safe route-to-sink information for operator review."""

    if config.sinks:
        rendered = ", ".join(
            f"{name} ({sink_config.type})" for name, sink_config in sorted(config.sinks.items())
        )
        typer.echo(f"Named sinks: {rendered}")
    if config.routing.default_targets:
        targets = ", ".join(
            _target_description(target) for target in config.routing.default_targets
        )
        typer.echo(f"Default route targets: {targets}")
    if config.routing.routes:
        typer.echo("Route target references:")
        for route in config.routing.routes:
            targets = ", ".join(_target_description(target) for target in route.targets)
            typer.echo(f"  - {route.name}: {targets}")


def _oracle_sink_config(config: AppConfig) -> OracleSinkConfig:
    """Return validated Oracle config for read-only Oracle helper commands."""

    raw_sink = _raw_sink_config(config)
    if raw_sink.get("type") != "oracle":
        raise ConfigurationError("lineage queries currently require sink.type 'oracle'")
    try:
        return OracleSinkConfig.model_validate(raw_sink)
    except PydanticValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


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


def _print_stream_plan_text(plan: StreamManagementPlan) -> None:
    """Render a stream management plan for human terminal review.

    The text intentionally contains stream and subject names because the command
    is an operator-facing local helper.  It never prints credentials, server
    URLs, IP addresses, payloads, or certificate material.
    """

    typer.echo("JetStream stream management plan")
    typer.echo(f"Stream: {plan.stream}")
    typer.echo(f"Durable consumer: {plan.durable_consumer}")
    typer.echo("Subjects:")
    for subject in plan.subjects:
        typer.echo(f"  - {subject}")
    typer.echo("Recommended stream settings:")
    typer.echo(f"  retention: {plan.settings.retention}")
    typer.echo(f"  discard: {plan.settings.discard}")
    typer.echo(f"  storage: {plan.settings.storage}")
    typer.echo(f"  replicas: {plan.settings.replicas}")
    typer.echo(f"  duplicate_window_seconds: {plan.settings.duplicate_window_seconds}")
    typer.echo("Runtime permissions to keep narrow:")
    for permission in plan.runtime_permissions:
        typer.echo(f"  - {permission}")
    typer.echo("Administrative permissions for a separate setup identity:")
    for permission in plan.administration_permissions:
        typer.echo(f"  - {permission}")
    typer.echo("NATS CLI example:")
    typer.echo(f"  {plan.nats_cli_example}")
    typer.echo("Notes:")
    for note in plan.notes:
        typer.echo(f"  - {note}")
    if plan.warnings:
        typer.echo("Warnings:")
        for warning in plan.warnings:
            typer.echo(f"  - {warning}")


def _nats_options(config: AppConfig) -> dict[str, Any]:
    """Convert validated JSON config into `nats-py` connection options."""

    return build_nats_connect_options(config.nats)


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


async def _connect_nats_for_inspection(options: dict[str, Any]) -> Any:
    """Open a NATS connection for inspection-only CLI commands.

    The helper exists so tests can inject a fake connection and prove that the
    inspection command does not construct or call any sink.
    """

    import nats  # noqa: PLC0415 - keep the runtime client import lazy.

    return await nats.connect(**options)


async def _close_nats_connection(connection: Any) -> None:
    """Close a NATS connection when the client exposes a close method."""

    close = getattr(connection, "close", None)
    if close is None or not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


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
        _validate_all_sink_configs(loaded)
    except (ConfigurationError, PydanticValidationError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo("Configuration is valid.")
    typer.echo(f"Active sink: {loaded.sink.type}")
    typer.echo("ACK policy: commit-then-acknowledge")
    _print_named_sink_report(loaded)


@app.command("show-effective-config")
def show_effective_config(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Show the effective configuration with secrets redacted."""

    loaded = _load_or_exit(config)
    _print_redacted(loaded)


@app.command("stream-plan")
def stream_plan(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    retention: Annotated[
        str,
        typer.Option(
            "--retention",
            help="Planned stream retention policy: limits, interest, or workqueue.",
        ),
    ] = "limits",
    discard: Annotated[
        str,
        typer.Option("--discard", help="Planned stream discard policy: old or new."),
    ] = "old",
    storage: Annotated[
        str,
        typer.Option("--storage", help="Planned stream storage type: file or memory."),
    ] = "file",
    replicas: Annotated[
        int,
        typer.Option("--replicas", help="Planned stream replica count, from 1 to 5."),
    ] = 1,
    duplicate_window_seconds: Annotated[
        int,
        typer.Option(
            "--duplicate-window-seconds",
            help="Planned JetStream duplicate detection window in seconds.",
        ),
    ] = 120,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Generate an offline JetStream stream-management plan.

    This command is intentionally separate from `nats-sink run`. It does not
    connect to NATS, does not create streams, does not update consumers, and
    does not require administrative credentials. Operators can use the output as
    a review artifact before applying changes with their approved NATS
    administration process.
    """

    loaded = _load_or_exit(config)
    try:
        options = StreamManagementOptions(
            retention=retention,
            discard=discard,
            storage=storage,
            replicas=replicas,
            duplicate_window_seconds=duplicate_window_seconds,
        )
        plan = build_stream_management_plan(loaded, options)
    except NatsSinksError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc

    normalized_format = output_format.strip().casefold()
    if normalized_format == "json":
        typer.echo(json.dumps(plan.to_dict(), indent=2, sort_keys=False))
    elif normalized_format == "text":
        _print_stream_plan_text(plan)
    else:
        typer.echo("Configuration error: --format must be text or json", err=True)
        raise typer.Exit(2)


@app.command("query-lineage")
def query_lineage(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    field: Annotated[
        str,
        typer.Option(
            "--field",
            help=(
                "Allow-listed lineage field: correlation_id, causation_id, mission_id, "
                "tasking_id, track_id, message_id, or subject."
            ),
        ),
    ],
    value: Annotated[
        str,
        typer.Option("--value", help="Identifier value to look up using a bind variable."),
    ],
    table: Annotated[
        str | None,
        typer.Option(
            "--table",
            help=(
                "Optional configured Oracle table to query. Must be sink.table or one of "
                "sink.table_routes[].table."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum records to return, from 1 to 1000."),
    ] = 50,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
    include_payload: Annotated[
        bool,
        typer.Option(
            "--include-payload",
            help="Explicitly include the payload column in output. Disabled by default.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate and print the generated query without connecting to Oracle.",
        ),
    ] = False,
) -> None:
    """Query persisted Oracle lineage records through a read-only helper.

    The command is intended for operators and auditors who need bounded,
    script-friendly inspection of already persisted records.  It does not
    connect to NATS, does not ACK messages, does not write to Oracle, and does
    not print payloads unless `--include-payload` is explicitly provided.
    """

    loaded = _load_or_exit(config)
    try:
        oracle_config = _oracle_sink_config(loaded)
        table_name = resolve_lineage_table(oracle_config, table)
        query = build_oracle_lineage_query(
            table=table_name,
            columns=oracle_config.columns,
            field=field,
            value=value,
            limit=limit,
            include_payload=include_payload,
        )
    except NatsSinksError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc

    normalized_format = output_format.strip().casefold()
    if normalized_format not in {"text", "json"}:
        typer.echo("Configuration error: --format must be text or json", err=True)
        raise typer.Exit(2)

    if dry_run:
        bind_names = sorted(query.binds)
        safe_plan = {
            "field": query.field,
            "table": query.table_name,
            "limit": query.limit,
            "payload_included": query.include_payload,
            "binds": bind_names,
            "sql": query.sql,
        }
        if normalized_format == "json":
            typer.echo(json.dumps(safe_plan, indent=2, sort_keys=False))
        else:
            typer.echo("Oracle lineage query dry run")
            typer.echo(f"field={safe_plan['field']}")
            typer.echo(f"table={safe_plan['table']}")
            typer.echo(f"limit={safe_plan['limit']}")
            typer.echo(f"payload_included={safe_plan['payload_included']}")
            typer.echo(f"binds={','.join(bind_names)}")
            typer.echo(query.sql)
        return

    try:
        result = asyncio.run(
            OracleLineageReader(oracle_config).query(
                field=field,
                value=value,
                table=table,
                limit=limit,
                include_payload=include_payload,
            )
        )
    except NatsSinksError as exc:
        typer.echo(f"Lineage query failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected lineage query failure: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc

    if normalized_format == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=False, allow_nan=False))
    else:
        typer.echo(render_lineage_result_text(result))


@app.command("inspect-ordered")
def inspect_ordered(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    max_messages: Annotated[
        int,
        typer.Option(
            "--max-messages",
            help="Maximum ordered inspection records to emit, from 1 to 1000.",
        ),
    ] = DEFAULT_MAX_MESSAGES,
    max_payload_bytes: Annotated[
        int,
        typer.Option(
            "--max-payload-bytes",
            help="Maximum total payload bytes to inspect before stopping.",
        ),
    ] = DEFAULT_MAX_PAYLOAD_BYTES,
    include_payload: Annotated[
        bool,
        typer.Option(
            "--include-payload",
            help="Include payload data in sanitized JSON output. Disabled by default.",
        ),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout-seconds",
            help="Per-message wait time before ending inspection when no message arrives.",
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or jsonl."),
    ] = "text",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help=(
                "Optional JSONL file name or path under --output-root for sanitized "
                "inspection records."
            ),
        ),
    ] = None,
    output_root: Annotated[
        Path,
        typer.Option(
            "--output-root",
            help="Approved local root for --output JSONL files.",
        ),
    ] = DEFAULT_OUTPUT_ROOT,
    pending_messages: Annotated[
        int,
        typer.Option("--pending-messages", help="Bounded client pending message limit."),
    ] = DEFAULT_PENDING_MESSAGES,
    pending_bytes: Annotated[
        int,
        typer.Option("--pending-bytes", help="Bounded client pending byte limit."),
    ] = DEFAULT_PENDING_BYTES,
    max_headers: Annotated[
        int,
        typer.Option("--max-headers", help="Maximum headers to include in sanitized output."),
    ] = DEFAULT_MAX_HEADERS,
    max_header_value_bytes: Annotated[
        int,
        typer.Option(
            "--max-header-value-bytes",
            help="Maximum UTF-8 bytes per non-sensitive header value in output.",
        ),
    ] = DEFAULT_MAX_HEADER_VALUE_BYTES,
) -> None:
    """Inspect stream messages through a read-only ordered consumer.

    This command is for bounded troubleshooting and analysis. It does not build
    a sink, does not write destination records, and does not ACK production
    durable work. If the installed NATS Python client does not expose ordered
    consumer support, the command fails closed with a compatibility message.
    """

    loaded = _load_or_exit(config)
    normalized_format = output_format.strip().casefold()
    if normalized_format not in {"text", "jsonl"}:
        typer.echo("Inspection configuration error: --format must be text or jsonl", err=True)
        raise typer.Exit(2)

    try:
        options = OrderedInspectionOptions(
            max_messages=max_messages,
            max_payload_bytes=max_payload_bytes,
            include_payload=include_payload,
            timeout_seconds=timeout_seconds,
            pending_messages=pending_messages,
            pending_bytes=pending_bytes,
            max_headers=max_headers,
            max_header_value_bytes=max_header_value_bytes,
        )
        validate_ordered_inspection_options(options)
        resolved_output = (
            resolve_inspection_output_path(output, output_root=output_root)
            if output is not None
            else None
        )
    except ConfigurationError as exc:
        typer.echo(f"Inspection configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc

    async def _inspect() -> Any:
        connection = await _connect_nats_for_inspection(_nats_options(loaded))
        try:
            jetstream = connection.jetstream()
            return await collect_ordered_inspection_records(
                jetstream,
                subject=loaded.nats.subject,
                stream=loaded.nats.stream,
                options=options,
                message_metadata=loaded.message_metadata,
                mission_metadata=loaded.mission_metadata,
                security_labels=loaded.security_labels,
            )
        finally:
            await _close_nats_connection(connection)

    try:
        result = asyncio.run(_inspect())
    except ConfigurationError as exc:
        typer.echo(f"Inspection configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except NatsSinksError as exc:
        typer.echo(f"Inspection failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected inspection failure: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc

    if resolved_output is not None:
        write_ordered_inspection_jsonl(result.records, resolved_output)

    if normalized_format == "jsonl":
        rendered = render_ordered_inspection_jsonl(result.records)
        if rendered:
            typer.echo(rendered)
        return

    typer.echo(render_ordered_inspection_text(result))
    if resolved_output is not None:
        typer.echo(f"JSONL inspection records written to {resolved_output}")


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
            push_consumer=loaded.push_consumer,
            dead_letter=loaded.dead_letter,
            message_metadata=loaded.message_metadata,
            message_authenticity=loaded.message_authenticity,
            mission_metadata=loaded.mission_metadata,
            security_labels=loaded.security_labels,
            encryption=loaded.encryption,
            custody=loaded.custody,
            advisories=loaded.advisories,
            size_policy=loaded.size_policy,
            pre_sink_policy=loaded.pre_sink_policy,
            metrics=metrics,
            metrics_config=loaded.metrics,
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
    sink_name: Annotated[
        str | None,
        typer.Option(
            "--sink-name",
            help="Health-check one named sink from the top-level sinks object.",
        ),
    ] = None,
    all_named_sinks: Annotated[
        bool,
        typer.Option(
            "--all-named-sinks",
            help="Health-check every named sink instance instead of only the active sink.",
        ),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Start and health-check the configured sink."""

    loaded = _load_or_exit(config)
    if sink_name is not None and all_named_sinks:
        typer.echo("Configuration error: use --sink-name or --all-named-sinks, not both", err=True)
        raise typer.Exit(2)

    try:
        if all_named_sinks:
            if not loaded.sinks:
                raise ConfigurationError("no named sinks are configured")
            selected_sinks = [
                (name, sink_config.model_dump(mode="python"))
                for name, sink_config in loaded.sinks.items()
            ]
            typer.echo(f"Named sinks selected: {', '.join(name for name, _ in selected_sinks)}")
        elif sink_name is not None:
            selected_sinks = [(sink_name, _raw_named_sink_config(loaded, sink_name))]
            typer.echo(
                f"Named sink selected: {sink_name} ({selected_sinks[0][1].get('type', 'unknown')})"
            )
        else:
            selected_sinks = [("active", _raw_sink_config(loaded))]
            typer.echo(f"Active sink: {loaded.sink.type}")
    except ConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo("ACK policy: commit-then-acknowledge")
    if dry_run:
        typer.echo("Dry run complete; sink was not opened.")
        return

    async def _test_one(label: str, raw_sink: dict[str, Any]) -> None:
        sink = _build_sink_from_raw(loaded, raw_sink)
        await sink.start()
        try:
            if isinstance(sink, HealthCheckableSink):
                await sink.healthcheck()
        finally:
            await sink.stop()

    async def _test() -> None:
        for label, raw_sink in selected_sinks:
            await _test_one(label, raw_sink)
            if label != "active":
                typer.echo(f"Sink test succeeded for {label}.")

    try:
        asyncio.run(_test())
    except NatsSinksError as exc:
        typer.echo(f"Sink test failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected sink test failure: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Sink test succeeded.")


@app.command("replay-spool")
def replay_spool(
    spool_config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    target_config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    max_records: Annotated[
        int | None,
        typer.Option(
            "--max-records",
            help="Maximum committed spool records to replay during this invocation.",
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Replay committed spool records into a configured target sink.

    Both arguments are normal nats-sinks JSON configuration files.  The first
    must select `sink.type: "spool"` and points at the local spool directory.
    The second selects the final destination sink, such as `file` or `oracle`.
    Replay never ACKs JetStream messages because the original ACK boundary was
    the local spool commit performed by `nats-sink run`.
    """

    if max_records is not None and max_records < 1:
        typer.echo("Configuration error: --max-records must be greater than zero", err=True)
        raise typer.Exit(2)

    loaded_spool = _load_or_exit(spool_config)
    loaded_target = _load_or_exit(target_config)
    spool_sink = _build_sink(loaded_spool)
    target_sink = _build_sink(loaded_target)
    if not isinstance(spool_sink, SpoolSink):
        typer.echo("Configuration error: first config must use sink.type 'spool'", err=True)
        raise typer.Exit(2)
    if isinstance(target_sink, SpoolSink):
        typer.echo("Configuration error: target config must not use sink.type 'spool'", err=True)
        raise typer.Exit(2)

    async def _replay() -> None:
        await spool_sink.start()
        if dry_run:
            entries = await asyncio.to_thread(spool_sink.committed_entries)
            limited = entries if max_records is None else entries[:max_records]
            typer.echo(f"Dry run complete; {len(limited)} committed spool record(s) eligible.")
            return

        await target_sink.start()
        try:
            result = await replay_spool_to_sink(
                spool_sink,
                target_sink,
                max_records=max_records,
            )
        finally:
            await target_sink.stop()
        typer.echo(
            "Replay complete: "
            f"scanned={result.scanned_records} "
            f"replayed={result.replayed_records} "
            f"deleted={result.deleted_records} "
            f"failed={result.failed_records}"
        )

    try:
        asyncio.run(_replay())
    except NatsSinksError as exc:
        typer.echo(f"Replay failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Unexpected replay failure: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
