# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Grafana Alloy observability connector profile.

Grafana Alloy support is implemented as a profile over the shared OTLP
observability connector.  nats-sinks renders and exports only policy-approved
metrics to an Alloy `otelcol.receiver.otlp` HTTP listener.  Alloy then owns the
collector-side concerns: batching, queueing, authentication, retry, and
forwarding into Grafana Cloud, Mimir, or any other OTLP-compatible target.

This module deliberately does not manage Alloy as a process, scrape payload
data, read sink output files, or connect to NATS or Oracle.  It is an
observability-plane helper only.  Export failures are therefore never allowed to
change JetStream ACK, NAK, DLQ, retry, sink-write, or idempotency behavior.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow
from nats_sinks.observability.otlp import (
    EMPTY_OTLP_TEXT,
    OtlpExportResult,
    build_otlp_metrics_document,
    export_otlp_metrics,
    filter_otlp_metric_rows,
    render_otlp_metrics_json,
    resolve_otlp_headers,
)
from nats_sinks.observability.policy import ObservabilityPolicy, OtlpMetricsPolicy

DISABLED_GRAFANA_ALLOY_TEXT = "Grafana Alloy export disabled by observability policy\n"
EMPTY_GRAFANA_ALLOY_TEXT = "Grafana Alloy export produced no allowed metrics\n"
GRAFANA_ALLOY_OTLP_SCOPE_NAME = "nats-sinks.observability.grafana_alloy"


def ensure_grafana_alloy_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless the Grafana Alloy profile is explicitly enabled."""

    if not policy.enabled or not policy.grafana_alloy.enabled:
        raise ConfigurationError("Grafana Alloy export is disabled by observability policy")
    if policy.grafana_alloy.endpoint is None:
        raise ConfigurationError(
            "Grafana Alloy endpoint is required when grafana_alloy.enabled is true"
        )


def _grafana_alloy_resource_attributes(policy: ObservabilityPolicy) -> dict[str, str]:
    """Return low-cardinality resource hints for the Alloy profile."""

    _ = policy
    return {
        "nats_sinks.observability.profile": "grafana_alloy",
        "telemetry.collector": "grafana_alloy",
    }


def _grafana_alloy_otlp_policy(policy: ObservabilityPolicy) -> ObservabilityPolicy:
    """Build a temporary OTLP policy from the Grafana Alloy profile settings."""

    return policy.model_copy(
        update={
            "otlp": OtlpMetricsPolicy(
                enabled=policy.grafana_alloy.enabled,
                endpoint=policy.grafana_alloy.endpoint,
                timeout_seconds=policy.grafana_alloy.timeout_seconds,
                max_retries=policy.grafana_alloy.max_retries,
                retry_backoff_seconds=policy.grafana_alloy.retry_backoff_seconds,
                stale_after_seconds=policy.grafana_alloy.stale_after_seconds,
                max_request_bytes=policy.grafana_alloy.max_request_bytes,
                headers_env=policy.grafana_alloy.headers_env,
            )
        }
    )


def filter_grafana_alloy_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for Grafana Alloy export by the shared policy."""

    return filter_otlp_metric_rows(snapshot, _grafana_alloy_otlp_policy(policy))


