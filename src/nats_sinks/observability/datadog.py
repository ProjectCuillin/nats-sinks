# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Datadog DogStatsD observability connector.

The Datadog connector is intentionally observational. It reads a local
nats-sinks metrics snapshot, applies the shared observability allow and deny
policy, renders bounded DogStatsD datagrams, and sends those datagrams to a
configured local or approved Datadog Agent listener.

The connector never reads NATS messages, destination records, message payloads,
subjects, classification labels, mission metadata, message IDs, file paths, or
table names. It also never participates in JetStream ACK, NAK, DLQ, retry, or
sink write decisions.
"""

from __future__ import annotations

import math
import re
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_DATADOG_TEXT = "Datadog export disabled by observability policy\n"
EMPTY_DATADOG_TEXT = "Datadog export produced no allowed metrics\n"
DATADOG_PROFILE_NAME = "datadog"
DATADOG_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DATADOG_SAFE_TAG_RE = re.compile(r"[^A-Za-z0-9_.:/-]+")


class DatadogSocket(Protocol):
    """Small socket protocol used for deterministic Datadog exporter tests."""

    def settimeout(self, value: float) -> None:
        """Set the socket timeout."""

    def sendto(self, data: bytes, address: object) -> int:
        """Send one DogStatsD datagram."""

    def close(self) -> None:
        """Close the socket."""


SocketFactory = Callable[[int, int], DatadogSocket]


@dataclass(frozen=True, slots=True)
class DatadogExportResult:
    """Safe result summary returned by the Datadog connector.

    The summary deliberately excludes target addresses, socket paths, and tags.
    It can be logged without disclosing deployment-specific observability
    topology or operator-approved tag values.
    """

    attempted: bool
    delivered: bool
    attempts: int
    datagrams: int
    message: str


def ensure_datadog_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless Datadog export is explicitly enabled."""

    if not policy.enabled or not policy.datadog.enabled:
        raise ConfigurationError("Datadog export is disabled by observability policy")
    if policy.datadog.transport == "unixgram" and policy.datadog.socket_path is None:
        raise ConfigurationError(
            "datadog.socket_path is required when datadog.transport is unixgram"
        )


def filter_datadog_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for Datadog export by the shared policy model."""

    return filter_metric_rows(snapshot, policy)


def _format_number(value: float) -> str:
    """Render finite numbers compactly for DogStatsD datagrams."""

    if not math.isfinite(float(value)):
        raise ValueError("Datadog metric values must be finite")
    rendered = float(value)
    if rendered.is_integer():
        return str(int(rendered))
    return f"{rendered:.12g}"


def _normalize_metric_component(value: str) -> str:
    """Normalize a DogStatsD metric-name component while keeping it readable."""

    rendered = DATADOG_SAFE_NAME_RE.sub("_", value.strip())
    rendered = re.sub(r"_+", "_", rendered).strip("._-")
    if not rendered:
        raise ValueError("Datadog metric name normalized to an empty value")
    return rendered


def _metric_prefix(policy: ObservabilityPolicy) -> str:
    """Return the configured Datadog prefix, falling back to the namespace."""

    return _normalize_metric_component(policy.datadog.metric_prefix or policy.namespace)


def datadog_metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the DogStatsD metric name for one approved row."""

    return ".".join([_metric_prefix(policy), _normalize_metric_component(row.name)])


def _dogstatsd_type(row: MetricRow) -> str:
    """Return the DogStatsD metric type for a flattened metric row."""

    _ = row
    # Metrics snapshots contain absolute aggregate values. DogStatsD counters
    # are normally deltas, so exporting snapshot counters as `|c` would
    # over-count when an observability timer runs repeatedly over the same
    # process. Gauges preserve the current snapshot value without local state.
    return "g"


def _normalize_tag_component(value: str) -> str:
    """Normalize a DogStatsD tag component without allowing separators."""

    rendered = DATADOG_SAFE_TAG_RE.sub("_", value.strip())
    rendered = re.sub(r"_+", "_", rendered).strip("._-:/")
    if not rendered:
        raise ValueError("Datadog tag component normalized to an empty value")
    return rendered


def _dogstatsd_tags(row: MetricRow, policy: ObservabilityPolicy) -> list[str]:
    """Return sorted DogStatsD tags allowed by policy."""

    tags = {
        _normalize_tag_component(key): _normalize_tag_component(value)
        for key, value in policy.datadog.tags.items()
    }
    if policy.datadog.include_metric_labels_as_tags:
        for key, value in row.labels.items():
            tags[_normalize_tag_component(key)] = _normalize_tag_component(value)
    return [f"{key}:{value}" for key, value in sorted(tags.items())]


