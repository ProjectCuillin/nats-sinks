# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Splunk HTTP Event Collector observability connector.

This connector exports only policy-approved aggregate metrics from a local
metrics snapshot to Splunk HEC.  It is intentionally an observability-plane
helper: it never connects to NATS, never reads sink payloads or destination
records, and never participates in JetStream ACK, NAK, DLQ, retry, or sink
write decisions.

The payload shape follows Splunk's JSON event endpoint with a metric event:
metadata fields such as `source`, `sourcetype`, and `host` stay bounded and
operator-controlled, while actual metric values are emitted as
`metric_name:<metric>` fields.  HEC tokens are loaded from environment
variables and converted into the required `Authorization: Splunk <token>`
header at runtime.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow, qualified_metric_name
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_SPLUNK_HEC_TEXT = "Splunk HEC export disabled by observability policy\n"
EMPTY_SPLUNK_HEC_TEXT = "Splunk HEC export produced no allowed metrics\n"
SPLUNK_HEC_PROFILE_NAME = "splunk_hec"
HTTP_ERROR_MIN_STATUS = 400


@dataclass(frozen=True, slots=True)
class SplunkHecExportResult:
    """Safe result summary returned by the Splunk HEC connector.

    The summary deliberately excludes endpoint URLs and token values.  It can be
    logged by operators without disclosing deployment-specific HEC details.
    """

    attempted: bool
    delivered: bool
    attempts: int
    status_code: int | None
    message: str


def ensure_splunk_hec_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless the Splunk HEC connector is explicitly enabled."""

    if not policy.enabled or not policy.splunk_hec.enabled:
        raise ConfigurationError("Splunk HEC export is disabled by observability policy")
    if policy.splunk_hec.endpoint is None:
        raise ConfigurationError("Splunk HEC endpoint is required when splunk_hec.enabled is true")
    if policy.splunk_hec.token_env is None:
        raise ConfigurationError("Splunk HEC token_env is required when splunk_hec.enabled is true")


def filter_splunk_hec_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for Splunk HEC export by the shared policy model."""

    return filter_metric_rows(snapshot, policy)


def _snapshot_time(snapshot: dict[str, object]) -> float:
    """Return the snapshot timestamp in Splunk's epoch-seconds format."""

    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float) or not math.isfinite(float(generated)):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be finite numeric")
    rendered = float(generated)
    if rendered < 0:
        raise ValueError("metrics snapshot generated_at_epoch_seconds must not be negative")
    return rendered


def _metric_field_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the Splunk metric field name for one approved row."""

    if row.kind == "observation":
        base_name, _separator, stat = row.name.rpartition(".")
        metric_name = f"{qualified_metric_name(base_name, namespace=policy.namespace)}_{stat}"
    else:
        metric_name = qualified_metric_name(row.name, namespace=policy.namespace)
    if row.labels:
        label_suffix = ".".join(f"{key}.{value}" for key, value in sorted(row.labels.items()))
        metric_name = f"{metric_name}.{label_suffix}"
    return f"metric_name:{metric_name}"


def build_splunk_hec_event(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> dict[str, object]:
    """Build one bounded Splunk HEC metric event.

    The event contains only metric fields approved by the observability policy.
    No subject, payload, message ID, path, table, classification, label, mission
    metadata, endpoint, or token value is included.
    """

    ensure_splunk_hec_enabled(policy)
    fields: dict[str, object] = {
        "nats_sinks_namespace": policy.namespace,
        "nats_sinks_observability_profile": SPLUNK_HEC_PROFILE_NAME,
    }
    for row in filter_splunk_hec_metric_rows(snapshot, policy):
        if not math.isfinite(float(row.value)):
            raise ValueError(f"metric row {row.name} contains a non-finite value")
        fields[_metric_field_name(row, policy)] = row.value

    hec = policy.splunk_hec
    event: dict[str, object] = {
        "time": _snapshot_time(snapshot),
        "host": hec.host,
        "source": hec.source,
        "sourcetype": hec.sourcetype,
        "event": "metric",
        "fields": fields,
    }
    if hec.index is not None:
        event["index"] = hec.index
    return event


def render_splunk_hec_event_json(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> bytes:
    """Render the bounded Splunk HEC JSON event body."""

    event = build_splunk_hec_event(snapshot, policy)
    rendered = json.dumps(event, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )
    if len(rendered) > policy.splunk_hec.max_request_bytes:
        raise ConfigurationError(
            "Splunk HEC request body exceeds splunk_hec.max_request_bytes; reduce the "
            "allow list or increase the configured bound after review"
        )
    return rendered


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


def resolve_splunk_hec_headers(policy: ObservabilityPolicy) -> dict[str, str]:
    """Resolve HEC request headers from environment-backed configuration."""

    ensure_splunk_hec_enabled(policy)
    token_env = policy.splunk_hec.token_env
    if token_env is None:
        raise ConfigurationError("Splunk HEC token_env is required when splunk_hec.enabled is true")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Splunk {_environment_value(token_env, purpose='Splunk HEC token')}",
    }
    for header_name, env_name in policy.splunk_hec.headers_env.items():
        headers[header_name] = _environment_value(
            env_name, purpose=f"Splunk HEC header {header_name}"
        )
    return headers


def _safe_failure_message(exc: BaseException) -> tuple[int | None, str]:
    """Return a sanitized failure category for CLI output and logs."""

    if isinstance(exc, error.HTTPError):
        return exc.code, f"Splunk HEC returned HTTP status {exc.code}"
    return None, f"Splunk HEC export failed with {type(exc).__name__}"


def export_splunk_hec_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SplunkHecExportResult:
    """Export approved metrics to Splunk HEC with bounded retry behavior."""

    if not policy.enabled or not policy.splunk_hec.enabled:
        return SplunkHecExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            status_code=None,
            message=DISABLED_SPLUNK_HEC_TEXT.strip(),
        )

    if not filter_splunk_hec_metric_rows(snapshot, policy):
        return SplunkHecExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            status_code=None,
            message=EMPTY_SPLUNK_HEC_TEXT.strip(),
        )

    endpoint = policy.splunk_hec.endpoint
    if endpoint is None:
        raise ConfigurationError("Splunk HEC endpoint is required when splunk_hec.enabled is true")

    body = render_splunk_hec_event_json(snapshot, policy)
    headers = resolve_splunk_hec_headers(policy)
    selected_opener = opener or request.urlopen  # nosec B310
    max_attempts = policy.splunk_hec.max_retries + 1
    last_status: int | None = None
    last_message = "Splunk HEC export did not run"

    for attempt in range(1, max_attempts + 1):
        req = request.Request(  # noqa: S310 # nosec B310
            endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with selected_opener(req, timeout=policy.splunk_hec.timeout_seconds) as response:
                status_code = int(response.getcode())
                if status_code >= HTTP_ERROR_MIN_STATUS:
                    last_status = status_code
                    last_message = f"Splunk HEC returned HTTP status {status_code}"
                else:
                    return SplunkHecExportResult(
                        attempted=True,
                        delivered=True,
                        attempts=attempt,
                        status_code=status_code,
                        message="Splunk HEC export delivered",
                    )
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            last_status, last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.splunk_hec.retry_backoff_seconds > 0:
            sleep(policy.splunk_hec.retry_backoff_seconds)

    return SplunkHecExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        status_code=last_status,
        message=last_message,
    )
