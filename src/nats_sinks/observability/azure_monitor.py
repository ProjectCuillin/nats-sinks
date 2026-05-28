# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Azure Monitor custom metrics observability connector.

This connector exports policy-approved aggregate metrics from a local
`nats-sinks` metrics snapshot to Azure Monitor custom metrics. It belongs to
the observability plane: it never connects to NATS, never reads sink payloads
or destination records, and never participates in JetStream ACK, NAK, DLQ,
retry, fan-out, idempotency, or sink write decisions.

The connector intentionally uses the standard library HTTP stack instead of
adding an Azure SDK dependency to the base package. Operators provide a
Microsoft Entra bearer token through an environment variable, normally obtained
by a managed identity, workload identity, service principal, or local Azure CLI
flow outside this process.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from typing import Any, cast
from urllib import error, request

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow, qualified_metric_name
from nats_sinks.observability.policy import (
    AZURE_MONITOR_MAX_DIMENSIONS,
    AZURE_MONITOR_NAME_MAX_LENGTH,
    ObservabilityPolicy,
)
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_AZURE_MONITOR_TEXT = "Azure Monitor export disabled by observability policy\n"
EMPTY_AZURE_MONITOR_TEXT = "Azure Monitor export produced no allowed metrics\n"
AZURE_MONITOR_PROFILE_NAME = "azure_monitor"
REDACTED_AZURE_MONITOR_VALUE = "<redacted>"
HTTP_ERROR_MIN_STATUS = 400


@dataclass(frozen=True, slots=True)
class AzureMonitorExportResult:
    """Safe result summary returned by the Azure Monitor connector.

    The summary deliberately excludes bearer tokens, resource IDs, regional
    endpoints, dimension values, and exception messages. It can be logged
    without disclosing cloud tenancy or deployment-specific details.
    """

    attempted: bool
    delivered: bool
    attempts: int
    requests: int
    metrics: int
    status_code: int | None
    message: str


def ensure_azure_monitor_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless Azure Monitor export is explicitly enabled."""

    if not policy.enabled or not policy.azure_monitor.enabled:
        raise ConfigurationError("Azure Monitor export is disabled by observability policy")
    if policy.azure_monitor.resource_id is None:
        raise ConfigurationError(
            "azure_monitor.resource_id is required when Azure Monitor export is enabled"
        )
    if policy.azure_monitor.location is None:
        raise ConfigurationError(
            "azure_monitor.location is required when Azure Monitor export is enabled"
        )
    if policy.azure_monitor.token_env is None:
        raise ConfigurationError(
            "azure_monitor.token_env is required when Azure Monitor export is enabled"
        )


def filter_azure_monitor_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for Azure Monitor export by the shared policy model."""

    rows = filter_metric_rows(snapshot, policy)
    if policy.azure_monitor.include_metric_labels_as_dimensions:
        return rows
    return [row for row in rows if not row.labels]


