# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Amazon CloudWatch observability connector.

This connector exports policy-approved aggregate metrics from a local
`nats-sinks` metrics snapshot to Amazon CloudWatch custom metrics. It belongs
to the observability plane: it never connects to NATS, never reads sink payloads
or destination records, and never participates in JetStream ACK, NAK, DLQ,
retry, or sink write decisions.

The implementation keeps the AWS SDK optional. Tests and dry-runs can build the
exact `PutMetricData` request shape without importing boto3, while production
deployments install the `cloudwatch` extra and authenticate through normal AWS
SDK credential providers such as workload identity, instance roles, profiles,
or environment variables.
"""

from __future__ import annotations

import importlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow, qualified_metric_name
from nats_sinks.observability.policy import CLOUDWATCH_MAX_DIMENSIONS, ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_CLOUDWATCH_TEXT = "Amazon CloudWatch export disabled by observability policy\n"
EMPTY_CLOUDWATCH_TEXT = "Amazon CloudWatch export produced no allowed metrics\n"
CLOUDWATCH_PROFILE_NAME = "cloudwatch"
CLOUDWATCH_METRIC_NAME_MAX_LENGTH = 255


class CloudWatchClient(Protocol):
    """Small protocol for the boto3 CloudWatch client used by tests and CLI code."""

    def put_metric_data(self, **kwargs: object) -> object:
        """Send one CloudWatch PutMetricData request."""


CloudWatchClientFactory = Callable[[ObservabilityPolicy], CloudWatchClient]


@dataclass(frozen=True, slots=True)
class CloudWatchExportResult:
    """Safe result summary returned by the CloudWatch connector.

    The summary deliberately excludes AWS account IDs, regions, credentials,
    endpoints, and exception messages. It can be logged without disclosing
    cloud tenancy or deployment-specific details.
    """

    attempted: bool
    delivered: bool
    attempts: int
    requests: int
    metrics: int
    message: str


def ensure_cloudwatch_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless CloudWatch export is explicitly enabled."""

    if not policy.enabled or not policy.cloudwatch.enabled:
        raise ConfigurationError("Amazon CloudWatch export is disabled by observability policy")
    if policy.cloudwatch.region is None:
        raise ConfigurationError("cloudwatch.region is required when cloudwatch.enabled is true")


def filter_cloudwatch_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for CloudWatch export by the shared policy model."""

    rows = filter_metric_rows(snapshot, policy)
    if policy.cloudwatch.include_metric_labels_as_dimensions:
        return rows
    return [row for row in rows if not row.labels]


def _cloudwatch_metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the CloudWatch metric name for one approved row."""

    if row.kind == "observation":
        base_name, _separator, stat = row.name.rpartition(".")
        metric_name = f"{qualified_metric_name(base_name, namespace=policy.namespace)}_{stat}"
    else:
        metric_name = qualified_metric_name(row.name, namespace=policy.namespace)
    if len(metric_name) > CLOUDWATCH_METRIC_NAME_MAX_LENGTH:
        raise ConfigurationError("CloudWatch metric name exceeds 255 characters")
    return metric_name


def _cloudwatch_dimensions(row: MetricRow, policy: ObservabilityPolicy) -> list[dict[str, str]]:
    """Return static and optional prepared dimensions for one approved metric row."""

    dimension_pairs = dict(policy.cloudwatch.dimensions)
    if policy.cloudwatch.include_metric_labels_as_dimensions:
        dimension_pairs.update(row.labels)
    if len(dimension_pairs) > CLOUDWATCH_MAX_DIMENSIONS:
        raise ConfigurationError("CloudWatch dimensions exceed the configured safety cap")
    return [
        {"Name": name, "Value": value}
        for name, value in sorted(dimension_pairs.items(), key=lambda item: item[0].lower())
    ]


def _metric_datum(row: MetricRow, policy: ObservabilityPolicy) -> dict[str, object]:
    """Translate one policy-approved metric row into CloudWatch MetricDatum."""

    value = float(row.value)
    if not math.isfinite(value):
        raise ValueError(f"metric row {row.name} contains a non-finite value")
    datum: dict[str, object] = {
        "MetricName": _cloudwatch_metric_name(row, policy),
        "Value": value,
        "Unit": policy.cloudwatch.unit,
        "StorageResolution": policy.cloudwatch.storage_resolution,
    }
    dimensions = _cloudwatch_dimensions(row, policy)
    if dimensions:
        datum["Dimensions"] = dimensions
    return datum


