# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Command-line tools for observability policy and connectors.

The primary `nats-sink` command runs a sink worker.  This companion CLI manages
the separate observability side of the project: generating an export policy from
a core configuration, validating that policy, and rendering a Prometheus
textfile from a local metrics snapshot.  Keeping this logic separate helps
operators run the Prometheus connector as a different Linux service with fewer
permissions than the worker that talks to NATS and a destination backend.
"""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Literal

import typer

from nats_sinks import __version__
from nats_sinks.core.config import load_config
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import (
    METRIC_SPECS,
    load_metrics_snapshot,
)
from nats_sinks.observability.cloudwatch import (
    DISABLED_CLOUDWATCH_TEXT,
    EMPTY_CLOUDWATCH_TEXT,
    export_cloudwatch_metrics,
    render_cloudwatch_put_metric_data_requests_json,
)
from nats_sinks.observability.elastic import (
    DISABLED_ELASTIC_TEXT,
    EMPTY_ELASTIC_TEXT,
    export_elastic_observability_metrics,
    render_elastic_otlp_metrics_json,
)
from nats_sinks.observability.grafana_alloy import (
    DISABLED_GRAFANA_ALLOY_TEXT,
    EMPTY_GRAFANA_ALLOY_TEXT,
    export_grafana_alloy_metrics,
    render_grafana_alloy_config,
    render_grafana_alloy_otlp_metrics_json,
)
from nats_sinks.observability.nats_monitoring import (
    NatsMonitoringError,
    collect_nats_monitoring_snapshot,
    load_nats_monitoring_snapshot,
    render_nats_monitoring_prometheus,
    write_nats_monitoring_snapshot,
)
from nats_sinks.observability.otlp import (
    DISABLED_OTLP_TEXT,
    EMPTY_OTLP_TEXT,
    export_otlp_metrics,
    render_otlp_metrics_json,
)
from nats_sinks.observability.policy import (
    ObservabilityPolicy,
    load_observability_policy,
    observability_policy_template,
    write_observability_policy,
)
from nats_sinks.observability.prometheus import (
    render_prometheus_textfile,
    write_prometheus_textfile,
)
from nats_sinks.observability.prometheus_http import (
    ensure_prometheus_http_enabled,
    render_prometheus_http_response,
    serve_prometheus_http,
)
from nats_sinks.observability.splunk_hec import (
    DISABLED_SPLUNK_HEC_TEXT,
    EMPTY_SPLUNK_HEC_TEXT,
    export_splunk_hec_metrics,
    render_splunk_hec_event_json,
)
from nats_sinks.observability.statsd import (
    DISABLED_STATSD_TEXT,
    EMPTY_STATSD_TEXT,
    export_statsd_metrics,
    render_statsd_lines,
)
from nats_sinks.observability.syslog import (
    DISABLED_SYSLOG_TEXT,
    EMPTY_SYSLOG_TEXT,
    export_syslog_metrics,
    render_syslog_messages,
)

app = typer.Typer(help="Manage nats-sinks observability policies and connectors.")

PolicyOutputFormat = Literal["json", "summary"]
ListOutputFormat = Literal["table", "json", "names", "shell"]


def _version_callback(value: bool) -> None:
    """Print the package version before command validation when requested."""

    if value:
        typer.echo(__version__)
        raise typer.Exit()


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
    """Manage observability policy without connecting to NATS or a sink."""
    _ = version


def _load_policy_or_exit(policy_file: Path) -> ObservabilityPolicy:
    try:
        return load_observability_policy(policy_file)
    except ConfigurationError as exc:
        typer.echo(f"Observability policy error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _load_snapshot_or_exit(snapshot_file: Path) -> dict[str, object]:
    try:
        return load_metrics_snapshot(snapshot_file)
    except ValueError as exc:
        typer.echo(f"Metrics snapshot error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _load_nats_monitoring_snapshot_or_exit(snapshot_file: Path) -> dict[str, object]:
    """Load a NATS monitoring snapshot and report safe CLI errors."""

    try:
        return load_nats_monitoring_snapshot(snapshot_file)
    except ValueError as exc:
        typer.echo(f"NATS monitoring snapshot error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _snapshot_age_seconds(snapshot: dict[str, object]) -> float:
    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be numeric")
    return max(time.time() - float(generated), 0.0)


def _check_staleness(
    snapshot: dict[str, object],
    *,
    stale_after_seconds: float | None,
    allow_stale: bool,
) -> None:
    """Reject stale snapshots unless the operator explicitly allows them."""

    if stale_after_seconds is None:
        return
    age = _snapshot_age_seconds(snapshot)
    if age <= stale_after_seconds:
        return
    typer.echo(
        f"Metrics snapshot is stale: age={age:.1f}s limit={stale_after_seconds:.1f}s",
        err=True,
    )
    if not allow_stale:
        raise typer.Exit(3)


def _policy_summary(policy: ObservabilityPolicy) -> str:
    return "\n".join(
        [
            f"schema={policy.schema_id}",
            f"enabled={str(policy.enabled).lower()}",
            f"namespace={policy.namespace}",
            f"prometheus_enabled={str(policy.prometheus.enabled).lower()}",
            f"otlp_enabled={str(policy.otlp.enabled).lower()}",
            f"elastic_enabled={str(policy.elastic.enabled).lower()}",
            f"grafana_alloy_enabled={str(policy.grafana_alloy.enabled).lower()}",
            f"splunk_hec_enabled={str(policy.splunk_hec.enabled).lower()}",
            f"statsd_enabled={str(policy.statsd.enabled).lower()}",
            f"cloudwatch_enabled={str(policy.cloudwatch.enabled).lower()}",
            f"syslog_enabled={str(policy.syslog.enabled).lower()}",
            f"nats_server_monitoring_enabled={str(policy.nats_server_monitoring.enabled).lower()}",
            "nats_server_monitoring_prometheus_enabled="
            f"{str(policy.nats_server_monitoring.prometheus_enabled).lower()}",
            f"allowed_metrics={len(policy.allowed_metrics)}",
            f"allowed_metric_patterns={len(policy.allowed_metric_patterns)}",
            f"denied_metrics={len(policy.denied_metrics)}",
            f"denied_metric_patterns={len(policy.denied_metric_patterns)}",
            f"subjects={len(policy.subjects)}",
        ]
    )


@app.command("init-prometheus-policy")
def init_prometheus_policy(
    config_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    policy_file: Annotated[Path, typer.Argument(help="Path for the generated policy JSON.")],
    output_file: Annotated[
        str | None,
        typer.Option(
            "--output-file",
            help="Optional Prometheus textfile path to place in the disabled policy.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing policy file."),
    ] = False,
) -> None:
    """Generate a disabled Prometheus observability policy from core config."""

    try:
        config = load_config(config_file)
        template = observability_policy_template(config, output_file=output_file)
        write_observability_policy(template, policy_file, overwrite=overwrite)
    except ConfigurationError as exc:
        typer.echo(f"Observability policy generation error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Generated disabled observability policy: {policy_file}")
    typer.echo("Prometheus export remains disabled until the policy explicitly enables sharing.")


@app.command("validate-policy")
def validate_policy(
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Validate an observability policy file."""

    policy = _load_policy_or_exit(policy_file)
    typer.echo("Observability policy is valid.")
    typer.echo(_policy_summary(policy))


