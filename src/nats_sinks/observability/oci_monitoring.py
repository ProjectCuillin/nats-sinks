# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle Cloud Infrastructure Monitoring observability connector.

This connector exports policy-approved aggregate metrics from a local
`nats-sinks` metrics snapshot to Oracle Cloud Infrastructure Monitoring custom
metrics. It belongs to the observability plane: it never connects to NATS,
never reads sink payloads or destination records, and never participates in
JetStream ACK, NAK, DLQ, retry, fan-out, idempotency, or sink write decisions.

The OCI Python SDK is optional. Tests and dry-runs build a sanitized
`PostMetricData` preview without importing the SDK, while production
deployments install the `oci` extra and authenticate through standard OCI SDK
patterns such as instance principals, resource principals, or protected config
files.
"""

from __future__ import annotations

import importlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow, qualified_metric_name
from nats_sinks.observability.policy import (
    OCI_MONITORING_MAX_DIMENSIONS,
    OCI_MONITORING_NAME_MAX_LENGTH,
    ObservabilityPolicy,
)
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_OCI_MONITORING_TEXT = "OCI Monitoring export disabled by observability policy\n"
EMPTY_OCI_MONITORING_TEXT = "OCI Monitoring export produced no allowed metrics\n"
OCI_MONITORING_PROFILE_NAME = "oci_monitoring"
REDACTED_OCI_VALUE = "<redacted>"


class OciMonitoringClient(Protocol):
    """Small protocol for the OCI Monitoring client used by tests and CLI code."""

    def post_metric_data(self, post_metric_data_details: object, **kwargs: object) -> object:
        """Send one OCI Monitoring PostMetricData request."""


OciMonitoringClientFactory = Callable[[ObservabilityPolicy], OciMonitoringClient]
OciMonitoringRequestModelFactory = Callable[[dict[str, object]], object]


@dataclass(frozen=True, slots=True)
class OciMonitoringExportResult:
    """Safe result summary returned by the OCI Monitoring connector.

    The summary deliberately excludes tenancy OCIDs, compartment OCIDs, regions,
    signer details, endpoint values, and exception messages. It can be logged
    without disclosing cloud tenancy or deployment-specific details.
    """

    attempted: bool
    delivered: bool
    attempts: int
    requests: int
    metrics: int
    message: str


class OciMonitoringRejectedMetricError(RuntimeError):
    """Raised when OCI accepts a request but reports rejected metric objects."""


def ensure_oci_monitoring_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless OCI Monitoring export is explicitly enabled."""

    if not policy.enabled or not policy.oci_monitoring.enabled:
        raise ConfigurationError("OCI Monitoring export is disabled by observability policy")
    if policy.oci_monitoring.region is None:
        raise ConfigurationError("oci_monitoring.region is required when OCI Monitoring is enabled")
    if policy.oci_monitoring.compartment_id is None:
        raise ConfigurationError(
            "oci_monitoring.compartment_id is required when OCI Monitoring is enabled"
        )


def filter_oci_monitoring_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for OCI Monitoring export by the shared policy model."""

    rows = filter_metric_rows(snapshot, policy)
    if policy.oci_monitoring.include_metric_labels_as_dimensions:
        return rows
    return [row for row in rows if not row.labels]


def _snapshot_timestamp(snapshot: dict[str, object]) -> str:
    """Return the snapshot timestamp in RFC3339 form for OCI datapoints."""

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


def _oci_metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the OCI Monitoring metric name for one approved row."""

    if row.kind == "observation":
        base_name, _separator, stat = row.name.rpartition(".")
        metric_name = f"{qualified_metric_name(base_name, namespace=policy.namespace)}_{stat}"
    else:
        metric_name = qualified_metric_name(row.name, namespace=policy.namespace)
    if len(metric_name) > OCI_MONITORING_NAME_MAX_LENGTH:
        raise ConfigurationError("OCI Monitoring metric name exceeds 255 characters")
    if not metric_name[0].isalpha():
        raise ConfigurationError("OCI Monitoring metric names must start with a letter")
    return metric_name


def _oci_dimensions(row: MetricRow, policy: ObservabilityPolicy) -> dict[str, str]:
    """Return static and optional prepared dimensions for one approved metric row."""

    dimension_pairs = dict(policy.oci_monitoring.dimensions)
    if policy.oci_monitoring.include_metric_labels_as_dimensions:
        dimension_pairs.update(row.labels)
    if len(dimension_pairs) > OCI_MONITORING_MAX_DIMENSIONS:
        raise ConfigurationError("OCI Monitoring dimensions exceed the configured safety cap")
    return dict(sorted(dimension_pairs.items(), key=lambda item: item[0].lower()))


