# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenTelemetry OTLP metrics connector.

This module exports policy-approved nats-sinks metrics to an OpenTelemetry
collector using OTLP over HTTP with JSON encoding.  It intentionally depends
only on the Python standard library: production deployments can send metrics to
an already-managed collector without adding another telemetry SDK to the sink
worker or changing message-delivery semantics.

The connector reads a local metrics snapshot and is designed to run from the
separate `nats-sink-observe` CLI.  It never connects to NATS, never talks to a
destination sink, never reads message payloads, and never ACKs or NAKs a
JetStream message.  Export failure is therefore an observability failure, not a
delivery success or delivery failure.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow, qualified_metric_name
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_OTLP_TEXT = "OTLP export disabled by observability policy\n"
EMPTY_OTLP_TEXT = "OTLP export produced no allowed metrics\n"
OTLP_SCOPE_NAME = "nats-sinks.observability.otlp"
HTTP_ERROR_MIN_STATUS = 400


@dataclass(frozen=True, slots=True)
class OtlpExportResult:
    """Safe result summary returned by the OTLP connector.

    The result deliberately excludes endpoint URLs and header values.  Operators
    should be able to log the result without leaking collector locations,
    bearer tokens, or other deployment-specific details.
    """

    attempted: bool
    delivered: bool
    attempts: int
    status_code: int | None
    message: str


def ensure_otlp_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless both the global policy and OTLP connector are enabled."""

    if not policy.enabled or not policy.otlp.enabled:
        raise ConfigurationError("OTLP export is disabled by observability policy")
    if policy.otlp.endpoint is None:
        raise ConfigurationError("OTLP endpoint is required when OTLP export is enabled")


def filter_otlp_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for OTLP export by the shared policy model."""

    return filter_metric_rows(snapshot, policy)


def _snapshot_time_unix_nano(snapshot: dict[str, object]) -> str:
    """Return the snapshot timestamp in the OTLP nanosecond string format."""

    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float) or not math.isfinite(float(generated)):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be finite numeric")
    if generated < 0:
        raise ValueError("metrics snapshot generated_at_epoch_seconds must not be negative")
    return str(int(float(generated) * 1_000_000_000))


def _metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return a stable exported metric name for one row."""

    if row.kind == "observation":
        base_name, _separator, stat = row.name.rpartition(".")
        return f"{qualified_metric_name(base_name, namespace=policy.namespace)}_{stat}"
    return qualified_metric_name(row.name, namespace=policy.namespace)


def _data_point(row: MetricRow, *, time_unix_nano: str) -> dict[str, object]:
    """Build one OTLP NumberDataPoint without labels or sensitive attributes."""

    return {
        "timeUnixNano": time_unix_nano,
        "asDouble": row.value,
    }


def _row_to_otlp_metric(
    row: MetricRow,
    policy: ObservabilityPolicy,
    *,
    time_unix_nano: str,
) -> dict[str, object]:
    """Translate one policy-approved row into an OTLP metric object."""

    metric_name = _metric_name(row, policy)
    data_point = _data_point(row, time_unix_nano=time_unix_nano)
    if row.kind == "counter":
        return {
            "name": metric_name,
            "description": row.description,
            "unit": "1",
            "sum": {
                "aggregationTemporality": 2,
                "isMonotonic": True,
                "dataPoints": [data_point],
            },
        }
    return {
        "name": metric_name,
        "description": row.description,
        "unit": "1",
        "gauge": {"dataPoints": [data_point]},
    }


def _resource_attributes(
    policy: ObservabilityPolicy,
    *,
    extra_resource_attributes: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Return low-cardinality resource attributes for an OTLP document."""

    attributes: list[dict[str, object]] = [
        {"key": "service.name", "value": {"stringValue": "nats-sinks"}},
        {
            "key": "nats_sinks.namespace",
            "value": {"stringValue": policy.namespace},
        },
    ]
    if extra_resource_attributes is None:
        return attributes
    for key, value in extra_resource_attributes.items():
        attributes.append({"key": key, "value": {"stringValue": value}})
    return attributes