def _dogstatsd_line(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Render one DogStatsD datagram line for an approved metric row."""

    line = f"{datadog_metric_name(row, policy)}:{_format_number(row.value)}|{_dogstatsd_type(row)}"
    tags = _dogstatsd_tags(row, policy)
    if tags:
        return f"{line}|#{','.join(tags)}"
    return line


def render_datadog_lines(snapshot: dict[str, object] | None, policy: ObservabilityPolicy) -> str:
    """Render safe DogStatsD lines for policy-approved metrics.

    Disabled policies return a harmless explanatory line and do not need a
    snapshot. Enabled policies require a validated metrics snapshot. The output
    is designed for dry-run review and deliberately contains no delivery
    subject, payload, classification, labels unless explicitly reviewed,
    mission metadata, destination, or credential details.
    """

    if not policy.enabled or not policy.datadog.enabled:
        return DISABLED_DATADOG_TEXT
    if snapshot is None:
        raise ValueError("an enabled Datadog policy requires a metrics snapshot")

    rows = filter_datadog_metric_rows(snapshot, policy)
    if not rows:
        return EMPTY_DATADOG_TEXT

    return "\n".join(_dogstatsd_line(row, policy) for row in rows) + "\n"


def build_datadog_datagrams(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[bytes]:
    """Build bounded DogStatsD datagrams for the approved metrics."""

    ensure_datadog_enabled(policy)
    rendered = render_datadog_lines(snapshot, policy)
    if rendered == EMPTY_DATADOG_TEXT:
        return []
    datagrams = [line.encode("utf-8") for line in rendered.rstrip("\n").splitlines()]
    for datagram in datagrams:
        if len(datagram) > policy.datadog.max_datagram_bytes:
            raise ConfigurationError(
                "Datadog datagram exceeds datadog.max_datagram_bytes; reduce metric_prefix, "
                "tags, or the allow list, or increase the bound after review"
            )
    return datagrams


def _datadog_address(policy: ObservabilityPolicy) -> object:
    """Return the socket address without exposing it in result messages."""

    if policy.datadog.transport == "unixgram":
        if policy.datadog.socket_path is None:
            raise ConfigurationError(
                "datadog.socket_path is required when datadog.transport is unixgram"
            )
        return policy.datadog.socket_path
    return (policy.datadog.host, policy.datadog.port)


def _socket_family(policy: ObservabilityPolicy) -> int:
    """Return the socket family for the configured Datadog transport."""

    if policy.datadog.transport == "unixgram":
        return socket.AF_UNIX
    return socket.AF_INET


def _safe_failure_message(exc: BaseException) -> str:
    """Return a sanitized failure category for CLI output and logs."""

    return f"Datadog export failed with {type(exc).__name__}"


def _send_datagrams_once(
    datagrams: Sequence[bytes],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory,
) -> None:
    """Send all datagrams once through a short-lived datagram socket."""

    sock = socket_factory(_socket_family(policy), socket.SOCK_DGRAM)
    try:
        sock.settimeout(policy.datadog.timeout_seconds)
        address = _datadog_address(policy)
        for datagram in datagrams:
            sock.sendto(datagram, address)
    finally:
        sock.close()


def export_datadog_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DatadogExportResult:
    """Export approved metrics to Datadog DogStatsD with bounded retries."""

    if not policy.enabled or not policy.datadog.enabled:
        return DatadogExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            datagrams=0,
            message=DISABLED_DATADOG_TEXT.strip(),
        )

    rows = filter_datadog_metric_rows(snapshot, policy)
    if not rows:
        return DatadogExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            datagrams=0,
            message=EMPTY_DATADOG_TEXT.strip(),
        )

    datagrams = build_datadog_datagrams(snapshot, policy)
    selected_socket_factory = (
        socket_factory if socket_factory is not None else cast(SocketFactory, socket.socket)
    )
    max_attempts = policy.datadog.max_retries + 1
    last_message = "Datadog export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            _send_datagrams_once(
                datagrams,
                policy,
                socket_factory=selected_socket_factory,
            )
            return DatadogExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                datagrams=len(datagrams),
                message="Datadog export delivered",
            )
        except (OSError, TimeoutError) as exc:
            last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.datadog.retry_backoff_seconds > 0:
            sleep(policy.datadog.retry_backoff_seconds)

    return DatadogExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        datagrams=len(datagrams),
        message=last_message,
    )