def _metric_data_details(
    row: MetricRow,
    policy: ObservabilityPolicy,
    *,
    timestamp: str,
) -> dict[str, object]:
    """Translate one policy-approved metric row into OCI MetricDataDetails."""

    ensure_oci_monitoring_enabled(policy)
    value = float(row.value)
    if not math.isfinite(value):
        raise ValueError(f"metric row {row.name} contains a non-finite value")
    compartment_id = policy.oci_monitoring.compartment_id
    if compartment_id is None:
        raise ConfigurationError("oci_monitoring.compartment_id is required")
    metric: dict[str, object] = {
        "namespace": policy.oci_monitoring.metric_namespace,
        "compartment_id": compartment_id,
        "name": _oci_metric_name(row, policy),
        "dimensions": _oci_dimensions(row, policy),
        "datapoints": [{"timestamp": timestamp, "value": value, "count": 1}],
    }
    if policy.oci_monitoring.resource_group is not None:
        metric["resource_group"] = policy.oci_monitoring.resource_group
    if policy.oci_monitoring.metadata:
        metric["metadata"] = dict(policy.oci_monitoring.metadata)
    return metric


def build_oci_monitoring_metric_data(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded OCI Monitoring metric-data entries from an approved snapshot."""

    ensure_oci_monitoring_enabled(policy)
    timestamp = _snapshot_timestamp(snapshot)
    return [
        _metric_data_details(row, policy, timestamp=timestamp)
        for row in filter_oci_monitoring_metric_rows(snapshot, policy)
    ]


def _chunks(
    values: Sequence[dict[str, object]],
    *,
    chunk_size: int,
) -> list[list[dict[str, object]]]:
    """Split metric data into fixed-size OCI request chunks."""

    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


def _request_size_bytes(request_body: dict[str, object]) -> int:
    """Return the exact JSON size used for local request-size enforcement."""

    return len(json.dumps(request_body, separators=(",", ":"), sort_keys=True, allow_nan=False))


def build_oci_monitoring_post_metric_data_requests(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[dict[str, object]]:
    """Build bounded OCI Monitoring `PostMetricDataDetails` request dictionaries.

    The request dictionaries intentionally contain only namespace, compartment
    OCID, metric names, datapoints, optional resource group, static metadata,
    and approved low-cardinality dimensions. Dry-run rendering redacts the
    compartment OCID before writing JSON for review.
    """

    metric_data = build_oci_monitoring_metric_data(snapshot, policy)
    requests: list[dict[str, object]] = []
    for metric_chunk in _chunks(
        metric_data,
        chunk_size=policy.oci_monitoring.max_metrics_per_request,
    ):
        request_body: dict[str, object] = {
            "batch_atomicity": policy.oci_monitoring.batch_atomicity,
            "metric_data": metric_chunk,
        }
        if _request_size_bytes(request_body) > policy.oci_monitoring.max_request_bytes:
            raise ConfigurationError(
                "OCI Monitoring PostMetricData request exceeds oci_monitoring.max_request_bytes; "
                "reduce the allow list, dimensions, metadata, or per-request batch size"
            )
        requests.append(request_body)
    return requests


def _sanitized_request(request_body: dict[str, object]) -> dict[str, object]:
    """Return a dry-run safe OCI request preview."""

    sanitized = json.loads(
        json.dumps(request_body, separators=(",", ":"), sort_keys=True, allow_nan=False)
    )
    for metric in sanitized.get("metric_data", []):
        if isinstance(metric, dict) and "compartment_id" in metric:
            metric["compartment_id"] = REDACTED_OCI_VALUE
    return cast(dict[str, object], sanitized)


def render_oci_monitoring_post_metric_data_requests_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render a sanitized OCI Monitoring dry-run request list as bounded JSON."""

    requests = [
        _sanitized_request(request)
        for request in build_oci_monitoring_post_metric_data_requests(snapshot, policy)
    ]
    return json.dumps(requests, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )


def _oci_module() -> Any:
    """Import the optional OCI SDK or raise a clear configuration error."""

    try:
        return importlib.import_module("oci")
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            "oci is required for live OCI Monitoring export; install nats-sinks[oci]"
        ) from exc


