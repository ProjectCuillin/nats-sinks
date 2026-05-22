# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prometheus textfile connector for nats-sinks observability.

This module deliberately implements the smallest useful Prometheus integration:
it converts a local metrics snapshot into Prometheus exposition text that can be
read by node_exporter's textfile collector.  It does not run an HTTP server and
it does not connect to NATS or a destination sink.  That split lets operators run
the sink process and the Prometheus publishing process as separate Linux
services with different permissions, schedules, and filesystem access.

The connector is controlled by an explicit allow-list policy.  By default it
exports nothing, including when a policy file has been generated from a real
runtime configuration.  This avoids accidental disclosure of operational
details in mission, defence, or other sensitive environments where even metric
names, counts, and timing values can reveal activity patterns.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from nats_sinks.core.metrics import (
    METRIC_SPEC_BY_NAME,
    MetricRow,
    metric_rows_from_snapshot,
    qualified_metric_name,
    validate_metric_namespace,
)
from nats_sinks.observability.policy import ObservabilityPolicy

DISABLED_PROMETHEUS_TEXT = "# nats-sinks Prometheus export disabled by observability policy\n"
EMPTY_PROMETHEUS_TEXT = "# nats-sinks Prometheus export produced no allowed metrics\n"


def _format_number(value: float) -> str:
    """Render metric values compactly while preserving Prometheus readability."""

    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def _base_metric_name(row: MetricRow) -> str:
    """Return the canonical metric name for policy checks.

    Observation rows are flattened as `metric.stat` by the metrics snapshot
    helper.  Policy authors should allow or deny `metric`, but exact row names
    such as `metric.count` are accepted as well so shell-generated policies can
    be precise when needed.
    """

    if row.kind == "observation":
        base_name, _, _stat = row.name.rpartition(".")
        return base_name
    return row.name


def _matches_any(value: str, patterns: list[str]) -> bool:
    """Return whether a metric name matches one of the configured glob rules."""

    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _is_allowed(row: MetricRow, policy: ObservabilityPolicy) -> bool:
    """Apply allow-list and deny-list decisions for one flattened metric row."""

    if row.kind == "observation" and not policy.include_observations:
        return False

    base_name = _base_metric_name(row)
    allowed = (
        row.name in policy.allowed_metrics
        or base_name in policy.allowed_metrics
        or _matches_any(row.name, policy.allowed_metric_patterns)
        or _matches_any(base_name, policy.allowed_metric_patterns)
    )
    if not allowed:
        return False
    denied = (
        row.name in policy.denied_metrics
        or base_name in policy.denied_metrics
        or _matches_any(row.name, policy.denied_metric_patterns)
        or _matches_any(base_name, policy.denied_metric_patterns)
    )
    return not denied


def filter_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return the snapshot rows allowed by an observability policy.

    The function is intentionally pure and deterministic so it is easy to test.
    It never mutates the policy or snapshot.  Subject-specific policy entries
    are retained for operator review and future subject-aware metrics, but the
    current core metric snapshot does not contain subject labels and therefore
    cannot safely emit per-subject Prometheus series.
    """

    rows = metric_rows_from_snapshot(snapshot, include_legacy=policy.include_legacy)
    return [row for row in rows if _is_allowed(row, policy)]


def render_prometheus_textfile(
    snapshot: dict[str, object] | None,
    policy: ObservabilityPolicy,
) -> str:
    """Render Prometheus exposition text for allowed metrics.

    A disabled policy returns a harmless explanatory comment and does not
    require a metrics snapshot.  An enabled policy requires the caller to supply
    a validated snapshot and then exports only metrics explicitly allowed by the
    policy.  No payload, headers, subjects, usernames, table names, file paths,
    or connection details are added as labels by this connector.
    """

    if not policy.enabled or not policy.prometheus.enabled:
        return DISABLED_PROMETHEUS_TEXT
    if snapshot is None:
        raise ValueError("an enabled Prometheus policy requires a metrics snapshot")

    namespace = validate_metric_namespace(policy.namespace)
    rows = filter_metric_rows(snapshot, policy)
    if not rows:
        return EMPTY_PROMETHEUS_TEXT

    lines: list[str] = []
    emitted: set[str] = set()
    for row in rows:
        if row.kind == "observation":
            base_name = _base_metric_name(row)
            _base, _separator, stat = row.name.rpartition(".")
            metric_name = qualified_metric_name(base_name, namespace=namespace)
            if metric_name not in emitted:
                spec = METRIC_SPEC_BY_NAME.get(base_name)
                description = spec.description if spec is not None else row.description
                if policy.prometheus.include_help:
                    lines.append(f"# HELP {metric_name} {description}")
                if policy.prometheus.include_type:
                    lines.append(f"# TYPE {metric_name} summary")
                emitted.add(metric_name)
            lines.append(f"{metric_name}_{stat} {_format_number(row.value)}")
            continue

        metric_name = qualified_metric_name(row.name, namespace=namespace)
        if metric_name not in emitted:
            if policy.prometheus.include_help:
                lines.append(f"# HELP {metric_name} {row.description}")
            if policy.prometheus.include_type:
                lines.append(f"# TYPE {metric_name} {row.kind}")
            emitted.add(metric_name)
        lines.append(f"{metric_name} {_format_number(row.value)}")
    return "\n".join(lines) + "\n"


def write_prometheus_textfile(
    text: str,
    path: str | os.PathLike[str],
) -> None:
    """Write Prometheus exposition text using atomic replacement.

    The generated file is world-readable by default because node_exporter often
    runs as a different low-privilege user from the sink service.  The content is
    policy-filtered before this function is called, so operators should grant
    write permission only to the observability service account and read
    permission to the node_exporter account.
    """

    destination = Path(path).expanduser()
    if destination.name in {"", ".", ".."}:
        raise ValueError("Prometheus textfile output path must name a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, destination)
    finally:
        if temp_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)