def build_grafana_alloy_otlp_metrics_document(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> dict[str, object]:
    """Build an OTLP metrics document with Alloy profile resource attributes."""

    ensure_grafana_alloy_enabled(policy)
    return build_otlp_metrics_document(
        snapshot,
        _grafana_alloy_otlp_policy(policy),
        scope_name=GRAFANA_ALLOY_OTLP_SCOPE_NAME,
        extra_resource_attributes=_grafana_alloy_resource_attributes(policy),
    )


def render_grafana_alloy_otlp_metrics_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render the bounded OTLP JSON body used by the Alloy profile."""

    ensure_grafana_alloy_enabled(policy)
    return render_otlp_metrics_json(
        snapshot,
        _grafana_alloy_otlp_policy(policy),
        scope_name=GRAFANA_ALLOY_OTLP_SCOPE_NAME,
        extra_resource_attributes=_grafana_alloy_resource_attributes(policy),
    )


def resolve_grafana_alloy_headers(policy: ObservabilityPolicy) -> dict[str, str]:
    """Resolve local Alloy receiver headers from environment variables."""

    ensure_grafana_alloy_enabled(policy)
    return resolve_otlp_headers(_grafana_alloy_otlp_policy(policy))


def _receiver_listen_endpoint(policy: ObservabilityPolicy) -> str:
    """Return the Alloy receiver `host:port` endpoint for generated config."""

    ensure_grafana_alloy_enabled(policy)
    parsed = urlsplit(policy.grafana_alloy.endpoint or "")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def render_grafana_alloy_config(policy: ObservabilityPolicy) -> str:
    """Render a minimal Alloy River config for the nats-sinks OTLP handoff.

    The generated snippet is intentionally small and safe to publish in
    documentation.  It references upstream Grafana credentials by environment
    variable name only and keeps the Alloy receiver bound to the endpoint
    configured for the nats-sinks export command.
    """

    ensure_grafana_alloy_enabled(policy)
    alloy = policy.grafana_alloy
    receiver_endpoint = _receiver_listen_endpoint(policy)
    lines: list[str] = [
        "// nats-sinks Grafana Alloy profile.",
        "// Keep Alloy separate from the nats-sink delivery worker.",
        f'otelcol.receiver.otlp "{alloy.receiver_label}" {{',
        "  http {",
        f'    endpoint = "{receiver_endpoint}"',
        "  }",
        "",
        "  output {",
        f"    metrics = [otelcol.processor.batch.{alloy.batch_label}.input]",
        "  }",
        "}",
        "",
        f'otelcol.processor.batch "{alloy.batch_label}" {{',
        "  output {",
        f"    metrics = [otelcol.exporter.otlphttp.{alloy.exporter_label}.input]",
        "  }",
        "}",
        "",
    ]
    if alloy.upstream_auth_mode == "basic":
        lines.extend(
            [
                f'otelcol.auth.basic "{alloy.auth_label}" {{',
                "  client_auth {",
                f'    username = sys.env("{alloy.upstream_auth_username_env}")',
                f'    password = sys.env("{alloy.upstream_auth_password_env}")',
                "  }",
                "}",
                "",
            ]
        )

    lines.extend(
        [
            f'otelcol.exporter.otlphttp "{alloy.exporter_label}" {{',
            "  client {",
            f'    endpoint = sys.env("{alloy.upstream_endpoint_env}")',
        ]
    )
    if alloy.upstream_auth_mode == "basic":
        lines.append(f"    auth     = otelcol.auth.basic.{alloy.auth_label}.handler")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def export_grafana_alloy_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> OtlpExportResult:
    """Export approved metrics through the Grafana Alloy OTLP profile."""

    if not policy.enabled or not policy.grafana_alloy.enabled:
        return OtlpExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            status_code=None,
            message=DISABLED_GRAFANA_ALLOY_TEXT.strip(),
        )

    if not filter_grafana_alloy_metric_rows(snapshot, policy):
        return OtlpExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            status_code=None,
            message=EMPTY_GRAFANA_ALLOY_TEXT.strip(),
        )

    if sleep is None:
        result = export_otlp_metrics(
            snapshot,
            _grafana_alloy_otlp_policy(policy),
            opener=opener,
            scope_name=GRAFANA_ALLOY_OTLP_SCOPE_NAME,
            extra_resource_attributes=_grafana_alloy_resource_attributes(policy),
            connector_name="Grafana Alloy",
        )
    else:
        result = export_otlp_metrics(
            snapshot,
            _grafana_alloy_otlp_policy(policy),
            opener=opener,
            sleep=sleep,
            scope_name=GRAFANA_ALLOY_OTLP_SCOPE_NAME,
            extra_resource_attributes=_grafana_alloy_resource_attributes(policy),
            connector_name="Grafana Alloy",
        )
    if result.message == EMPTY_OTLP_TEXT.strip():
        return dataclasses.replace(result, message=EMPTY_GRAFANA_ALLOY_TEXT.strip())
    return result
