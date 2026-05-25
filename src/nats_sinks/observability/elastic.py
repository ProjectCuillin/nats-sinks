# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Elastic Observability connector profile.

Elastic support is implemented as a profile over the shared OTLP observability
connector.  That choice keeps nats-sinks on one policy and serialization path:
operators approve metrics once, nats-sinks renders bounded OTLP metrics, and a
local or gateway OpenTelemetry Collector can forward those metrics to Elastic
Observability using the deployment-specific Elastic exporter or managed OTLP
endpoint.

The profile deliberately does not write directly to Elasticsearch indices or
the Bulk API.  Direct writes would require index naming, mapping, retry, and
partial-failure behavior that duplicates Elastic and OpenTelemetry Collector
responsibilities.  Keeping this module as a profile also preserves the core
project invariant that observability failures never affect JetStream ACK, NAK,
DLQ, retry, sink writes, or idempotency.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

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

DISABLED_ELASTIC_TEXT = "Elastic Observability export disabled by observability policy\n"
EMPTY_ELASTIC_TEXT = "Elastic Observability export produced no allowed metrics\n"
ELASTIC_OTLP_SCOPE_NAME = "nats-sinks.observability.elastic"


def ensure_elastic_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless the Elastic profile is explicitly enabled."""

    if not policy.enabled or not policy.elastic.enabled:
        raise ConfigurationError("Elastic Observability export is disabled by observability policy")
    if policy.elastic.endpoint is None:
        raise ConfigurationError(
            "Elastic Observability endpoint is required when elastic.enabled is true"
        )


def _elastic_resource_attributes(policy: ObservabilityPolicy) -> dict[str, str]:
    """Return bounded Elastic routing hints for the OTLP resource block."""

    return {
        "data_stream.dataset": policy.elastic.data_stream_dataset,
        "data_stream.namespace": policy.elastic.data_stream_namespace,
        "nats_sinks.observability.profile": "elastic",
    }


def _elastic_otlp_policy(policy: ObservabilityPolicy) -> ObservabilityPolicy:
    """Build a temporary OTLP policy from the Elastic profile settings."""

    return policy.model_copy(
        update={
            "otlp": OtlpMetricsPolicy(
                enabled=policy.elastic.enabled,
                endpoint=policy.elastic.endpoint,
                timeout_seconds=policy.elastic.timeout_seconds,
                max_retries=policy.elastic.max_retries,
                retry_backoff_seconds=policy.elastic.retry_backoff_seconds,
                stale_after_seconds=policy.elastic.stale_after_seconds,
                max_request_bytes=policy.elastic.max_request_bytes,
                headers_env=policy.elastic.headers_env,
            )
        }
    )


def filter_elastic_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for Elastic export by the shared policy model."""

    return filter_otlp_metric_rows(snapshot, _elastic_otlp_policy(policy))


def build_elastic_otlp_metrics_document(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> dict[str, object]:
    """Build an OTLP metrics document with Elastic-safe static attributes."""

    ensure_elastic_enabled(policy)
    return build_otlp_metrics_document(
        snapshot,
        _elastic_otlp_policy(policy),
        scope_name=ELASTIC_OTLP_SCOPE_NAME,
        extra_resource_attributes=_elastic_resource_attributes(policy),
    )


def render_elastic_otlp_metrics_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render the bounded OTLP JSON body used by the Elastic profile."""

    ensure_elastic_enabled(policy)
    return render_otlp_metrics_json(
        snapshot,
        _elastic_otlp_policy(policy),
        scope_name=ELASTIC_OTLP_SCOPE_NAME,
        extra_resource_attributes=_elastic_resource_attributes(policy),
    )


def resolve_elastic_headers(policy: ObservabilityPolicy) -> dict[str, str]:
    """Resolve Elastic profile headers from environment variables."""

    ensure_elastic_enabled(policy)
    return resolve_otlp_headers(_elastic_otlp_policy(policy))


def export_elastic_observability_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> OtlpExportResult:
    """Export approved metrics through the Elastic OTLP profile.

    Disabled policies and empty allow lists remain safe no-ops.  The returned
    result mirrors the generic OTLP exporter but uses Elastic wording so CLI
    output and logs are understandable to operators.
    """

    if not policy.enabled or not policy.elastic.enabled:
        return OtlpExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            status_code=None,
            message=DISABLED_ELASTIC_TEXT.strip(),
        )

    if not filter_elastic_metric_rows(snapshot, policy):
        return OtlpExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            status_code=None,
            message=EMPTY_ELASTIC_TEXT.strip(),
        )

    if sleep is None:
        result = export_otlp_metrics(
            snapshot,
            _elastic_otlp_policy(policy),
            opener=opener,
            scope_name=ELASTIC_OTLP_SCOPE_NAME,
            extra_resource_attributes=_elastic_resource_attributes(policy),
            connector_name="Elastic Observability",
        )
    else:
        result = export_otlp_metrics(
            snapshot,
            _elastic_otlp_policy(policy),
            opener=opener,
            sleep=sleep,
            scope_name=ELASTIC_OTLP_SCOPE_NAME,
            extra_resource_attributes=_elastic_resource_attributes(policy),
            connector_name="Elastic Observability",
        )
    if result.message == EMPTY_OTLP_TEXT.strip():
        return dataclasses.replace(result, message=EMPTY_ELASTIC_TEXT.strip())
    return result