def _snapshot_timestamp(snapshot: dict[str, object]) -> str:
    """Return the snapshot timestamp in ISO 8601 form for Azure datapoints."""

    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float) or not math.isfinite(float(generated)):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be finite numeric")
    rendered = float(generated)
    if rendered < 0:
        raise ValueError("metrics snapshot generated_at_epoch_seconds must not be negative")
    return (
        datetime.fromtimestamp(rendered, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _azure_monitor_metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the Azure Monitor metric name for one approved row."""

    if row.kind == "observation":
        base_name, _separator, stat = row.name.rpartition(".")
        metric_name = f"{qualified_metric_name(base_name, namespace=policy.namespace)}_{stat}"
    else:
        metric_name = qualified_metric_name(row.name, namespace=policy.namespace)
    if len(metric_name) > AZURE_MONITOR_NAME_MAX_LENGTH:
        raise ConfigurationError("Azure Monitor metric name exceeds 255 characters")
    if not metric_name[0].isalpha():
        raise ConfigurationError("Azure Monitor metric names must start with a letter")
    return metric_name


def _azure_monitor_dimensions(row: MetricRow, policy: ObservabilityPolicy) -> dict[str, str]:
    """Return static and optional prepared dimensions for one approved metric row."""

    dimension_pairs = dict(policy.azure_monitor.dimensions)
    if policy.azure_monitor.include_metric_labels_as_dimensions:
        dimension_pairs.update(row.labels)
    if len(dimension_pairs) > AZURE_MONITOR_MAX_DIMENSIONS:
        raise ConfigurationError("Azure Monitor dimensions exceed the configured safety cap")
    return dict(sorted(dimension_pairs.items(), key=lambda item: item[0].lower()))


def _metric_document(
    row: MetricRow,
    policy: ObservabilityPolicy,
    *,
    timestamp: str,
) -> dict[str, object]:
    """Translate one policy-approved metric row into an Azure metric document."""

    value = float(row.value)
    if not math.isfinite(value):
        raise ValueError(f"metric row {row.name} contains a non-finite value")
    dimensions = _azure_monitor_dimensions(row, policy)
    series: dict[str, object] = {
        "dimValues": list(dimensions.values()),
        "min": value,
        "max": value,
        "sum": value,
        "count": 1,
    }
    return {
        "time": timestamp,
        "data": {
            "baseData": {
                "metric": _azure_monitor_metric_name(row, policy),
                "namespace": policy.azure_monitor.metric_namespace,
                "dimNames": list(dimensions.keys()),
                "series": [series],
            }
        },
    }


def build_azure_monitor_metric_documents(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded Azure Monitor metric documents from an approved snapshot."""

    ensure_azure_monitor_enabled(policy)
    timestamp = _snapshot_timestamp(snapshot)
    return [
        _metric_document(row, policy, timestamp=timestamp)
        for row in filter_azure_monitor_metric_rows(snapshot, policy)
    ]


def _request_size_bytes(request_body: dict[str, object]) -> int:
    """Return the exact JSON size used for local request-size enforcement."""

    return len(json.dumps(request_body, separators=(",", ":"), sort_keys=True, allow_nan=False))


def build_azure_monitor_metric_requests(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded Azure Monitor request dictionaries.

    Each dictionary contains exactly one metric document because the Azure
    custom-metrics REST API posts one metric name per request body. The request
    preview intentionally excludes the regional endpoint, resource ID, bearer
    token, subject, payload, file path, table name, classification value, and
    message ID.
    """

    requests: list[dict[str, object]] = []
    for document in build_azure_monitor_metric_documents(snapshot, policy):
        if _request_size_bytes(document) > policy.azure_monitor.max_request_bytes:
            raise ConfigurationError(
                "Azure Monitor custom metric request exceeds azure_monitor.max_request_bytes; "
                "reduce the allow list, dimensions, or namespace length"
            )
        requests.append(document)
    return requests


def _sanitized_request(request_body: dict[str, object]) -> dict[str, object]:
    """Return a dry-run safe Azure Monitor request preview."""

    return cast(
        dict[str, object],
        json.loads(json.dumps(request_body, separators=(",", ":"), sort_keys=True)),
    )


def render_azure_monitor_metric_requests_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render a sanitized Azure Monitor dry-run request list as bounded JSON."""

    requests = [
        _sanitized_request(request_body)
        for request_body in build_azure_monitor_metric_requests(snapshot, policy)
    ]
    return json.dumps(requests, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )


def _environment_value(env_name: str, *, purpose: str) -> str:
    """Load one secret-like environment value with common safety checks."""

    value = os.getenv(env_name)
    if value is None:
        raise ConfigurationError(f"environment variable {env_name} for {purpose} is not set")
    if value == "":
        raise ConfigurationError(f"environment variable {env_name} for {purpose} is empty")
    if any(character in value for character in "\x00\n\r"):
        raise ConfigurationError(
            f"environment variable {env_name} for {purpose} contains control characters"
        )
    return value


def resolve_azure_monitor_headers(policy: ObservabilityPolicy) -> dict[str, str]:
    """Resolve Azure Monitor request headers from environment-backed configuration."""

    ensure_azure_monitor_enabled(policy)
    token_env = policy.azure_monitor.token_env
    if token_env is None:
        raise ConfigurationError(
            "azure_monitor.token_env is required when azure_monitor.enabled is true"
        )
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_environment_value(token_env, purpose='Azure Monitor token')}",
    }


def azure_monitor_metrics_endpoint(policy: ObservabilityPolicy) -> str:
    """Return the Azure Monitor regional metric endpoint for live export."""

    ensure_azure_monitor_enabled(policy)
    location = policy.azure_monitor.location
    resource_id = policy.azure_monitor.resource_id
    if location is None or resource_id is None:
        raise ConfigurationError("azure_monitor.location and resource_id are required")
    return f"https://{location}.monitoring.azure.com{resource_id}/metrics"


def _safe_failure_message(exc: BaseException) -> tuple[int | None, str]:
    """Return a sanitized failure category for CLI output and logs."""

    if isinstance(exc, error.HTTPError):
        return exc.code, f"Azure Monitor returned HTTP status {exc.code}"
    return None, f"Azure Monitor export failed with {type(exc).__name__}"


def _payload_bytes(request_body: dict[str, object]) -> bytes:
    """Render one already bounded Azure Monitor request body."""

    return json.dumps(request_body, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )


def _post_requests_once(
    requests_: Sequence[dict[str, object]],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any],
) -> int:
    """Send all approved request bodies once and return the final HTTP status."""

    endpoint = azure_monitor_metrics_endpoint(policy)
    headers = resolve_azure_monitor_headers(policy)
    last_status = 0
    for request_body in requests_:
        req = request.Request(  # noqa: S310 # nosec B310
            endpoint,
            data=_payload_bytes(request_body),
            headers=headers,
            method="POST",
        )
        with opener(req, timeout=policy.azure_monitor.timeout_seconds) as response:
            last_status = int(response.getcode())
            if last_status >= HTTP_ERROR_MIN_STATUS:
                raise error.HTTPError(
                    endpoint,
                    last_status,
                    "Azure Monitor returned an unsuccessful status",
                    hdrs=Message(),
                    fp=None,
                )
    return last_status


