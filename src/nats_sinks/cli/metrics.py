# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Standalone CLI for inspecting nats-sinks metrics snapshots.

The main `nats-sink` CLI runs sink processes.  This companion command reads a
local JSON snapshot written by `JsonFileMetrics` and renders it in formats that
are easy for operators, developers, and shell scripts to consume.  It never
connects to NATS, Oracle, or a sink backend.
"""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from nats_sinks import __version__
from nats_sinks.core.metrics import (
    METRIC_SPEC_BY_NAME,
    METRIC_SPECS,
    MetricRow,
    MetricRowKind,
    load_metrics_snapshot,
    metric_rows_from_snapshot,
    qualified_metric_name,
    validate_metric_namespace,
)

app = typer.Typer(help="Inspect nats-sinks metrics snapshots.")

OutputFormat = Literal["table", "json", "jsonl", "shell", "prometheus", "names"]
MetricKindFilter = Literal["all", "counter", "gauge", "observation"]
SortMode = Literal["name", "kind", "value"]


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
    """Inspect dependency-free JSON metrics snapshots."""
    _ = version


def _load_or_exit(snapshot_file: Path) -> dict[str, object]:
    try:
        return load_metrics_snapshot(snapshot_file)
    except ValueError as exc:
        typer.echo(f"Metrics snapshot error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def _snapshot_age_seconds(snapshot: dict[str, object]) -> float:
    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be numeric")
    return max(time.time() - float(generated), 0.0)


def _sort_by_name(row: MetricRow) -> str:
    return row.name


def _sort_by_kind(row: MetricRow) -> tuple[str, str]:
    return (row.kind, row.name)


def _sort_by_value(row: MetricRow) -> tuple[float, str]:
    return (row.value, row.name)


def _check_staleness(
    snapshot: dict[str, object],
    *,
    stale_after_seconds: float | None,
    fail_on_stale: bool,
) -> None:
    if stale_after_seconds is None:
        return
    age = _snapshot_age_seconds(snapshot)
    if age <= stale_after_seconds:
        return
    typer.echo(
        f"Metrics snapshot is stale: age={age:.1f}s limit={stale_after_seconds:.1f}s",
        err=True,
    )
    if fail_on_stale:
        raise typer.Exit(3)


def _filter_rows(
    rows: list[MetricRow],
    *,
    kind: MetricKindFilter,
    metrics: list[str] | None,
    sort: SortMode,
    reverse: bool,
) -> list[MetricRow]:
    filtered = [row for row in rows if kind in ("all", row.kind)]
    patterns = metrics or []
    if patterns:
        filtered = [
            row
            for row in filtered
            if any(fnmatch.fnmatchcase(row.name, pattern) for pattern in patterns)
        ]
    if sort == "kind":
        return sorted(filtered, key=_sort_by_kind, reverse=reverse)
    if sort == "value":
        return sorted(filtered, key=_sort_by_value, reverse=reverse)
    return sorted(filtered, key=_sort_by_name, reverse=reverse)


def _rows_as_json(snapshot: dict[str, object], rows: list[MetricRow]) -> str:
    return json.dumps(
        {
            "schema": snapshot["schema"],
            "namespace": snapshot["namespace"],
            "generated_at_epoch_seconds": snapshot["generated_at_epoch_seconds"],
            "metrics": [
                {
                    "kind": row.kind,
                    "name": row.name,
                    "value": row.value,
                    "stat": row.stat,
                    "description": row.description,
                }
                for row in rows
            ],
        },
        indent=2,
        sort_keys=True,
    )


def _render_table(rows: list[MetricRow]) -> str:
    if not rows:
        return "KIND  METRIC  VALUE  DESCRIPTION"
    kind_width = max(len("KIND"), *(len(row.kind) for row in rows))
    name_width = max(len("METRIC"), *(len(row.name) for row in rows))
    value_width = max(len("VALUE"), *(len(_format_number(row.value)) for row in rows))
    lines = [
        f"{'KIND':<{kind_width}}  {'METRIC':<{name_width}}  {'VALUE':>{value_width}}  DESCRIPTION"
    ]
    for row in rows:
        lines.append(
            f"{row.kind:<{kind_width}}  {row.name:<{name_width}}  "
            f"{_format_number(row.value):>{value_width}}  {row.description}"
        )
    return "\n".join(lines)


def _render_prometheus(
    rows: list[MetricRow],
    *,
    namespace: str,
) -> str:
    lines: list[str] = []
    emitted: set[str] = set()
    for row in rows:
        if row.kind == "observation":
            base_name, _, stat = row.name.rpartition(".")
            metric_name = qualified_metric_name(base_name, namespace=namespace)
            if metric_name not in emitted:
                spec = METRIC_SPEC_BY_NAME.get(base_name)
                description = spec.description if spec is not None else row.description
                lines.append(f"# HELP {metric_name} {description}")
                lines.append(f"# TYPE {metric_name} summary")
                emitted.add(metric_name)
            lines.append(f"{metric_name}_{stat} {_format_number(row.value)}")
            continue
        metric_name = qualified_metric_name(row.name, namespace=namespace)
        if metric_name not in emitted:
            lines.append(f"# HELP {metric_name} {row.description}")
            lines.append(f"# TYPE {metric_name} {row.kind}")
            emitted.add(metric_name)
        lines.append(f"{metric_name} {_format_number(row.value)}")
    return "\n".join(lines)


def _render_rows(
    snapshot: dict[str, object],
    rows: list[MetricRow],
    *,
    output_format: OutputFormat,
    namespace: str | None,
) -> str:
    if output_format == "json":
        return _rows_as_json(snapshot, rows)
    if output_format == "jsonl":
        return "\n".join(
            json.dumps(
                {
                    "kind": row.kind,
                    "name": row.name,
                    "value": row.value,
                    "stat": row.stat,
                    "description": row.description,
                },
                sort_keys=True,
            )
            for row in rows
        )
    if output_format == "shell":
        return "\n".join(f"{row.shell_name}={_format_number(row.value)}" for row in rows)
    if output_format == "prometheus":
        rendered_namespace = namespace or str(snapshot["namespace"])
        return _render_prometheus(rows, namespace=validate_metric_namespace(rendered_namespace))
    if output_format == "names":
        return "\n".join(row.name for row in rows)
    return _render_table(rows)


@app.command()
def show(
    snapshot_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: table, json, jsonl, shell, prometheus, or names.",
        ),
    ] = "table",
    kind: Annotated[
        MetricKindFilter,
        typer.Option("--kind", "-k", help="Limit output to one metric kind."),
    ] = "all",
    metric: Annotated[
        list[str] | None,
        typer.Option("--metric", "-m", help="Metric glob to include; may be repeated."),
    ] = None,
    include_legacy: Annotated[
        bool,
        typer.Option("--include-legacy", help="Include legacy compatibility aliases."),
    ] = False,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Override namespace for Prometheus output."),
    ] = None,
    sort: Annotated[
        SortMode,
        typer.Option("--sort", help="Sort by name, kind, or value."),
    ] = "name",
    reverse: Annotated[bool, typer.Option("--reverse", help="Reverse the selected sort.")] = False,
    stale_after_seconds: Annotated[
        float | None,
        typer.Option("--stale-after-seconds", help="Fail if the snapshot is older than this."),
    ] = None,
    allow_stale: Annotated[
        bool,
        typer.Option("--allow-stale", help="Warn but do not fail when the snapshot is stale."),
    ] = False,
) -> None:
    """Show metrics from a JSON snapshot in human or script-friendly formats."""

    snapshot = _load_or_exit(snapshot_file)
    try:
        _check_staleness(
            snapshot,
            stale_after_seconds=stale_after_seconds,
            fail_on_stale=not allow_stale,
        )
        rows = metric_rows_from_snapshot(snapshot, include_legacy=include_legacy)
        rows = _filter_rows(rows, kind=kind, metrics=metric, sort=sort, reverse=reverse)
        typer.echo(_render_rows(snapshot, rows, output_format=output_format, namespace=namespace))
    except ValueError as exc:
        typer.echo(f"Metrics display error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def get(
    snapshot_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    metric_name: Annotated[str, typer.Argument(help="Metric row name to print.")],
    include_legacy: Annotated[
        bool,
        typer.Option("--include-legacy", help="Allow lookup of legacy compatibility aliases."),
    ] = False,
    default: Annotated[
        str | None,
        typer.Option("--default", help="Value to print when the metric is missing."),
    ] = None,
) -> None:
    """Print one metric value for shell scripts."""

    snapshot = _load_or_exit(snapshot_file)
    rows = metric_rows_from_snapshot(snapshot, include_legacy=include_legacy)
    for row in rows:
        if row.name == metric_name:
            typer.echo(_format_number(row.value))
            return
    if default is not None:
        typer.echo(default)
        return
    typer.echo(f"Metric not found: {metric_name}", err=True)
    raise typer.Exit(4)


@app.command()
def describe(
    output_format: Annotated[
        Literal["table", "json", "names"],
        typer.Option("--format", "-f", help="Output format: table, json, or names."),
    ] = "table",
) -> None:
    """Describe the metrics emitted by nats-sinks."""

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
    rows = [
        MetricRow(
            kind=cast(MetricRowKind, spec.kind if spec.kind != "histogram" else "observation"),
            name=spec.name,
            value=0.0,
            description=spec.description,
        )
        for spec in METRIC_SPECS
    ]
    typer.echo(_render_table(rows))


if __name__ == "__main__":
    app()
