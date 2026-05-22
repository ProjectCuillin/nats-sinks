# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Optional NATS server monitoring observability connector.

NATS exposes useful operational endpoints such as `/healthz` and `/jsz`.
Those endpoints belong to the monitoring plane, not to the message-delivery
plane.  This module therefore keeps all server-monitoring reads outside the
`JetStreamSinkRunner`: it can be used by the separate `nats-sink-observe` CLI,
but it is never imported or called by the sink worker while deciding ACK, NAK,
retry, dead-letter, or destination-write behavior.

The connector is intentionally conservative.  A policy must explicitly enable
monitoring, name the allowed endpoints, name the allowed JSON fields, and set a
bounded timeout and response-size limit.  Snapshots never store the configured
base URL, credentials, or private topology values that were not selected by the
field allow list.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import validate_metric_namespace
from nats_sinks.observability.policy import ObservabilityPolicy

NATS_MONITORING_SNAPSHOT_SCHEMA = "nats_sinks.observability.nats_monitoring.snapshot.v1"
MAX_NATS_MONITORING_SNAPSHOT_BYTES = 5_242_880
DISABLED_NATS_MONITORING_PROMETHEUS_TEXT = (
    "# nats-sinks NATS server monitoring export disabled by observability policy\n"
)
EMPTY_NATS_MONITORING_PROMETHEUS_TEXT = (
    "# nats-sinks NATS server monitoring export produced no numeric values\n"
)

JsonScalar = str | int | float | bool | None
FetchFunction = Callable[[str, float, int, ssl.SSLContext | None], tuple[int, bytes]]


class _NonStandardJsonConstantError(ValueError):
    """Raised when monitoring JSON uses Python-only constants."""


class NatsMonitoringError(RuntimeError):
    """Raised when an approved NATS monitoring endpoint cannot be collected."""


@dataclass(frozen=True)
class NatsMonitoringEndpointObservation:
    """Safe values extracted from one approved NATS monitoring endpoint."""

    endpoint: str
    status_code: int
    fields: Mapping[str, JsonScalar]


def ensure_nats_monitoring_enabled(policy: ObservabilityPolicy) -> None:
    """Validate that NATS server monitoring collection is explicitly enabled."""

    monitoring = policy.nats_server_monitoring
    if not policy.enabled or not monitoring.enabled:
        raise ConfigurationError("NATS server monitoring is disabled by observability policy")
    if monitoring.base_url is None:
        raise ConfigurationError("nats_server_monitoring.base_url is required when enabled")
    if not monitoring.allowed_endpoints:
        raise ConfigurationError(
            "nats_server_monitoring.allowed_endpoints must include at least one endpoint"
        )
    if not monitoring.allowed_fields:
        raise ConfigurationError(
            "nats_server_monitoring.allowed_fields must include at least one JSON field path"
        )


def build_nats_monitoring_url(policy: ObservabilityPolicy, endpoint: str) -> str:
    """Build a monitoring URL from validated policy values.

    The policy validator requires `base_url` to contain only scheme and host,
    while endpoint validation allows only known NATS monitoring paths.  Joining
    them here avoids ad hoc string handling in CLI code and tests.
    """

    base_url = policy.nats_server_monitoring.base_url
    if base_url is None:
        raise ConfigurationError("nats_server_monitoring.base_url is required")
    return f"{base_url}{endpoint}"


def _ssl_context(policy: ObservabilityPolicy) -> ssl.SSLContext | None:
    """Return the TLS context for HTTPS monitoring requests.

    Certificate verification is enabled by default.  The explicit
    `verify_tls=false` escape hatch exists for isolated local labs but should
    not be used for production or mission environments.
    """

    base_url = policy.nats_server_monitoring.base_url
    if base_url is None or not base_url.startswith("https://"):
        return None
    if policy.nats_server_monitoring.verify_tls:
        return ssl.create_default_context(cafile=policy.nats_server_monitoring.ca_file)
    # Explicit policy escape hatch for isolated local labs only.  The policy
    # defaults to certificate verification and documentation tells operators to
    # keep verification enabled for production and mission environments.
    return ssl._create_unverified_context()  # noqa: S323 # nosec B323