def build_otlp_metrics_document(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    scope_name: str = OTLP_SCOPE_NAME,
    extra_resource_attributes: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build an OTLP/HTTP JSON metrics request body.

    The function builds only low-cardinality, policy-approved metric series. It
    does not add subject labels, table names, file paths, payload snippets,
    usernames, collector endpoints, or other sensitive operational details. The
    optional resource attributes are intended for connector profiles that need
    static routing hints, such as Elastic data stream names; callers must keep
    those values bounded and free of secrets before passing them here.
    """

    ensure_otlp_enabled(policy)
    time_unix_nano = _snapshot_time_unix_nano(snapshot)
    metrics = [
        _row_to_otlp_metric(row, policy, time_unix_nano=time_unix_nano)
        for row in filter_otlp_metric_rows(snapshot, policy)
    ]
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": _resource_attributes(
                        policy,
                        extra_resource_attributes=extra_resource_attributes,
                    )
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": scope_name},
                        "metrics": metrics,
                    }
                ],
            }
        ]
    }


def render_otlp_metrics_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    scope_name: str = OTLP_SCOPE_NAME,
    extra_resource_attributes: Mapping[str, str] | None = None,
) -> bytes:
    """Render a bounded OTLP/HTTP JSON request body."""

    document = build_otlp_metrics_document(
        snapshot,
        policy,
        scope_name=scope_name,
        extra_resource_attributes=extra_resource_attributes,
    )
    rendered = json.dumps(document, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )
    if len(rendered) > policy.otlp.max_request_bytes:
        raise ConfigurationError(
            "OTLP request body exceeds otlp.max_request_bytes; reduce the allow list "
            "or increase the configured bound after review"
        )
    return rendered


def resolve_otlp_headers(policy: ObservabilityPolicy) -> dict[str, str]:
    """Resolve static OTLP headers and optional secret header values from env vars."""

    headers = {"Content-Type": "application/json"}
    for header_name, env_name in policy.otlp.headers_env.items():
        value = os.getenv(env_name)
        if value is None:
            raise ConfigurationError(
                f"environment variable {env_name} for OTLP header {header_name} is not set"
            )
        if value == "":
            raise ConfigurationError(
                f"environment variable {env_name} for OTLP header {header_name} is empty"
            )
        if any(character in value for character in "\x00\n\r"):
            raise ConfigurationError(
                f"environment variable {env_name} for OTLP header {header_name} "
                "contains control characters"
            )
        headers[header_name] = value
    return headers


def _safe_failure_message(exc: BaseException) -> tuple[int | None, str]:
    """Return a sanitized failure category for CLI output and logs."""

    if isinstance(exc, error.HTTPError):
        return exc.code, f"OTLP collector returned HTTP status {exc.code}"
    return None, f"OTLP export failed with {type(exc).__name__}"


def export_otlp_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    scope_name: str = OTLP_SCOPE_NAME,
    extra_resource_attributes: Mapping[str, str] | None = None,
    connector_name: str = "OTLP",
) -> OtlpExportResult:
    """Send policy-approved metrics to an OTLP collector.

    Network errors are summarized without endpoint or header details. Retries
    are bounded by `policy.otlp.max_retries`; the first attempt is not counted
    as a retry. A disabled policy or an empty allow list is treated as a safe
    no-op so observability cannot interfere with message delivery.
    """

    if not policy.enabled or not policy.otlp.enabled:
        return OtlpExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            status_code=None,
            message=DISABLED_OTLP_TEXT.strip(),
        )

    if not filter_otlp_metric_rows(snapshot, policy):
        return OtlpExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            status_code=None,
            message=EMPTY_OTLP_TEXT.strip(),
        )

    endpoint = policy.otlp.endpoint
    if endpoint is None:
        raise ConfigurationError("OTLP endpoint is required when OTLP export is enabled")

    body = render_otlp_metrics_json(
        snapshot,
        policy,
        scope_name=scope_name,
        extra_resource_attributes=extra_resource_attributes,
    )
    headers = resolve_otlp_headers(policy)
    selected_opener = opener or request.urlopen  # nosec B310
    max_attempts = policy.otlp.max_retries + 1
    last_status: int | None = None
    last_message = "OTLP export did not run"

    for attempt in range(1, max_attempts + 1):
        req = request.Request(  # noqa: S310 # nosec B310
            endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with selected_opener(req, timeout=policy.otlp.timeout_seconds) as response:
                status_code = int(response.getcode())
                if status_code >= HTTP_ERROR_MIN_STATUS:
                    last_status = status_code
                    last_message = f"OTLP collector returned HTTP status {status_code}"
                else:
                    return OtlpExportResult(
                        attempted=True,
                        delivered=True,
                        attempts=attempt,
                        status_code=status_code,
                        message=f"{connector_name} export delivered",
                    )
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            last_status, last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.otlp.retry_backoff_seconds > 0:
            sleep(policy.otlp.retry_backoff_seconds)

    return OtlpExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        status_code=last_status,
        message=last_message,
    )