def build_oci_monitoring_client(policy: ObservabilityPolicy) -> OciMonitoringClient:
    """Create an OCI Monitoring client with bounded timeout and no SDK retries."""

    ensure_oci_monitoring_enabled(policy)
    oci = _oci_module()
    timeout = (policy.oci_monitoring.timeout_seconds, policy.oci_monitoring.timeout_seconds)
    retry_strategy = oci.retry.NoneRetryStrategy()
    if policy.oci_monitoring.auth_mode == "instance_principal":
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        config = {"region": policy.oci_monitoring.region}
        return cast(
            OciMonitoringClient,
            oci.monitoring.MonitoringClient(
                config,
                signer=signer,
                timeout=timeout,
                retry_strategy=retry_strategy,
            ),
        )
    if policy.oci_monitoring.auth_mode == "resource_principal":
        signer = oci.auth.signers.get_resource_principals_signer()
        config = {"region": policy.oci_monitoring.region}
        return cast(
            OciMonitoringClient,
            oci.monitoring.MonitoringClient(
                config,
                signer=signer,
                timeout=timeout,
                retry_strategy=retry_strategy,
            ),
        )
    if policy.oci_monitoring.config_file is None:
        raise ConfigurationError(
            "oci_monitoring.config_file is required when auth_mode is config_file"
        )
    config = oci.config.from_file(
        file_location=policy.oci_monitoring.config_file,
        profile_name=policy.oci_monitoring.profile,
    )
    return cast(
        OciMonitoringClient,
        oci.monitoring.MonitoringClient(
            config,
            timeout=timeout,
            retry_strategy=retry_strategy,
        ),
    )


def build_oci_monitoring_post_metric_data_details_model(
    request_body: dict[str, object],
) -> object:
    """Build OCI SDK model objects from an internal request dictionary."""

    oci = _oci_module()
    models = oci.monitoring.models
    metric_models = []
    for metric in cast(list[dict[str, object]], request_body["metric_data"]):
        datapoints = []
        for point in cast(list[dict[str, object]], metric["datapoints"]):
            timestamp = str(point["timestamp"]).replace("Z", "+00:00")
            value = cast(float, point["value"])
            count = cast(int, point["count"])
            datapoints.append(
                models.Datapoint(
                    timestamp=datetime.fromisoformat(timestamp),
                    value=value,
                    count=count,
                )
            )
        metric_models.append(
            models.MetricDataDetails(
                namespace=str(metric["namespace"]),
                compartment_id=str(metric["compartment_id"]),
                name=str(metric["name"]),
                dimensions=cast(dict[str, str], metric["dimensions"]),
                metadata=cast(dict[str, str], metric.get("metadata", {})),
                datapoints=datapoints,
                resource_group=cast(str | None, metric.get("resource_group")),
            )
        )
    return models.PostMetricDataDetails(
        batch_atomicity=str(request_body["batch_atomicity"]),
        metric_data=metric_models,
    )


def _response_has_metric_failures(response: object) -> bool:
    """Return true when the OCI response reports rejected metric objects."""

    data = getattr(response, "data", None)
    if isinstance(response, dict):
        data = response.get("data")
    failed_metrics = None
    if isinstance(data, dict):
        failed_metrics = data.get("failed_metrics")
    elif data is not None:
        failed_metrics = getattr(data, "failed_metrics", None)
    return isinstance(failed_metrics, list) and bool(failed_metrics)


def _safe_failure_message(exc: BaseException) -> str:
    """Return a sanitized failure category for CLI output and logs."""

    return f"OCI Monitoring export failed with {type(exc).__name__}"


def export_oci_monitoring_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    client: OciMonitoringClient | None = None,
    client_factory: OciMonitoringClientFactory | None = None,
    request_model_factory: OciMonitoringRequestModelFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> OciMonitoringExportResult:
    """Export approved metrics to OCI Monitoring with bounded retries."""

    if not policy.enabled or not policy.oci_monitoring.enabled:
        return OciMonitoringExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            requests=0,
            metrics=0,
            message=DISABLED_OCI_MONITORING_TEXT.strip(),
        )

    rows = filter_oci_monitoring_metric_rows(snapshot, policy)
    if not rows:
        return OciMonitoringExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            requests=0,
            metrics=0,
            message=EMPTY_OCI_MONITORING_TEXT.strip(),
        )

    requests = build_oci_monitoring_post_metric_data_requests(snapshot, policy)
    selected_client = client
    if selected_client is None:
        selected_client = (
            client_factory(policy) if client_factory else build_oci_monitoring_client(policy)
        )
    selected_model_factory = (
        request_model_factory or build_oci_monitoring_post_metric_data_details_model
    )
    max_attempts = policy.oci_monitoring.max_retries + 1
    last_message = "OCI Monitoring export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            for request_body in requests:
                response = selected_client.post_metric_data(
                    selected_model_factory(request_body),
                )
                if _response_has_metric_failures(response):
                    raise OciMonitoringRejectedMetricError("OCI Monitoring rejected metric data")
            return OciMonitoringExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                requests=len(requests),
                metrics=len(rows),
                message="OCI Monitoring export delivered",
            )
        except Exception as exc:
            last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.oci_monitoring.retry_backoff_seconds > 0:
            sleep(policy.oci_monitoring.retry_backoff_seconds)

    return OciMonitoringExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        requests=len(requests),
        metrics=len(rows),
        message=last_message,
    )