def _fetch_bytes(
    url: str,
    timeout_seconds: float,
    max_response_bytes: int,
    context: ssl.SSLContext | None,
) -> tuple[int, bytes]:
    """Fetch one endpoint with a bounded read and no credential-bearing headers."""

    request = Request(url, headers={"Accept": "application/json"}, method="GET")  # noqa: S310
    try:
        # The URL is built from a validated policy base URL and a strict
        # allow-listed endpoint path.  `file:` and custom schemes are rejected
        # before this function is called.
        with urlopen(  # noqa: S310 # nosec B310
            request,
            timeout=timeout_seconds,
            context=context,
        ) as response:
            payload = response.read(max_response_bytes + 1)
            if len(payload) > max_response_bytes:
                raise NatsMonitoringError(
                    "NATS monitoring response exceeds configured maximum size"
                )
            return int(response.status), payload
    except HTTPError as exc:
        raise NatsMonitoringError(
            f"NATS monitoring endpoint returned HTTP status {exc.code}"
        ) from exc
    except URLError as exc:
        raise NatsMonitoringError("NATS monitoring endpoint could not be reached") from exc
    except TimeoutError as exc:
        raise NatsMonitoringError("NATS monitoring endpoint timed out") from exc


def _json_loads_endpoint(payload: bytes, *, endpoint: str) -> dict[str, Any]:
    """Parse an endpoint response as JSON while keeping error messages small."""

    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NatsMonitoringError(
            f"NATS monitoring endpoint {endpoint} did not return UTF-8 JSON"
        ) from exc
    try:
        document = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise NatsMonitoringError(
            f"NATS monitoring endpoint {endpoint} did not return valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise NatsMonitoringError(
            f"NATS monitoring endpoint {endpoint} returned a JSON value that is not an object"
        )
    return document


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python JSON extensions in monitoring endpoint responses."""

    raise _NonStandardJsonConstantError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous NATS monitoring JSON objects."""

    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = item
    return result


def _field_value(document: Mapping[str, Any], field_path: str) -> JsonScalar:
    """Extract one dotted field path from a JSON object.

    Missing fields and non-scalar values are represented as `None`.  The
    connector does not guess, flatten arbitrary objects, or export whole nested
    structures because monitoring payloads can contain operational topology.
    """

    current: Any = document
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    if current is None or isinstance(current, str | int | float | bool):
        return current
    return None


def extract_nats_monitoring_fields(
    document: Mapping[str, Any],
    allowed_fields: list[str],
) -> dict[str, JsonScalar]:
    """Extract only policy-approved scalar field values from an endpoint body."""

    return {field: _field_value(document, field) for field in allowed_fields}


def collect_nats_monitoring_snapshot(
    policy: ObservabilityPolicy,
    *,
    fetch: FetchFunction | None = None,
) -> dict[str, object]:
    """Collect a sanitized NATS server monitoring snapshot.

    The snapshot intentionally contains endpoint paths but not the configured
    base URL.  Endpoint paths are already explicit policy decisions, while the
    base URL may reveal deployment location and should stay in local
    configuration.
    """

    ensure_nats_monitoring_enabled(policy)
    monitoring = policy.nats_server_monitoring
    fetcher = fetch or _fetch_bytes
    context = _ssl_context(policy)
    endpoints: list[dict[str, object]] = []

    for endpoint in monitoring.allowed_endpoints:
        url = build_nats_monitoring_url(policy, endpoint)
        status_code, payload = fetcher(
            url,
            monitoring.timeout_seconds,
            monitoring.max_response_bytes,
            context,
        )
        document = _json_loads_endpoint(payload, endpoint=endpoint)
        endpoints.append(
            {
                "endpoint": endpoint,
                "status_code": status_code,
                "fields": extract_nats_monitoring_fields(document, monitoring.allowed_fields),
            }
        )

    return {
        "schema": NATS_MONITORING_SNAPSHOT_SCHEMA,
        "generated_at_epoch_seconds": time.time(),
        "endpoints": endpoints,
    }