def export_azure_monitor_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> AzureMonitorExportResult:
    """Export approved metrics to Azure Monitor with bounded retries."""

    if not policy.enabled or not policy.azure_monitor.enabled:
        return AzureMonitorExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            requests=0,
            metrics=0,
            status_code=None,
            message=DISABLED_AZURE_MONITOR_TEXT.strip(),
        )

    rows = filter_azure_monitor_metric_rows(snapshot, policy)
    if not rows:
        return AzureMonitorExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            requests=0,
            metrics=0,
            status_code=None,
            message=EMPTY_AZURE_MONITOR_TEXT.strip(),
        )

    requests_ = build_azure_monitor_metric_requests(snapshot, policy)
    selected_opener = opener or request.urlopen  # nosec B310
    max_attempts = policy.azure_monitor.max_retries + 1
    last_status: int | None = None
    last_message = "Azure Monitor export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            status_code = _post_requests_once(requests_, policy, opener=selected_opener)
            return AzureMonitorExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                requests=len(requests_),
                metrics=len(rows),
                status_code=status_code,
                message="Azure Monitor export delivered",
            )
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            last_status, last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.azure_monitor.retry_backoff_seconds > 0:
            sleep(policy.azure_monitor.retry_backoff_seconds)

    return AzureMonitorExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        requests=len(requests_),
        metrics=len(rows),
        status_code=last_status,
        message=last_message,
    )