def build_cloudwatch_metric_data(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded CloudWatch `MetricData` entries from an approved snapshot."""

    ensure_cloudwatch_enabled(policy)
    return [_metric_datum(row, policy) for row in filter_cloudwatch_metric_rows(snapshot, policy)]


def _chunks(
    values: Sequence[dict[str, object]],
    *,
    chunk_size: int,
) -> list[list[dict[str, object]]]:
    """Split metric data into fixed-size CloudWatch request chunks."""

    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


def _request_size_bytes(request_body: dict[str, object]) -> int:
    """Return the exact JSON size used for local request-size enforcement."""

    return len(json.dumps(request_body, separators=(",", ":"), sort_keys=True, allow_nan=False))


def build_cloudwatch_put_metric_data_requests(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded `PutMetricData` request dictionaries.

    The request dictionaries intentionally contain only namespace, metric
    values, unit, storage resolution, and approved low-cardinality dimensions.
    No region, account ID, credential material, endpoint, subject, payload, file
    path, table name, classification value, or message ID is included.
    """

    metric_data = build_cloudwatch_metric_data(snapshot, policy)
    requests: list[dict[str, object]] = []
    for metric_chunk in _chunks(
        metric_data,
        chunk_size=policy.cloudwatch.max_metrics_per_request,
    ):
        request_body: dict[str, object] = {
            "Namespace": policy.cloudwatch.metric_namespace,
            "MetricData": metric_chunk,
        }
        if _request_size_bytes(request_body) > policy.cloudwatch.max_request_bytes:
            raise ConfigurationError(
                "CloudWatch PutMetricData request exceeds cloudwatch.max_request_bytes; "
                "reduce the allow list, dimensions, or per-request batch size"
            )
        requests.append(request_body)
    return requests


def render_cloudwatch_put_metric_data_requests_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render the CloudWatch dry-run request list as bounded JSON."""

    requests = build_cloudwatch_put_metric_data_requests(snapshot, policy)
    return json.dumps(requests, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )


def build_boto3_cloudwatch_client(policy: ObservabilityPolicy) -> CloudWatchClient:
    """Create a boto3 CloudWatch client with bounded timeout and retry settings."""

    ensure_cloudwatch_enabled(policy)
    try:
        boto3 = importlib.import_module("boto3")
        botocore_config = importlib.import_module("botocore.config")
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            "boto3 is required for live CloudWatch export; install nats-sinks[cloudwatch]"
        ) from exc

    config_class = botocore_config.Config
    config = config_class(
        connect_timeout=policy.cloudwatch.timeout_seconds,
        read_timeout=policy.cloudwatch.timeout_seconds,
        retries={"max_attempts": 1, "mode": "standard"},
    )
    return boto3.client(  # type: ignore[no-any-return]
        "cloudwatch",
        region_name=policy.cloudwatch.region,
        config=config,
    )


def _safe_failure_message(exc: BaseException) -> str:
    """Return a sanitized failure category for CLI output and logs."""

    return f"Amazon CloudWatch export failed with {type(exc).__name__}"


def export_cloudwatch_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    client: CloudWatchClient | None = None,
    client_factory: CloudWatchClientFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CloudWatchExportResult:
    """Export approved metrics to Amazon CloudWatch with bounded retries."""

    if not policy.enabled or not policy.cloudwatch.enabled:
        return CloudWatchExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            requests=0,
            metrics=0,
            message=DISABLED_CLOUDWATCH_TEXT.strip(),
        )

    rows = filter_cloudwatch_metric_rows(snapshot, policy)
    if not rows:
        return CloudWatchExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            requests=0,
            metrics=0,
            message=EMPTY_CLOUDWATCH_TEXT.strip(),
        )

    requests = build_cloudwatch_put_metric_data_requests(snapshot, policy)
    selected_client = client
    if selected_client is None:
        selected_client = (
            client_factory(policy) if client_factory else build_boto3_cloudwatch_client(policy)
        )
    max_attempts = policy.cloudwatch.max_retries + 1
    last_message = "Amazon CloudWatch export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            for request_body in requests:
                selected_client.put_metric_data(**request_body)
            return CloudWatchExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                requests=len(requests),
                metrics=len(rows),
                message="Amazon CloudWatch export delivered",
            )
        except Exception as exc:
            last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.cloudwatch.retry_backoff_seconds > 0:
            sleep(policy.cloudwatch.retry_backoff_seconds)

    return CloudWatchExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        requests=len(requests),
        metrics=len(rows),
        message=last_message,
    )