def write_nats_monitoring_snapshot(
    snapshot: dict[str, object],
    path: str | os.PathLike[str],
) -> None:
    """Write a NATS monitoring snapshot atomically with restrictive mode."""

    destination = Path(path).expanduser()
    if destination.name in {"", ".", ".."}:
        raise ValueError("NATS monitoring snapshot path must name a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n"
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
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, destination)
    finally:
        if temp_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)


def load_nats_monitoring_snapshot(path: str | os.PathLike[str]) -> dict[str, object]:
    """Load and validate a NATS monitoring snapshot from disk."""

    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read NATS monitoring snapshot {source}") from exc
    if len(payload) > MAX_NATS_MONITORING_SNAPSHOT_BYTES:
        raise ValueError(
            f"NATS monitoring snapshot {source} is too large; maximum is "
            f"{MAX_NATS_MONITORING_SNAPSHOT_BYTES} bytes"
        )
    try:
        raw = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"NATS monitoring snapshot {source} must be UTF-8") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"NATS monitoring snapshot {source} is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("NATS monitoring snapshot root must be a JSON object")
    if raw.get("schema") != NATS_MONITORING_SNAPSHOT_SCHEMA:
        raise ValueError(
            f"NATS monitoring snapshot schema must be {NATS_MONITORING_SNAPSHOT_SCHEMA!r}"
        )
    endpoints = raw.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("NATS monitoring snapshot endpoints section must be a list")
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("NATS monitoring snapshot endpoint entries must be objects")
        if not isinstance(endpoint.get("endpoint"), str):
            raise ValueError("NATS monitoring snapshot endpoint path must be text")
        if not isinstance(endpoint.get("fields"), dict):
            raise ValueError("NATS monitoring snapshot endpoint fields must be an object")
    return raw


def _safe_metric_token(value: str) -> str:
    """Convert an endpoint or field path into a Prometheus metric-name token."""

    rendered = []
    previous_was_separator = False
    for character in value.lower():
        if character.isalnum():
            rendered.append(character)
            previous_was_separator = False
            continue
        if not previous_was_separator:
            rendered.append("_")
            previous_was_separator = True
    token = "".join(rendered).strip("_")
    return token or "value"


def _numeric_prometheus_value(value: object) -> float | None:
    """Return a Prometheus-safe number for scalar monitoring values."""

    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
    return None


def _format_number(value: float) -> str:
    """Render numeric monitoring values compactly for Prometheus text."""

    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def render_nats_monitoring_prometheus(
    snapshot: dict[str, object] | None,
    policy: ObservabilityPolicy,
) -> str:
    """Render selected NATS monitoring values as Prometheus exposition text."""

    monitoring = policy.nats_server_monitoring
    if not policy.enabled or not monitoring.enabled or not monitoring.prometheus_enabled:
        return DISABLED_NATS_MONITORING_PROMETHEUS_TEXT
    if snapshot is None:
        raise ValueError("an enabled NATS monitoring Prometheus export requires a snapshot")

    namespace = validate_metric_namespace(policy.namespace)
    lines: list[str] = []
    emitted: set[str] = set()
    endpoints = snapshot.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("NATS monitoring snapshot endpoints section must be a list")

    for endpoint_entry in endpoints:
        if not isinstance(endpoint_entry, dict):
            continue
        endpoint = endpoint_entry.get("endpoint")
        fields = endpoint_entry.get("fields")
        if not isinstance(endpoint, str) or not isinstance(fields, dict):
            continue
        endpoint_token = _safe_metric_token(endpoint)
        for field_name in monitoring.allowed_fields:
            value = _numeric_prometheus_value(fields.get(field_name))
            if value is None:
                continue
            field_token = _safe_metric_token(field_name)
            metric_name = f"{namespace}_nats_monitoring_{endpoint_token}_{field_token}"
            if metric_name not in emitted:
                if monitoring.include_help:
                    lines.append(
                        f"# HELP {metric_name} NATS server monitoring value for "
                        f"{endpoint} field {field_name}"
                    )
                if monitoring.include_type:
                    lines.append(f"# TYPE {metric_name} gauge")
                emitted.add(metric_name)
            lines.append(f"{metric_name} {_format_number(value)}")

    if not lines:
        return EMPTY_NATS_MONITORING_PROMETHEUS_TEXT
    return "\n".join(lines) + "\n"