@app.command("show-effective-policy")
def show_effective_policy(
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_format: Annotated[
        PolicyOutputFormat,
        typer.Option("--format", "-f", help="Output format: json or summary."),
    ] = "json",
) -> None:
    """Show an observability policy in a safe, script-friendly form."""

    policy = _load_policy_or_exit(policy_file)
    if output_format == "summary":
        typer.echo(_policy_summary(policy))
        return
    typer.echo(json.dumps(policy.model_dump(mode="json", exclude_none=True), indent=2))


@app.command("list-metrics")
def list_metrics(
    output_format: Annotated[
        ListOutputFormat,
        typer.Option("--format", "-f", help="Output format: table, json, names, or shell."),
    ] = "table",
) -> None:
    """List metric names that can be allowed in an observability policy."""

    if output_format == "json":
        typer.echo(
            json.dumps(
                [
                    {
                        "name": spec.name,
                        "kind": spec.kind,
                        "description": spec.description,
                    }
                    for spec in METRIC_SPECS
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return
    if output_format == "names":
        typer.echo("\n".join(spec.name for spec in METRIC_SPECS))
        return
    if output_format == "shell":
        typer.echo("\n".join(f"{spec.name.upper()}={spec.kind}" for spec in METRIC_SPECS))
        return

    rows = list(METRIC_SPECS)
    name_width = max(len("METRIC"), *(len(spec.name) for spec in rows))
    kind_width = max(len("KIND"), *(len(spec.kind) for spec in rows))
    lines = [f"{'METRIC':<{name_width}}  {'KIND':<{kind_width}}  DESCRIPTION"]
    for spec in rows:
        lines.append(f"{spec.name:<{name_width}}  {spec.kind:<{kind_width}}  {spec.description}")
    typer.echo("\n".join(lines))


def _subject_shell_name(subject: str) -> str:
    """Return a shell-safe variable name for a configured subject pattern."""

    rendered = []
    for character in subject.upper():
        if character.isalnum():
            rendered.append(character)
        else:
            rendered.append("_")
    return "".join(rendered).strip("_") or "SUBJECT"


@app.command("list-subjects")
def list_subjects(
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_format: Annotated[
        ListOutputFormat,
        typer.Option("--format", "-f", help="Output format: table, json, names, or shell."),
    ] = "table",
    enabled_only: Annotated[
        bool,
        typer.Option("--enabled-only", help="Show only subject policy entries enabled for export."),
    ] = False,
) -> None:
    """List subject patterns discovered from core configuration.

    Current nats-sinks core metrics are not subject-labeled.  This command is
    therefore an operator review aid and a future-proof policy surface for
    subject-aware metrics.  It does not imply that subject names will be
    exported unless a future connector explicitly supports and documents that.
    """

    policy = _load_policy_or_exit(policy_file)
    subjects = [subject for subject in policy.subjects if subject.enabled or not enabled_only]
    if output_format == "json":
        typer.echo(
            json.dumps(
                [
                    {
                        "subject": subject.subject,
                        "enabled": subject.enabled,
                        "allowed_metrics": subject.allowed_metrics,
                        "allowed_metric_patterns": subject.allowed_metric_patterns,
                        "share_subject_label": subject.share_subject_label,
                    }
                    for subject in subjects
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return
    if output_format == "names":
        typer.echo("\n".join(subject.subject for subject in subjects))
        return
    if output_format == "shell":
        typer.echo(
            "\n".join(
                f"NATS_SINKS_SUBJECT_{index}_{_subject_shell_name(subject.subject)}="
                f"{subject.subject}"
                for index, subject in enumerate(subjects, start=1)
            )
        )
        return

    lines = ["SUBJECT  ENABLED  SHARE_SUBJECT_LABEL  ALLOWED_METRICS"]
    for subject in subjects:
        allowed = ",".join([*subject.allowed_metrics, *subject.allowed_metric_patterns])
        lines.append(
            f"{subject.subject}  {str(subject.enabled).lower()}  "
            f"{str(subject.share_subject_label).lower()}  {allowed}"
        )
    typer.echo("\n".join(lines))


@app.command("prometheus-textfile")
def prometheus_textfile(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write Prometheus text to this file instead of stdout or policy output_file.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Render to stdout even when an output file is configured."),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but do not fail when the snapshot is stale."),
    ] = False,
) -> None:
    """Render policy-filtered Prometheus textfile output from a snapshot."""

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.prometheus.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.prometheus.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    try:
        rendered = render_prometheus_textfile(snapshot, policy)
    except ValueError as exc:
        typer.echo(f"Prometheus render error: {exc}", err=True)
        raise typer.Exit(2) from exc

    selected_output = output or (
        Path(policy.prometheus.output_file) if policy.prometheus.output_file is not None else None
    )
    if selected_output is None or dry_run:
        typer.echo(rendered, nl=False)
        return
    try:
        write_prometheus_textfile(rendered, selected_output)
    except OSError as exc:
        typer.echo(f"Prometheus textfile write error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Wrote Prometheus textfile: {selected_output}")


@app.command("prometheus-http")
def prometheus_http(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render one scrape response to stdout instead of opening a listener.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but serve when the snapshot is stale."),
    ] = False,
) -> None:
    """Run the optional native Prometheus scrape endpoint.

    The endpoint is disabled unless both the top-level observability policy and
    `prometheus.http_endpoint.enabled` are true.  It is designed to run as a
    separate observability service and reads only the local metrics snapshot.
    """

    policy = _load_policy_or_exit(policy_file)
    try:
        ensure_prometheus_http_enabled(policy)
    except ConfigurationError as exc:
        typer.echo(f"Prometheus HTTP endpoint error: {exc}", err=True)
        raise typer.Exit(2) from exc

    if dry_run:
        response = render_prometheus_http_response(
            snapshot_file,
            policy,
            request_path=policy.prometheus.http_endpoint.path,
            allow_stale=allow_stale,
        )
        typer.echo(response.body.decode("utf-8"), nl=False)
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            raise typer.Exit(3)
        return

    endpoint = policy.prometheus.http_endpoint
    typer.echo(f"Serving Prometheus metrics on {endpoint.host}:{endpoint.port}{endpoint.path}")
    try:
        serve_prometheus_http(snapshot_file, policy, allow_stale=allow_stale)
    except OSError as exc:
        typer.echo(f"Prometheus HTTP endpoint error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command("otlp-export")
def otlp_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render the OTLP/HTTP JSON body to stdout instead of posting it.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export policy-approved metrics to an OpenTelemetry collector.

    The command is intentionally separate from `nats-sink run`.  OTLP export
    reads a local metrics snapshot, applies the observability policy, and fails
    safely without changing JetStream ACK, NAK, DLQ, retry, or sink behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.otlp.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.otlp.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.otlp.enabled:
        typer.echo(DISABLED_OTLP_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo("OTLP export error: enabled OTLP export requires a metrics snapshot", err=True)
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_otlp_metrics_json(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"OTLP render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered.decode("utf-8"))
        return

    try:
        result = export_otlp_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"OTLP export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "OTLP export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"status={result.status_code if result.status_code is not None else 'none'} "
        f"message={result.message}"
    )
    if result.message == EMPTY_OTLP_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("elastic-export")
def elastic_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render the Elastic OTLP JSON body to stdout instead of posting it.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics through the Elastic Observability OTLP profile.

    This command is an observability-side profile over the generic OTLP core.
    It reads a local metrics snapshot, applies the shared allow and deny policy,
    and sends only bounded, low-cardinality metrics to the configured collector
    or Elastic-managed OTLP path.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.elastic.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.elastic.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.elastic.enabled:
        typer.echo(DISABLED_ELASTIC_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "Elastic Observability export error: enabled Elastic export requires "
            "a metrics snapshot",
            err=True,
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_elastic_otlp_metrics_json(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"Elastic Observability render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered.decode("utf-8"))
        return

    try:
        result = export_elastic_observability_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Elastic Observability export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "Elastic Observability export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"status={result.status_code if result.status_code is not None else 'none'} "
        f"message={result.message}"
    )
    if result.message == EMPTY_ELASTIC_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("grafana-alloy-config")
def grafana_alloy_config(
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Render a minimal Grafana Alloy River config for the OTLP handoff."""

    policy = _load_policy_or_exit(policy_file)
    try:
        rendered = render_grafana_alloy_config(policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Grafana Alloy config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(rendered, nl=False)


@app.command("grafana-alloy-export")
def grafana_alloy_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render the Grafana Alloy OTLP JSON body to stdout instead of posting it.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics to a Grafana Alloy OTLP receiver.

    This command is intentionally outside the delivery worker.  It reads only a
    local metrics snapshot and sends policy-approved metrics to Alloy; failures
    cannot change JetStream ACK, NAK, DLQ, retry, or sink behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.grafana_alloy.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.grafana_alloy.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.grafana_alloy.enabled:
        typer.echo(DISABLED_GRAFANA_ALLOY_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "Grafana Alloy export error: enabled Grafana Alloy export requires a metrics snapshot",
            err=True,
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_grafana_alloy_otlp_metrics_json(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"Grafana Alloy render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered.decode("utf-8"))
        return

    try:
        result = export_grafana_alloy_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Grafana Alloy export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "Grafana Alloy export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"status={result.status_code if result.status_code is not None else 'none'} "
        f"message={result.message}"
    )
    if result.message == EMPTY_GRAFANA_ALLOY_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("splunk-hec-export")
def splunk_hec_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render the Splunk HEC JSON event body to stdout instead of posting it.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics to Splunk HTTP Event Collector.

    This command is intentionally outside the delivery worker. It reads only a
    local metrics snapshot and sends policy-approved aggregate metric fields to
    Splunk HEC; failures cannot change JetStream ACK, NAK, DLQ, retry, or sink
    behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.splunk_hec.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.splunk_hec.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.splunk_hec.enabled:
        typer.echo(DISABLED_SPLUNK_HEC_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "Splunk HEC export error: enabled Splunk HEC export requires a metrics snapshot",
            err=True,
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_splunk_hec_event_json(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"Splunk HEC render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered.decode("utf-8"))
        return

    try:
        result = export_splunk_hec_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Splunk HEC export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "Splunk HEC export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"status={result.status_code if result.status_code is not None else 'none'} "
        f"message={result.message}"
    )
    if result.message == EMPTY_SPLUNK_HEC_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("statsd-export")
def statsd_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render StatsD lines to stdout instead of sending datagrams.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics to StatsD.

    StatsD output is best-effort observability.  It reads only a local metrics
    snapshot, applies the shared allow and deny policy, and sends bounded
    datagrams to the configured StatsD target.  Failures cannot change
    JetStream ACK, NAK, DLQ, retry, or sink behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.statsd.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.statsd.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.statsd.enabled:
        typer.echo(DISABLED_STATSD_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "StatsD export error: enabled StatsD export requires a metrics snapshot", err=True
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_statsd_lines(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"StatsD render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered, nl=False)
        return

    try:
        result = export_statsd_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"StatsD export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "StatsD export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"datagrams={result.datagrams} "
        f"message={result.message}"
    )
    if result.message == EMPTY_STATSD_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("cloudwatch-export")
def cloudwatch_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render CloudWatch PutMetricData JSON requests instead of sending them.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics to Amazon CloudWatch custom metrics.

    The command reads only a local metrics snapshot and sends policy-approved
    custom metric data through the optional AWS SDK path. Failures cannot change
    JetStream ACK, NAK, DLQ, retry, or sink behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.cloudwatch.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.cloudwatch.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.cloudwatch.enabled:
        typer.echo(DISABLED_CLOUDWATCH_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "Amazon CloudWatch export error: enabled CloudWatch export requires a metrics snapshot",
            err=True,
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_cloudwatch_put_metric_data_requests_json(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"Amazon CloudWatch render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered.decode("utf-8"))
        return

    try:
        result = export_cloudwatch_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Amazon CloudWatch export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "Amazon CloudWatch export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"requests={result.requests} "
        f"metrics={result.metrics} "
        f"message={result.message}"
    )
    if result.message == EMPTY_CLOUDWATCH_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("syslog-export")
def syslog_export(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="Metrics snapshot JSON written by nats-sink."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Render RFC 5424-style syslog messages to stdout instead of sending datagrams.",
        ),
    ] = False,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but export when the snapshot is stale."),
    ] = False,
) -> None:
    """Export approved metrics as RFC 5424-style syslog messages.

    The syslog bridge is best-effort observability.  It reads only a local
    metrics snapshot, applies the shared allow and deny policy, and sends
    bounded structured messages to the configured syslog target.  Failures
    cannot change JetStream ACK, NAK, DLQ, retry, or sink behavior.
    """

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if policy.enabled and policy.syslog.enabled:
        snapshot = _load_snapshot_or_exit(snapshot_file)
        try:
            _check_staleness(
                snapshot,
                stale_after_seconds=policy.syslog.stale_after_seconds,
                allow_stale=allow_stale,
            )
        except ValueError as exc:
            typer.echo(f"Metrics snapshot error: {exc}", err=True)
            raise typer.Exit(2) from exc

    if not policy.enabled or not policy.syslog.enabled:
        typer.echo(DISABLED_SYSLOG_TEXT, nl=False)
        return
    if snapshot is None:
        typer.echo(
            "Syslog export error: enabled syslog export requires a metrics snapshot", err=True
        )
        raise typer.Exit(2)

    if dry_run:
        try:
            rendered = render_syslog_messages(snapshot, policy)
        except (ConfigurationError, ValueError) as exc:
            typer.echo(f"Syslog render error: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(rendered, nl=False)
        return

    try:
        result = export_syslog_metrics(snapshot, policy)
    except (ConfigurationError, ValueError) as exc:
        typer.echo(f"Syslog export error: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(
        "Syslog export: "
        f"attempted={str(result.attempted).lower()} "
        f"delivered={str(result.delivered).lower()} "
        f"attempts={result.attempts} "
        f"messages={result.messages} "
        f"message={result.message}"
    )
    if result.message == EMPTY_SYSLOG_TEXT.strip():
        return
    if not result.delivered:
        raise typer.Exit(3)


@app.command("nats-monitoring-poll")
def nats_monitoring_poll(
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the sanitized NATS monitoring snapshot to this JSON file.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Write the sanitized snapshot to stdout."),
    ] = False,
) -> None:
    """Poll explicitly allowed NATS monitoring endpoints.

    This command belongs to the observability plane.  It never ACKs messages,
    connects to destination sinks, or changes delivery behavior.  It collects
    only endpoint paths and field values selected by the observability policy.
    """

    policy = _load_policy_or_exit(policy_file)
    try:
        snapshot = collect_nats_monitoring_snapshot(policy)
    except (ConfigurationError, NatsMonitoringError) as exc:
        typer.echo(f"NATS monitoring error: {exc}", err=True)
        raise typer.Exit(2) from exc

    if output is None or dry_run:
        typer.echo(json.dumps(snapshot, indent=2, sort_keys=True))
        return
    try:
        write_nats_monitoring_snapshot(snapshot, output)
    except OSError as exc:
        typer.echo(f"NATS monitoring snapshot write error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Wrote NATS monitoring snapshot: {output}")


@app.command("nats-monitoring-prometheus")
def nats_monitoring_prometheus(
    snapshot_file: Annotated[
        Path,
        typer.Argument(help="NATS monitoring snapshot JSON written by nats-sink-observe."),
    ],
    policy_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write Prometheus text to this file instead of stdout.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Render to stdout even when output is configured."),
    ] = False,
) -> None:
    """Render NATS monitoring values as policy-controlled Prometheus text."""

    policy = _load_policy_or_exit(policy_file)
    snapshot: dict[str, object] | None = None
    if (
        policy.enabled
        and policy.nats_server_monitoring.enabled
        and policy.nats_server_monitoring.prometheus_enabled
    ):
        snapshot = _load_nats_monitoring_snapshot_or_exit(snapshot_file)

    try:
        rendered = render_nats_monitoring_prometheus(snapshot, policy)
    except ValueError as exc:
        typer.echo(f"NATS monitoring Prometheus render error: {exc}", err=True)
        raise typer.Exit(2) from exc

    if output is None or dry_run:
        typer.echo(rendered, nl=False)
        return
    try:
        write_prometheus_textfile(rendered, output)
    except OSError as exc:
        typer.echo(f"NATS monitoring Prometheus write error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Wrote NATS monitoring Prometheus textfile: {output}")


if __name__ == "__main__":
    app()
