# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Observability policy and connector helpers.

The observability package is intentionally separate from the core runner and
destination sinks.  The runner emits small, safe metric names; observability
connectors decide what, if anything, is shared with external platforms such as
Prometheus.  This keeps delivery semantics independent from monitoring tools
and lets operators run observability as a separate Linux service.
"""

from nats_sinks.observability.elastic import (
    DISABLED_ELASTIC_TEXT,
    ELASTIC_OTLP_SCOPE_NAME,
    EMPTY_ELASTIC_TEXT,
    build_elastic_otlp_metrics_document,
    ensure_elastic_enabled,
    export_elastic_observability_metrics,
    filter_elastic_metric_rows,
    render_elastic_otlp_metrics_json,
    resolve_elastic_headers,
)
from nats_sinks.observability.nats_monitoring import (
    NATS_MONITORING_SNAPSHOT_SCHEMA,
    NatsMonitoringEndpointObservation,
    NatsMonitoringError,
    build_nats_monitoring_url,
    collect_nats_monitoring_snapshot,
    ensure_nats_monitoring_enabled,
    extract_nats_monitoring_fields,
    load_nats_monitoring_snapshot,
    render_nats_monitoring_prometheus,
    write_nats_monitoring_snapshot,
)
from nats_sinks.observability.otlp import (
    DISABLED_OTLP_TEXT,
    EMPTY_OTLP_TEXT,
    OtlpExportResult,
    build_otlp_metrics_document,
    ensure_otlp_enabled,
    export_otlp_metrics,
    filter_otlp_metric_rows,
    render_otlp_metrics_json,
    resolve_otlp_headers,
)
from nats_sinks.observability.policy import (
    NATS_MONITORING_ALLOWED_ENDPOINTS,
    OBSERVABILITY_POLICY_SCHEMA,
    ElasticObservabilityPolicy,
    NatsServerMonitoringPolicy,
    ObservabilityPolicy,
    ObservabilitySubjectPolicy,
    OtlpMetricsPolicy,
    PrometheusHttpEndpointPolicy,
    PrometheusTextfilePolicy,
    build_policy_from_app_config,
    load_observability_policy,
    observability_policy_template,
    write_observability_policy,
)
from nats_sinks.observability.prometheus import (
    filter_metric_rows,
    render_prometheus_textfile,
    write_prometheus_textfile,
)
from nats_sinks.observability.prometheus_http import (
    PrometheusHttpResponse,
    build_prometheus_http_server,
    ensure_prometheus_http_enabled,
    render_prometheus_http_response,
    serve_prometheus_http,
)

__all__ = [
    "DISABLED_ELASTIC_TEXT",
    "DISABLED_OTLP_TEXT",
    "ELASTIC_OTLP_SCOPE_NAME",
    "EMPTY_ELASTIC_TEXT",
    "EMPTY_OTLP_TEXT",
    "NATS_MONITORING_ALLOWED_ENDPOINTS",
    "NATS_MONITORING_SNAPSHOT_SCHEMA",
    "OBSERVABILITY_POLICY_SCHEMA",
    "ElasticObservabilityPolicy",
    "NatsMonitoringEndpointObservation",
    "NatsMonitoringError",
    "NatsServerMonitoringPolicy",
    "ObservabilityPolicy",
    "ObservabilitySubjectPolicy",
    "OtlpExportResult",
    "OtlpMetricsPolicy",
    "PrometheusHttpEndpointPolicy",
    "PrometheusHttpResponse",
    "PrometheusTextfilePolicy",
    "build_elastic_otlp_metrics_document",
    "build_nats_monitoring_url",
    "build_otlp_metrics_document",
    "build_policy_from_app_config",
    "build_prometheus_http_server",
    "collect_nats_monitoring_snapshot",
    "ensure_elastic_enabled",
    "ensure_nats_monitoring_enabled",
    "ensure_otlp_enabled",
    "ensure_prometheus_http_enabled",
    "export_elastic_observability_metrics",
    "export_otlp_metrics",
    "extract_nats_monitoring_fields",
    "filter_elastic_metric_rows",
    "filter_metric_rows",
    "filter_otlp_metric_rows",
    "load_nats_monitoring_snapshot",
    "load_observability_policy",
    "observability_policy_template",
    "render_elastic_otlp_metrics_json",
    "render_nats_monitoring_prometheus",
    "render_otlp_metrics_json",
    "render_prometheus_http_response",
    "render_prometheus_textfile",
    "resolve_elastic_headers",
    "resolve_otlp_headers",
    "serve_prometheus_http",
    "write_nats_monitoring_snapshot",
    "write_observability_policy",
    "write_prometheus_textfile",
]
